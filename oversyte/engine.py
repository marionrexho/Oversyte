"""Runs cameras + analysis, turns detections into incidents (clip + DB row + push)."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from .analysis import Scheduler, use_batching
from .camera import Camera
from .camera_setup import CameraList
from .clips import write_clip
from .concealment import ConcealmentDetector, Detection
from .push import Pusher
from .storage import Store

log = logging.getLogger("oversyte.engine")

ZONE_KINDS = ("shelf", "ignore")
ZONE_COLORS = {"shelf": (80, 200, 120), "ignore": (150, 150, 150)}
PHASE_COLORS = {"idle": (200, 200, 200), "reaching": (0, 200, 255),
                "carrying": (0, 140, 255), "concealing": (40, 40, 235)}


class Zones:
    """Normalized (0..1) polygons per camera, persisted to zones.json."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict = json.loads(path.read_text()) if path.exists() else {}

    def get(self, cam_id: str) -> dict:
        with self._lock:
            z = self._data.get(cam_id, {})
            return {k: [list(map(list, poly)) for poly in z.get(k, [])] for k in ZONE_KINDS}

    def set(self, cam_id: str, zones: dict):
        clean = {}
        for k in ZONE_KINDS:
            polys = []
            for poly in zones.get(k, []):
                pts = [[min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)] for x, y in poly]
                if len(pts) >= 3:
                    polys.append(pts)
            clean[k] = polys
        with self._lock:
            self._data[cam_id] = clean
            self.path.write_text(json.dumps(self._data, indent=2))

    def pixels(self, cam_id: str, size) -> dict:
        w, h = size
        return {k: [np.array([[x * w, y * h] for x, y in poly], np.float32) for poly in polys]
                for k, polys in self.get(cam_id).items()}


class CameraWorker:
    """One camera: capture, theft-rule state, recent results. The AI itself runs in the
    engine's shared Scheduler, which batches frames from all cameras."""

    def __init__(self, engine: "Engine", cam_cfg: dict):
        self.engine = engine
        fps = float(engine.cfg["detection"]["analysis_fps"])
        self.added_from = cam_cfg.get("added_from", "config")
        self.brand = cam_cfg.get("brand")
        self.camera = Camera(cam_cfg, engine.cfg["clips"], analysis_fps=fps)
        self.detector = ConcealmentDetector(engine.cfg["detection"])
        self.interval = 1.0 / fps
        # Empty aisle: check less often. Full rate resumes on the next frame with a person in it.
        idle_fps = float(engine.cfg["detection"].get("idle_analysis_fps") or fps)
        self.idle_interval = 1.0 / min(idle_fps, fps)
        keep = engine.cfg["clips"]["pre_seconds"] + engine.cfg["clips"]["post_seconds"] + 10
        self.annotations: deque[tuple[float, dict]] = deque(maxlen=int(keep * fps) + 10)
        self.last_result = None  # (ts, persons, items, bags)
        self.analysis_ms = 0.0
        # read by the Scheduler to decide when this camera is due
        self.last_analysis = 0.0
        self.last_ts = 0.0
        self.active = True

    @property
    def id(self):
        return self.camera.id

    def start(self):
        self.camera.start()
        if self.engine.scheduler is None:  # CPU: this camera runs its own AI loop
            self._stop = threading.Event()
            threading.Thread(target=self._cpu_loop, daemon=True, name=f"ai-{self.id}").start()

    def stop(self):
        if hasattr(self, "_stop"):
            self._stop.set()
        self.camera.stop()

    def _cpu_loop(self):
        try:
            from .detector import FrameAnalyzer
            analyzer = FrameAnalyzer(self.engine.cfg["detection"], self.engine.object_model)
            self.cpu_ready = True
        except Exception as e:  # model failed to load
            log.exception("[%s] could not load AI models", self.id)
            self.cpu_error = f"AI model failed to load: {e}"
            return
        while not self._stop.is_set():
            t0 = time.time()
            ts, frame = self.camera.latest()
            if frame is None or ts == self.last_ts:
                self._stop.wait(0.02)
                continue
            self.last_ts = ts
            try:
                persons, items, bags = analyzer.analyze(frame, need_objects=self.need_objects())
                self.handle(ts, persons, items, bags, (time.time() - t0) * 1000)
            except Exception:
                log.exception("[%s] analysis error", self.id)
                self._stop.wait(1)
                continue
            interval = self.interval if self.active else self.idle_interval
            self._stop.wait(max(0.0, interval - (time.time() - t0)))

    def need_objects(self) -> bool:
        return self.detector.busy()  # bags/items only matter once someone has reached into a shelf

    def handle(self, ts: float, persons: list, items: list, bags: list, ms: float):
        """Results for one frame, from the Scheduler: run the theft rules, raise detections."""
        size = self.camera.frame_size or (1, 1)
        zones = self.engine.zones.pixels(self.id, size)
        dets = self.detector.update(ts, persons, items, bags, zones["shelf"], zones["ignore"])
        self.annotations.append((ts, {p.track_id: p.box for p in persons}))
        self.last_result = (ts, persons, items, bags)
        self.analysis_ms = ms
        self.active = bool(persons) or self.detector.busy()
        for d in dets:
            log.info("[%s] DETECTION track=%s score=%.2f %s", self.id, d.track_id, d.score, d.reason)
            self.engine.on_detection(self, d)

    # ---------- live view ----------
    def overlay_jpeg(self) -> bytes | None:
        ts, frame = self.camera.latest()
        if frame is None:
            return None
        frame = frame.copy()
        zones = self.engine.zones.pixels(self.id, frame.shape[1::-1])
        for kind, polys in zones.items():
            for poly in polys:
                cv2.polylines(frame, [poly.astype(np.int32)], True, ZONE_COLORS[kind], 2)
        res = self.last_result
        if res and ts - res[0] < 1.5:
            _, persons, items, bags = res
            for b in bags:
                cv2.rectangle(frame, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (255, 120, 0), 1)
            for b in items:
                cv2.rectangle(frame, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (255, 200, 0), 1)
            for p in persons:
                st = self.detector.tracks.get(p.track_id)
                phases = [st.hands[h].phase for h in ("left", "right")] if st else ["idle", "idle"]
                worst = max(phases, key=list(PHASE_COLORS).index)
                c = PHASE_COLORS[worst]
                x1, y1, x2, y2 = (int(v) for v in p.box)
                cv2.rectangle(frame, (x1, y1), (x2, y2), c, 2)
                cv2.putText(frame, f"#{p.track_id} {worst}", (x1 + 4, max(14, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
                for idx, ph in ((9, phases[0]), (10, phases[1])):
                    k = p.kpts[idx]
                    if k[2] > 0.3:
                        cv2.circle(frame, (int(k[0]), int(k[1])), 6, PHASE_COLORS[ph], -1)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes() if ok else None

    def status(self) -> dict:
        ts, _ = self.camera.latest()
        res = self.last_result
        sched = self.engine.scheduler
        ai_ready = sched.ready.is_set() if sched else getattr(self, "cpu_ready", False)
        ai_error = sched.error if sched else getattr(self, "cpu_error", None)
        return {
            "id": self.id,
            "name": self.camera.name,
            "connected": self.camera.connected,
            "error": ai_error or self.camera.error,
            "ai_ready": bool(ai_ready),
            "frame_age": round(time.time() - ts, 1) if ts else None,
            "fps": round(self.camera.measured_fps, 1),
            "analysis_ms": round(self.analysis_ms),
            "people": len(res[1]) if res and time.time() - res[0] < 2 else 0,
            "size": self.camera.frame_size,
            "added_from": self.added_from,
            "brand": self.brand,
        }


class Engine:
    def __init__(self, cfg: dict, store: Store, pusher: Pusher):
        self.cfg = cfg
        self.store = store
        self.pusher = pusher
        self.data_dir: Path = cfg["data_dir"]
        self.clips_dir = self.data_dir / "clips"
        self.thumbs_dir = self.data_dir / "thumbs"
        self.zones = Zones(self.data_dir / "zones.json")
        self.camera_list = CameraList(self.data_dir / "cameras.json")  # added from the dashboard
        self.object_model = None
        self.scheduler: Scheduler | None = None
        self.workers: dict[str, CameraWorker] = {}
        self._workers_lock = threading.Lock()
        self.listeners: list = []  # callables(event_dict) e.g. live UI refresh
        self._stop = threading.Event()

    def start(self):
        try:
            from .detector import FrameAnalyzer
            self.object_model = FrameAnalyzer.load_object_model(self.cfg["detection"])
        except Exception:
            log.exception("object model failed to load; continuing with pose only")
        if use_batching(self.cfg["detection"]):  # GPU: one worker batches all cameras
            self.scheduler = Scheduler(self.cfg["detection"], lambda: self.workers.values(), self.object_model)
            self.scheduler.start()
        for cam in self.cfg["cameras"]:
            if cam.get("enabled", True):
                self._start_worker(dict(cam, added_from="config"))
        for cam in self.camera_list.all():
            if cam["id"] not in self.workers:
                self._start_worker(dict(cam, added_from="dashboard"))
        threading.Thread(target=self._housekeeping, daemon=True, name="housekeeping").start()

    def stop(self):
        self._stop.set()
        if self.scheduler:
            self.scheduler.stop()
        for w in list(self.workers.values()):
            w.stop()

    # ---------- cameras added and removed while running ----------
    def _start_worker(self, cam: dict) -> "CameraWorker":
        w = CameraWorker(self, cam)
        with self._workers_lock:
            self.workers = {**self.workers, w.id: w}  # swap, so readers never see a half-updated dict
        w.start()
        return w

    def add_camera(self, name: str, source: str, brand: str) -> "CameraWorker":
        cam = self.camera_list.add(name, source, brand, taken=set(self.workers))
        return self._start_worker(dict(cam, added_from="dashboard"))

    def remove_camera(self, cam_id: str) -> bool:
        """Only cameras added from the dashboard; config.yaml cameras are edited in that file."""
        w = self.workers.get(cam_id)
        if not w or w.added_from != "dashboard":
            return False
        self.camera_list.remove(cam_id)
        with self._workers_lock:
            self.workers = {k: v for k, v in self.workers.items() if k != cam_id}
        w.stop()
        return True

    # ---------- incidents ----------
    def on_detection(self, worker: CameraWorker, det: Detection):
        threading.Thread(target=self._make_incident, args=(worker, det), daemon=True).start()

    def _make_incident(self, worker: CameraWorker, det: Detection | None, is_test=False):
        c = self.cfg["clips"]
        event_ts = det.ts if det else time.time() - c["post_seconds"]
        wait = event_ts + c["post_seconds"] - time.time()
        if wait > 0:
            time.sleep(wait)
        eid = uuid.uuid4().hex
        clip_name, thumb_name = f"{eid}.mp4", f"{eid}.jpg"
        frames = worker.camera.frames_between(event_ts - c["pre_seconds"], event_ts + c["post_seconds"])
        try:
            ok = write_clip(frames, self.clips_dir / clip_name, self.thumbs_dir / thumb_name,
                            c["fps"], event_ts, det.track_id if det else None,
                            list(worker.annotations), worker.camera.name)
        except Exception:
            log.exception("clip encoding failed")
            ok = False
        ev = {
            "id": eid,
            "camera_id": worker.id,
            "camera_name": worker.camera.name,
            "ts": event_ts,
            "score": det.score if det else 1.0,
            "region": det.region if det else None,
            "reason": det.reason if det else "Test alert: this is what a real alert looks like",
            "clip": clip_name if ok else None,
            "thumb": thumb_name if ok and (self.thumbs_dir / thumb_name).exists() else None,
            "is_test": 1 if is_test else 0,
        }
        self.store.add_event(ev)
        self.notify(ev)
        return ev

    def notify(self, ev: dict):
        from .server import signed_thumb_url  # avoid import cycle
        title = ("TEST: " if ev["is_test"] else "") + f"Possible theft: {ev['camera_name']}"
        payload = {
            "title": title,
            "body": ev["reason"],
            "url": f"/#/event/{ev['id']}",
            "tag": ev["id"],
            "image": signed_thumb_url(ev["thumb"]) if ev["thumb"] else None,
            "ts": ev["ts"],
        }
        result = self.pusher.send_all(payload)
        log.info("alert %s pushed: %s", ev["id"], result)
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception:
                pass
        return result

    def test_alert(self, camera_id: str | None = None) -> dict:
        worker = self.workers.get(camera_id) if camera_id else next(iter(self.workers.values()), None)
        if worker is None:
            ev = {"id": uuid.uuid4().hex, "camera_id": "-", "camera_name": "Test", "ts": time.time(),
                  "score": 1.0, "region": None, "reason": "Test alert (no camera connected)",
                  "clip": None, "thumb": None, "is_test": 1}
            self.store.add_event(ev)
            self.notify(ev)
            return ev
        return self._make_incident(worker, None, is_test=True)

    def delete_files(self, ev: dict):
        for d, name in ((self.clips_dir, ev.get("clip")), (self.thumbs_dir, ev.get("thumb"))):
            if name:
                (d / name).unlink(missing_ok=True)

    def _housekeeping(self):
        while not self._stop.wait(5):
            try:
                cutoff = time.time() - float(self.cfg["clips"]["retention_days"]) * 86400
                for ev in self.store.expired_events(cutoff):
                    self.delete_files(ev)
                    self.store.delete_event(ev["id"])
            except Exception:
                log.exception("housekeeping failed")
            self._stop.wait(3600)
