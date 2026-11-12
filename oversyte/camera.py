"""Camera capture: one thread per camera, keeping the latest frame plus a rolling
JPEG buffer that clips are cut from."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque

import cv2
import numpy as np

# Prefer TCP for RTSP: far fewer smeared/grey frames on store Wi-Fi.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

log = logging.getLogger("oversyte.camera")


def _even(n: int) -> int:
    return n - (n % 2)


class Camera(threading.Thread):
    def __init__(self, cam_cfg: dict, clip_cfg: dict):
        super().__init__(daemon=True, name=f"cam-{cam_cfg['id']}")
        self.id: str = cam_cfg["id"]
        self.name: str = cam_cfg["name"]
        self.source = cam_cfg["source"]
        self.loop: bool = bool(cam_cfg.get("loop", False))
        self.max_width: int = int(clip_cfg["max_width"])
        self.buffer_fps: float = float(clip_cfg["fps"])
        seconds = float(clip_cfg["pre_seconds"]) + float(clip_cfg["post_seconds"]) + 8
        self._buffer: deque[tuple[float, bytes]] = deque(maxlen=int(seconds * self.buffer_fps))
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._latest_ts: float = 0.0
        self._latest_jpeg: bytes | None = None
        self._stop = threading.Event()
        self.connected = False
        self.error: str | None = None
        self.frame_size: tuple[int, int] | None = None  # (w, h) after resize
        self.measured_fps = 0.0

    # ---------- public API ----------
    def latest(self) -> tuple[float, np.ndarray | None]:
        with self._lock:
            return self._latest_ts, self._latest

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            if self._latest_jpeg is None and self._latest is not None:
                ok, buf = cv2.imencode(".jpg", self._latest, [cv2.IMWRITE_JPEG_QUALITY, 80])
                self._latest_jpeg = buf.tobytes() if ok else None
            return self._latest_jpeg

    def frames_between(self, t0: float, t1: float) -> list[tuple[float, bytes]]:
        with self._lock:
            return [(ts, j) for ts, j in self._buffer if t0 <= ts <= t1]

    def stop(self):
        self._stop.set()

    # ---------- capture loop ----------
    def _open(self) -> cv2.VideoCapture:
        src = self.source
        if isinstance(src, str) and src.isdigit():
            src = int(src)
        if isinstance(src, int) and sys.platform == "win32":
            return cv2.VideoCapture(src, cv2.CAP_DSHOW)
        return cv2.VideoCapture(src)

    def _is_file(self) -> bool:
        return isinstance(self.source, str) and os.path.isfile(self.source)

    def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            cap = self._open()
            if not cap.isOpened():
                self.connected = False
                self.error = "Cannot open video source"
                log.warning("[%s] cannot open source, retrying in %.0fs", self.id, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)
                continue
            log.info("[%s] connected", self.id)
            self.connected, self.error, backoff = True, None, 1.0
            self._read_loop(cap)
            cap.release()
            self.connected = False
            if self._is_file() and not self.loop:
                self.error = "Video file finished"
                log.info("[%s] video file finished", self.id)
                return
            self._stop.wait(1.0)

    def _read_loop(self, cap: cv2.VideoCapture):
        is_file = self._is_file()
        file_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if not (1 <= file_fps <= 120):
            file_fps = 25.0
        frame_interval = 1.0 / file_fps
        last_buffered = 0.0
        fails = 0
        fps_count, fps_t0 = 0, time.time()
        next_t = time.time()
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                fails += 1
                if is_file or fails > 30:
                    return
                time.sleep(0.05)
                continue
            fails = 0
            if is_file:  # play files in real time so timing logic matches live cameras
                next_t += frame_interval
                delay = next_t - time.time()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_t = time.time()
            frame = self._resize(frame)
            now = time.time()
            with self._lock:
                self._latest, self._latest_ts, self._latest_jpeg = frame, now, None
            if now - last_buffered >= 1.0 / self.buffer_fps:
                last_buffered = now
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
                if ok:
                    with self._lock:
                        self._buffer.append((now, buf.tobytes()))
            fps_count += 1
            if now - fps_t0 >= 2:
                self.measured_fps = fps_count / (now - fps_t0)
                fps_count, fps_t0 = 0, now

    def _resize(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w > self.max_width:
            scale = self.max_width / w
            w, h = int(w * scale), int(h * scale)
        w, h = _even(w), _even(h)
        if (w, h) != frame.shape[1::-1]:
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        self.frame_size = (w, h)
        return frame
