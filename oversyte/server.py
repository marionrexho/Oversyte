"""FastAPI app: dashboard API, media, live view, push subscriptions."""
from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import secrets
import shutil
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import camera_setup, evaluation
from .engine import Engine
from .push import Pusher
from .storage import STATUSES, Store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_SECRET = b""
MIN_PASSWORD = 8
DEFAULT_PASSWORD = "change-me"  # the shipped placeholder; never accepted as a real password
log = logging.getLogger("oversyte.server")


def signed_thumb_url(name: str) -> str:
    sig = hmac.new(_SECRET, name.encode(), hashlib.sha256).hexdigest()[:32]
    return f"/media/thumbs/{name}?sig={sig}"


def _check_sig(name: str, sig: str | None) -> bool:
    if not sig:
        return False
    good = hmac.new(_SECRET, name.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(good, sig)


def _safe_name(name: str) -> str:
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(404)
    return name


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, hash_hex = stored.split("$", 2)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return hmac.compare_digest(digest.hex(), hash_hex)


def _public_user(user: dict | None) -> dict | None:
    if not user:
        return None
    return {"id": user["id"], "name": user["name"], "email": user["email"]}


class LoginBody(BaseModel):
    email: str
    password: str


class SignupBody(BaseModel):
    name: str
    email: str
    password: str


class ReviewBody(BaseModel):
    status: str | None = None
    notes: str | None = None


class SubBody(BaseModel):
    subscription: dict
    label: str = ""


class UnsubBody(BaseModel):
    endpoint: str


class ZonesBody(BaseModel):
    shelf: list[list[list[float]]] = []
    ignore: list[list[list[float]]] = []


class TestBody(BaseModel):
    camera_id: str | None = None


class CameraBody(BaseModel):
    """What the Add camera wizard sends. `url` is only used for brand "other"."""
    brand: str
    name: str = ""
    host: str = ""
    port: int | None = None
    username: str = ""
    password: str = ""
    channel: int = 1
    substream: bool = True
    url: str = ""

    def stream_url(self) -> str:
        try:
            return camera_setup.build_url(self.brand, self.host, self.port, self.username,
                                          self.password, self.channel, self.substream, self.url)
        except camera_setup.SetupError as e:
            raise HTTPException(400, str(e))


def create_app(cfg: dict) -> FastAPI:
    global _SECRET
    data_dir: Path = cfg["data_dir"]
    data_dir.mkdir(parents=True, exist_ok=True)
    secret_file = data_dir / "secret.key"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_hex(32))
    _SECRET = secret_file.read_text().strip().encode()

    store = Store(data_dir / "oversyte.db")
    pusher = Pusher(data_dir, store, cfg["push_contact"])
    engine = Engine(cfg, store, pusher)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not store.has_users():
            log.warning("No accounts yet: the first person to open the dashboard creates the owner "
                        "account. Do that before exposing this server to the internet.")
        engine.start()
        yield
        engine.stop()

    app = FastAPI(title="Oversyte", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(SessionMiddleware, secret_key=_SECRET.decode(), max_age=60 * 60 * 24 * 60,
                       same_site="lax", session_cookie="oversyte")
    app.state.engine, app.state.store = engine, store

    @app.middleware("http")
    async def revalidate_dashboard_files(request: Request, call_next):
        """Make browsers re-check the dashboard's own files on every load, so an update
        shows up after a normal refresh instead of the cached old version."""
        response = await call_next(request)
        path = request.url.path
        if not path.startswith(("/api/", "/media/")) and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-cache"
        return response

    def auth(request: Request):
        if not request.session.get("ok"):
            raise HTTPException(401, "Sign in required")
        uid = request.session.get("user_id")
        if uid is not None and store.get_user(int(uid)) is None:  # account was removed
            request.session.clear()
            raise HTTPException(401, "Sign in required")

    def worker(cam_id: str):
        w = engine.workers.get(cam_id)
        if not w:
            raise HTTPException(404, "Unknown camera")
        return w

    def public_event(ev: dict) -> dict:
        ev = dict(ev)
        ev["clip_url"] = f"/media/clips/{ev['clip']}" if ev.get("clip") else None
        ev["thumb_url"] = signed_thumb_url(ev["thumb"]) if ev.get("thumb") else None
        return ev

    # ---------- session ----------
    @app.get("/api/me")
    def me(request: Request):
        user = None
        if request.session.get("user_id"):
            user = store.get_user(int(request.session["user_id"]))
        elif request.session.get("legacy_ok"):
            user = {"id": 0, "name": request.session.get("user_name", "Dashboard owner"),
                    "email": request.session.get("user_email", "")}
        return {"authed": bool(request.session.get("ok")), "store_name": cfg["store_name"],
                "cameras": len(engine.workers),  # only cameras that are switched on
                "team_mode": bool(cfg.get("team_mode", True)),
                # Signup only creates the store's first (owner) account. After that, people are
                # added by someone already signed in (Settings -> Team).
                "signup_open": not store.has_users(),
                "user": _public_user(user)}

    def _new_user(body: SignupBody) -> dict:
        name, email, password = body.name.strip(), _norm_email(body.email), body.password
        if len(name) < 2:
            raise HTTPException(400, "Enter a name")
        if "@" not in email or len(email) < 5:
            raise HTTPException(400, "Enter a valid email")
        if len(password) < MIN_PASSWORD:
            raise HTTPException(400, f"Use a password of at least {MIN_PASSWORD} characters")
        if store.get_user_by_email(email):
            raise HTTPException(409, "An account already exists for that email")
        return store.add_user(name, email, _hash_password(password))

    @app.post("/api/signup")
    async def signup(body: SignupBody, request: Request):
        if store.has_users():
            raise HTTPException(403, "This store already has an owner account. "
                                     "Ask them to add you in Settings → Team.")
        user = _new_user(body)
        request.session.clear()
        request.session["ok"] = True
        request.session["user_id"] = user["id"]
        request.session["user_name"] = user["name"]
        request.session["user_email"] = user["email"]
        return {"ok": True, "user": _public_user(user)}

    @app.post("/api/login")
    async def login(body: LoginBody, request: Request):
        email = _norm_email(body.email) if body.email else ""
        password = body.password
        user = store.get_user_by_email(email) if email else None
        if user and _verify_password(password, user["password_hash"]):
            request.session.clear()
            request.session["ok"] = True
            request.session["user_id"] = user["id"]
            request.session["user_name"] = user["name"]
            request.session["user_email"] = user["email"]
            return {"ok": True, "user": _public_user(user)}
        # The shared store password from config.yaml, kept for existing installs. Never the
        # shipped default: an install nobody configured must not open with "change-me".
        legacy = str(cfg.get("dashboard_password") or "")
        if (not user and legacy and legacy != DEFAULT_PASSWORD
                and hmac.compare_digest(password.encode(), legacy.encode())):
            request.session.clear()
            request.session["ok"] = True
            request.session["legacy_ok"] = True
            request.session["user_name"] = cfg["store_name"]
            request.session["user_email"] = ""
            return {"ok": True, "legacy": True}
        # Same pause for every failure, so guessing is slow and the timing doesn't reveal
        # which emails have accounts.
        await asyncio.sleep(1.0)
        raise HTTPException(401, "Wrong email or password")

    @app.post("/api/logout")
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    # ---------- team (accounts) ----------
    @app.get("/api/users", dependencies=[Depends(auth)])
    def users():
        return [_public_user(u) | {"created": u["created"]} for u in store.list_users()]

    @app.post("/api/users", dependencies=[Depends(auth)])
    def add_user(body: SignupBody):
        return _public_user(_new_user(body))

    @app.delete("/api/users/{user_id}", dependencies=[Depends(auth)])
    def remove_user(user_id: int, request: Request):
        if request.session.get("user_id") == user_id:
            raise HTTPException(400, "You can't remove your own account")
        if len(store.list_users()) <= 1:  # zero accounts would reopen signup to anyone
            raise HTTPException(400, "The store needs at least one account")
        if not store.delete_user(user_id):
            raise HTTPException(404, "Unknown account")
        return {"ok": True}

    # ---------- incidents ----------
    @app.get("/api/stats", dependencies=[Depends(auth)])
    def stats():
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return store.stats(today.timestamp(), (today - timedelta(days=6)).timestamp())

    @app.get("/api/overview", dependencies=[Depends(auth)])
    def overview():
        """Everything the overview page needs in one call."""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        res = lab_results()
        s = res.get("summary") or {}
        vids = evaluation.list_videos(eval_dir) if eval_dir.exists() else []
        labeled = sum(evaluation.load_labels(eval_dir, v.name)["done"] for v in vids)
        zones = sum(len(engine.zones.get(cid)["shelf"]) for cid in engine.workers)
        return {
            "stats": store.stats(today.timestamp(), (today - timedelta(days=6)).timestamp()),
            "daily": store.daily(14),
            "cameras": {"total": len(engine.workers),
                        "online": sum(w.camera.connected for w in engine.workers.values()),
                        "ai_ready": sum(w.ai_ready for w in engine.workers.values())},
            "devices": len(store.subs()),
            "zones": zones,
            "has_test_alert": store.has_test_alert(),
            "accuracy": {"catch_rate": s.get("catch_rate"), "false_alarms_per_hour": s.get("false_alarms_per_hour"),
                         "videos": len(vids), "labeled": labeled, "run_at": res.get("run_at")},
        }

    @app.get("/api/events", dependencies=[Depends(auth)])
    def events(status: str | None = None, before: float | None = None, limit: int = 30):
        if status and status not in STATUSES:
            raise HTTPException(400, "Bad status")
        return [public_event(e) for e in store.list_events(status, before, min(limit, 200))]

    @app.get("/api/events/{eid}", dependencies=[Depends(auth)])
    def event(eid: str):
        ev = store.get_event(eid)
        if not ev:
            raise HTTPException(404)
        return public_event(ev)

    @app.patch("/api/events/{eid}", dependencies=[Depends(auth)])
    def review(eid: str, body: ReviewBody):
        if not store.get_event(eid):
            raise HTTPException(404)
        try:
            store.review(eid, body.status, body.notes)
        except ValueError:
            raise HTTPException(400, "Bad status")
        return public_event(store.get_event(eid))

    @app.delete("/api/events/{eid}", dependencies=[Depends(auth)])
    def delete(eid: str):
        ev = store.get_event(eid)
        if ev:
            engine.delete_files(ev)
            store.delete_event(eid)
        return {"ok": True}

    @app.post("/api/test-alert", dependencies=[Depends(auth)])
    def test_alert(body: TestBody):
        ev = engine.test_alert(body.camera_id)
        return public_event(ev)

    @app.get("/api/export.zip", dependencies=[Depends(auth)])
    def export():
        """Reviewed clips + labels, for tuning or training a better model later."""
        rows = [e for e in store.list_events(limit=100000) if e["status"] in ("theft", "false_alarm")]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            csvbuf = io.StringIO()
            wr = csv.writer(csvbuf)
            wr.writerow(["id", "label", "camera_id", "time", "score", "region", "reason", "notes", "clip"])
            for e in rows:
                wr.writerow([e["id"], e["status"], e["camera_id"],
                             datetime.fromtimestamp(e["ts"]).isoformat(timespec="seconds"),
                             e["score"], e["region"], e["reason"], e["notes"], e["clip"]])
                if e["clip"] and (engine.clips_dir / e["clip"]).exists():
                    z.write(engine.clips_dir / e["clip"], f"clips/{e['clip']}")
            z.writestr("labels.csv", csvbuf.getvalue())
        return Response(buf.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="oversyte-labels-{int(time.time())}.zip"'})

    # ---------- media ----------
    @app.get("/media/clips/{name}", dependencies=[Depends(auth)])
    def clip(name: str):
        p = engine.clips_dir / _safe_name(name)
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="video/mp4")

    @app.get("/media/thumbs/{name}")
    def thumb(name: str, request: Request, sig: str | None = None):
        if not (request.session.get("ok") or _check_sig(name, sig)):
            raise HTTPException(401)
        p = engine.thumbs_dir / _safe_name(name)
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    # ---------- cameras ----------
    @app.get("/api/cameras", dependencies=[Depends(auth)])
    def cameras():
        return [w.status() for w in engine.workers.values()]

    # Add camera wizard: test a camera's address (returns a snapshot), then save it.
    @app.post("/api/cameras/test", dependencies=[Depends(auth)])
    async def test_camera(body: CameraBody):
        url = body.stream_url()
        res = await run_in_threadpool(camera_setup.probe, url)
        jpeg = res.pop("jpeg", None)
        if jpeg:
            res["snapshot"] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        res["address"] = camera_setup.mask(url)
        return res

    @app.post("/api/cameras", dependencies=[Depends(auth)])
    def add_camera(body: CameraBody):
        url = body.stream_url()
        if any(w.camera.source == url for w in engine.workers.values()):
            raise HTTPException(409, "This camera is already connected")
        w = engine.add_camera(body.name, url, body.brand)
        return w.status()

    @app.delete("/api/cameras/{cam_id}", dependencies=[Depends(auth)])
    def remove_camera(cam_id: str):
        w = worker(cam_id)
        if w.added_from != "dashboard":
            raise HTTPException(400, "This camera is set up in config.yaml; remove it there.")
        engine.remove_camera(cam_id)
        return {"ok": True}

    @app.get("/api/cameras/{cam_id}/snapshot.jpg", dependencies=[Depends(auth)])
    def snapshot(cam_id: str, overlay: int = 0):
        w = worker(cam_id)
        jpg = w.overlay_jpeg() if overlay else w.camera.latest_jpeg()
        if not jpg:
            raise HTTPException(503, "No video yet")
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{cam_id}/live", dependencies=[Depends(auth)])
    async def live(cam_id: str, request: Request):
        w = worker(cam_id)

        async def gen():
            while not await request.is_disconnected():
                jpg = await run_in_threadpool(w.overlay_jpeg)
                if jpg:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                await asyncio.sleep(0.15)

        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{cam_id}/zones", dependencies=[Depends(auth)])
    def get_zones(cam_id: str):
        worker(cam_id)
        return engine.zones.get(cam_id)

    @app.put("/api/cameras/{cam_id}/zones", dependencies=[Depends(auth)])
    def put_zones(cam_id: str, body: ZonesBody):
        worker(cam_id)
        engine.zones.set(cam_id, body.model_dump())
        return engine.zones.get(cam_id)

    # ---------- push ----------
    @app.get("/api/push/key", dependencies=[Depends(auth)])
    def push_key():
        return {"key": pusher.public_key}

    @app.post("/api/push/subscribe", dependencies=[Depends(auth)])
    def subscribe(body: SubBody):
        if "endpoint" not in body.subscription or "keys" not in body.subscription:
            raise HTTPException(400, "Bad subscription")
        store.add_sub(body.subscription, body.label[:60])
        return {"ok": True}

    @app.post("/api/push/unsubscribe", dependencies=[Depends(auth)])
    def unsubscribe(body: UnsubBody):
        store.remove_sub(body.endpoint)
        return {"ok": True}

    @app.get("/api/push/devices", dependencies=[Depends(auth)])
    def devices():
        return [{"label": s["label"] or "Unnamed device", "created": s["created"],
                 "endpoint": s["endpoint"]} for s in store.subs()]

    # ---------- accuracy lab (labeling test videos) ----------
    eval_dir: Path = cfg["eval_dir"]

    def lab_video(name: str) -> Path:
        p = evaluation.dirs(eval_dir)["videos"] / _safe_name(name)
        if not p.is_file() or p.suffix.lower() not in evaluation.VIDEO_EXTS:
            raise HTTPException(404, "Unknown video")
        return p

    def lab_results() -> dict:
        p = eval_dir / "results.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except ValueError:
            return {}

    @app.get("/api/lab", dependencies=[Depends(auth)])
    def lab_list():
        res = lab_results()
        vids = []
        for p in evaluation.list_videos(eval_dir):
            lb = evaluation.load_labels(eval_dir, p.name)
            r = res.get("videos", {}).get(p.name)
            vids.append({"name": p.name, **evaluation.video_info(p), "done": lb["done"],
                         "events": len(lb["events"]), "zones": evaluation.zone_count(lb),
                         "angles": len(lb["scenes"]), "labeled_by": lb.get("labeled_by", ""),
                         "quality": res.get("quality", {}).get(p.name),
                         "result": {k: r[k] for k in ("caught", "events", "false_alarms")} if r else None})
        return {"folder": str(evaluation.dirs(eval_dir)["videos"]), "videos": vids,
                "types": evaluation.EVENT_TYPES,
                "results": {k: res.get(k) for k in ("run_at", "threshold", "setup", "summary", "sweep")}
                if res.get("summary") else None}

    @app.get("/api/lab/{name}/playable", dependencies=[Depends(auth)])
    def lab_playable(name: str):
        st = evaluation.preview_status(eval_dir, lab_video(name))
        return {"ready": st["ready"], "error": st.get("error")}

    @app.get("/api/lab/{name}/video", dependencies=[Depends(auth)])
    def lab_file(name: str):
        st = evaluation.preview_status(eval_dir, lab_video(name))
        if not st["ready"]:
            raise HTTPException(409, st.get("error") or "Video is still being prepared")
        p = st["file"]
        return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "video/mp4")

    @app.get("/api/lab/{name}/labels", dependencies=[Depends(auth)])
    def lab_labels(name: str):
        lab_video(name)
        res = lab_results()
        return {"labels": evaluation.load_labels(eval_dir, name),
                "result": res.get("videos", {}).get(name),
                "quality": res.get("quality", {}).get(name)}

    @app.put("/api/lab/{name}/labels", dependencies=[Depends(auth)])
    def lab_save(name: str, body: dict):
        lab_video(name)
        return evaluation.save_labels(eval_dir, name, body)

    @app.get("/api/lab/{name}/cuts", dependencies=[Depends(auth)])
    def lab_cuts(name: str):
        return {"cuts": evaluation.detect_cuts(eval_dir, lab_video(name))}

    @app.post("/api/lab/upload", dependencies=[Depends(auth)])
    def lab_upload(file: UploadFile = File(...)):
        name = Path(file.filename or "").name.replace(" ", "_")
        if Path(name).suffix.lower() not in evaluation.VIDEO_EXTS:
            raise HTTPException(400, "Not a video file (mp4, mov, avi, mkv…)")
        dest = evaluation.dirs(eval_dir)["videos"] / name
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{int(time.time())}{dest.suffix}")
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f, 1024 * 1024)
        return {"name": dest.name}

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    def _auth_page(name: str) -> Response:
        html = (WEB_DIR / "auth" / f"{name}.html").read_text(encoding="utf-8")
        for asset in ("styles.css", "auth/auth.js"):
            v = int((WEB_DIR / asset).stat().st_mtime)
            html = html.replace(f'"/{asset}"', f'"/{asset}?v={v}"')
        return Response(html, media_type="text/html", headers={"Cache-Control": "no-store"})

    # ---------- auth pages ----------
    @app.get("/auth", include_in_schema=False)
    @app.get("/auth/", include_in_schema=False)
    @app.get("/auth/login", include_in_schema=False)
    def auth_login(request: Request):
        if request.session.get("ok"):
            return RedirectResponse("/", status_code=303)
        return _auth_page("login")

    @app.get("/auth/signup", include_in_schema=False)
    def auth_signup(request: Request):
        if request.session.get("ok"):
            return RedirectResponse("/", status_code=303)
        return _auth_page("signup")

    # ---------- dashboard (PWA) ----------
    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def index(request: Request):
        """The page, with each file's URL stamped by its last-modified time
        (/app.js?v=…), so a browser can never keep running an old copy after an update."""
        if not request.session.get("ok"):
            return RedirectResponse("/auth/login?next=/", status_code=303)
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        for name in ("app.js", "styles.css"):
            v = int((WEB_DIR / name).stat().st_mtime)
            html = html.replace(f'"/{name}"', f'"/{name}?v={v}"')
        return Response(html, media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/sw.js")
    def sw():
        return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
