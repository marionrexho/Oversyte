"""Adding cameras from the dashboard: stream addresses per brand, a connection test, and the
list of cameras added this way (data/cameras.json, kept apart from config.yaml)."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import cv2

# How each brand names its streams. `sub` = the camera's low-resolution second stream, which is
# plenty for detection and much lighter on the store's internet and our servers.
BRANDS = {
    "hikvision": {"name": "Hikvision", "port": 554,
                  "path": lambda ch, sub: f"/Streaming/Channels/{ch}0{2 if sub else 1}"},
    "dahua": {"name": "Dahua / Amcrest", "port": 554,
              "path": lambda ch, sub: f"/cam/realmonitor?channel={ch}&subtype={1 if sub else 0}"},
    "reolink": {"name": "Reolink", "port": 554,
                "path": lambda ch, sub: f"/h264Preview_{ch:02d}_{'sub' if sub else 'main'}"},
    "uniview": {"name": "Uniview", "port": 554,
                "path": lambda ch, sub: f"/unicast/c{ch}/s{1 if sub else 0}/live"},
}
ALLOWED_SCHEMES = ("rtsp", "rtsps", "http", "https")


class SetupError(ValueError):
    """A problem with what the user typed; the message is shown to them as-is."""


def build_url(brand: str, host: str = "", port: int | None = None, username: str = "",
              password: str = "", channel: int = 1, substream: bool = True, url: str = "") -> str:
    if brand == "other":
        url = (url or "").strip()
        if urlsplit(url).scheme.lower() not in ALLOWED_SCHEMES:
            raise SetupError("Enter the camera's stream address, starting with rtsp://")
        return url
    if brand not in BRANDS:
        raise SetupError("Pick a camera brand")
    host = (host or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:]+\]", host):
        raise SetupError("Enter the camera's IP address, for example 192.168.1.64")
    if not 1 <= int(channel) <= 256:
        raise SetupError("Camera number must be between 1 and 256")
    b = BRANDS[brand]
    login = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username else ""
    return f"rtsp://{login}{host}:{int(port or b['port'])}{b['path'](int(channel), substream)}"


def mask(url: str) -> str:
    """The address with the password hidden, safe to show in the dashboard."""
    try:
        p = urlsplit(url)
    except ValueError:
        return "(invalid address)"
    if p.password is None:
        return url
    netloc = f"{p.username}:••••@{p.hostname}" + (f":{p.port}" if p.port else "")
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


def probe(url: str, timeout: float = 12.0) -> dict:
    """Try to open the stream and grab one frame. Never raises; reports what went wrong."""
    out: dict = {}

    def attempt():
        t0 = time.time()
        cap = cv2.VideoCapture(url)
        try:
            out["opened"] = cap.isOpened()
            out["open_s"] = time.time() - t0
            if out["opened"]:
                for _ in range(30):  # the first frames of a stream can be empty
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        out["frame"] = frame
                        break
        finally:
            cap.release()

    th = threading.Thread(target=attempt, daemon=True)  # an unreachable address can hang a while
    th.start()
    th.join(timeout)
    if th.is_alive():
        return {"ok": False, "error": "No answer from the camera.",
                "hint": "Check the IP address, and that this Oversyte server can reach the camera's "
                        "network (the same network, or a Tailscale connection)."}
    frame = out.get("frame")
    if frame is None:
        quick = out.get("open_s", timeout) < 4
        return {"ok": False,
                "error": "The camera answered but didn't send video." if quick else "Couldn't open the video stream.",
                "hint": "Check the username and password, the camera number, and that RTSP is switched on "
                        "in the camera's settings. Some cameras lock you out for a while after several wrong "
                        "passwords."}
    h, w = frame.shape[:2]
    scale = min(1.0, 960 / w)
    if scale < 1:
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return {"ok": True, "width": w, "height": h, "jpeg": buf.tobytes() if ok else None}


class CameraList:
    """Cameras added from the dashboard. They live in data/ (never committed), because their
    addresses usually contain the camera password."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def all(self) -> list[dict]:
        with self._lock:
            return self._read()

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def add(self, name: str, source: str, brand: str, taken: set[str]) -> dict:
        name = (name or "").strip()[:60] or "Camera"
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "camera"
        cam_id = base if base not in taken else f"{base}-{uuid.uuid4().hex[:4]}"
        cam = {"id": cam_id, "name": name, "source": source, "brand": brand, "added": time.time()}
        with self._lock:
            cams = self._read()
            cams.append(cam)
            self._write(cams)
        return cam

    def remove(self, cam_id: str) -> bool:
        with self._lock:
            cams = self._read()
            keep = [c for c in cams if c["id"] != cam_id]
            if len(keep) == len(cams):
                return False
            self._write(keep)
            return True

    def _write(self, cams: list[dict]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cams, indent=2), encoding="utf-8")
        tmp.replace(self.path)
