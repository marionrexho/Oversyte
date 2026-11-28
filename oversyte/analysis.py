"""One AI worker for all cameras: frames from every camera that is due go to the GPU together.

Running the models once per camera repeats the same per-call work (preprocessing, Python
overhead, GPU launches) for every frame; on a cloud server that, not the GPU, set the limit
on cameras per server. Batching does that work once per batch. Each camera still keeps its
own person tracker, so track ids never mix between cameras.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterable, Protocol

import numpy as np
import torch

from .detector import BAG_CLASSES, ITEM_CLASSES, _load, gpu_args, objects_from, persons_from

log = logging.getLogger("oversyte.analysis")


class BatchAnalyzer:
    """Pose + tracking for many cameras per GPU call; bag/item detection only where needed."""

    def __init__(self, det_cfg: dict, object_model=None, max_batch: int = 16):
        from ultralytics.trackers.byte_tracker import BYTETracker
        from ultralytics.utils import YAML, IterableSimpleNamespace
        from ultralytics.utils.checks import check_yaml

        self.cfg = det_cfg
        self.device = det_cfg.get("device", "cpu")
        self.imgsz = int(det_cfg["imgsz"])
        self.max_batch = max_batch
        # GPU settings (see detector.gpu_args); on a CPU only square padding, so a batch of mixed
        # sizes matches single frames there too (the CPU path doesn't batch in Oversyte itself)
        self._extra = gpu_args() if use_batching(det_cfg) else {"rect": False}
        self.pose = _load(det_cfg["pose_model"])
        self.objects = object_model
        # the same tracker settings model.track(tracker="bytetrack.yaml") uses
        self._tracker_cfg = IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml")))
        self._new_tracker = lambda: BYTETracker(args=self._tracker_cfg)
        self.trackers: dict[str, object] = {}

    def forget(self, keep: Iterable[str]):
        """Drop tracker state for cameras that were removed."""
        keep = set(keep)
        for cam_id in [c for c in self.trackers if c not in keep]:
            del self.trackers[cam_id]

    def analyze(self, jobs: list[tuple[str, np.ndarray, bool]]) -> dict[str, tuple]:
        """jobs: [(camera id, frame, need bag/item detection)] -> {camera id: (persons, items, bags)}.

        Frames of any size share a batch: when sizes differ, the model pads each to the same
        square (same scale as alone, just more border), which the GPU absorbs easily. Grouping
        by size instead would leave most stores' mixed cameras unbatched."""
        out: dict[str, tuple] = {}
        for i in range(0, len(jobs), self.max_batch):
            out.update(self._analyze_batch(jobs[i:i + self.max_batch]))
        return out

    def _analyze_batch(self, jobs: list[tuple[str, np.ndarray, bool]]) -> dict[str, tuple]:
        frames = [f for _, f, _ in jobs]
        results = self.pose.predict(frames, classes=[0], conf=float(self.cfg["person_conf"]),
                                    imgsz=self.imgsz, device=self.device, verbose=False, **self._extra)
        out, wants_objects = {}, []
        for (cam_id, frame, need_objects), res in zip(jobs, results):
            tracker = self.trackers.get(cam_id)
            if tracker is None:
                tracker = self.trackers[cam_id] = self._new_tracker()
            persons = []
            tracks = tracker.update(res.boxes.cpu().numpy(), frame)  # as model.track() does
            if len(tracks):
                res = res[tracks[:, -1].astype(int)]
                res.update(boxes=torch.as_tensor(tracks[:, :-1], device=res.boxes.data.device))
                persons = persons_from(res)
            out[cam_id] = (persons, [], [])
            if persons and need_objects and self.objects is not None:
                wants_objects.append((cam_id, frame))
        if wants_objects:
            objs = self.objects.predict([f for _, f in wants_objects],
                                        classes=list(BAG_CLASSES) + list(ITEM_CLASSES),
                                        conf=float(self.cfg["object_conf"]), imgsz=self.imgsz,
                                        device=self.device, verbose=False, **self._extra)
            for (cam_id, _), r in zip(wants_objects, objs):
                items, bags = objects_from(r)
                out[cam_id] = (out[cam_id][0], items, bags)
        return out


def use_batching(det_cfg: dict) -> bool:
    """Batch on a GPU. On a CPU, cameras analysed in parallel threads use more cores than one
    batch worker, so each camera keeps its own AI loop there."""
    return str(det_cfg.get("device", "cpu")).lower() not in ("cpu", "mps")


class Analysed(Protocol):
    """What the scheduler needs from a camera (engine.CameraWorker, or the benchmark's)."""
    id: str
    interval: float          # seconds between analyses while someone is in view
    idle_interval: float     # ... while the view is empty
    last_analysis: float     # when its last frame went to the AI
    last_ts: float           # timestamp of that frame
    active: bool             # someone in view, or mid-sequence, at the last analysis

    @property
    def camera(self): ...
    def need_objects(self) -> bool: ...
    def handle(self, ts: float, persons: list, items: list, bags: list, ms: float): ...


class Scheduler(threading.Thread):
    """Every few milliseconds: collect the cameras that are due, analyse them as one batch,
    hand each camera its results."""

    def __init__(self, det_cfg: dict, get_cameras: Callable[[], Iterable[Analysed]],
                 object_model=None, max_batch: int = 16):
        super().__init__(daemon=True, name="ai-scheduler")
        self.det_cfg, self.get_cameras = det_cfg, get_cameras
        self.object_model, self.max_batch = object_model, max_batch
        self.ready = threading.Event()
        self.error: str | None = None
        self.batches = 0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            analyzer = BatchAnalyzer(self.det_cfg, self.object_model, self.max_batch)
        except Exception as e:  # model failed to load
            log.exception("could not load AI models")
            self.error = f"AI model failed to load: {e}"
            return
        self.ready.set()
        last_prune = 0.0
        while not self._stop.is_set():
            cams = list(self.get_cameras())
            now = time.time()
            if now - last_prune > 10:
                analyzer.forget(c.id for c in cams)
                last_prune = now
            due = []
            for c in cams:
                ts, frame = c.camera.latest()
                if frame is None or ts == c.last_ts:
                    continue
                if now - c.last_analysis < (c.interval if c.active else c.idle_interval) * 0.95:
                    continue
                due.append((c, ts, frame))
            if not due:
                self._stop.wait(0.01)
                continue
            due.sort(key=lambda d: d[0].last_analysis)  # longest-waiting first if over a batch
            due = due[:self.max_batch]
            for c, ts, _ in due:
                c.last_analysis, c.last_ts = now, ts
            t0 = time.time()
            try:
                results = analyzer.analyze([(c.id, f, c.need_objects()) for c, _, f in due])
            except Exception:
                log.exception("analysis error")
                self._stop.wait(1)
                continue
            ms = (time.time() - t0) * 1000 / len(due)
            self.batches += 1
            for c, ts, _ in due:
                persons, items, bags = results.get(c.id, ([], [], []))
                try:
                    c.handle(ts, persons, items, bags, ms)
                except Exception:
                    log.exception("[%s] error handling results", c.id)
