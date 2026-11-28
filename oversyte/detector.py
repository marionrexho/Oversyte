"""YOLO wrappers: pose + tracking for people, plain detection for bags and items."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from .concealment import Person

log = logging.getLogger("oversyte.detector")

# COCO class ids
BAG_CLASSES = {24: "backpack", 26: "handbag", 28: "suitcase"}
ITEM_CLASSES = {
    39: "bottle", 40: "wine glass", 41: "cup", 42: "fork", 43: "knife", 44: "spoon",
    45: "bowl", 46: "banana", 47: "apple", 48: "sandwich", 49: "orange", 50: "broccoli",
    51: "carrot", 52: "hot dog", 53: "pizza", 54: "donut", 55: "cake", 64: "mouse",
    65: "remote", 67: "cell phone", 73: "book", 76: "scissors", 79: "toothbrush",
}

_load_lock = threading.Lock()


def gpu_args() -> dict:
    """Model settings on a GPU, shared by the single-camera and the batched path so both see
    exactly the same input: 16-bit, and every frame padded to a full square (`rect=False`).
    A batch of differently sized frames is always padded to a square, so a single frame must
    be too, or live results and eval.py would differ."""
    return {"half": True, "rect": False}


def persons_from(res) -> list[Person]:
    """Tracked people in one YOLO pose result (people without a track id are left out)."""
    if res.boxes is None or res.boxes.id is None or res.keypoints is None:
        return []
    boxes = res.boxes.xyxy.cpu().numpy()
    ids = res.boxes.id.int().cpu().numpy()
    kpts = res.keypoints.data.cpu().numpy()
    return [Person(int(i), tuple(float(v) for v in b), k) for b, i, k in zip(boxes, ids, kpts)]


def objects_from(res) -> tuple[list, list]:
    """(items, bags) boxes in one YOLO detection result."""
    items, bags = [], []
    if res.boxes is not None:
        for b, c in zip(res.boxes.xyxy.cpu().numpy(), res.boxes.cls.int().cpu().numpy()):
            (bags if int(c) in BAG_CLASSES else items).append(tuple(float(v) for v in b))
    return items, bags


def _load(path: str):
    from ultralytics import YOLO
    with _load_lock:  # first run downloads weights; don't download twice in parallel
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return YOLO(path)


class FrameAnalyzer:
    """One instance per camera: the pose tracker keeps per-stream track state."""

    def __init__(self, det_cfg: dict, shared_object_model=None):
        self.cfg = det_cfg
        self.device = det_cfg.get("device", "cpu")
        self.imgsz = int(det_cfg["imgsz"])
        # NVIDIA GPUs run the models about twice as fast in 16-bit; CPUs don't support it.
        self.half = str(self.device).lower() not in ("cpu", "mps")
        self._extra = gpu_args() if self.half else {}
        self.pose = _load(det_cfg["pose_model"])
        self.objects = shared_object_model
        self._obj_lock = threading.Lock()

    @staticmethod
    def load_object_model(det_cfg: dict):
        path = det_cfg.get("object_model")
        return _load(path) if path else None

    def analyze(self, frame: np.ndarray, need_objects: bool = True):
        res = self.pose.track(
            frame, persist=True, tracker="bytetrack.yaml", classes=[0],
            conf=float(self.cfg["person_conf"]), imgsz=self.imgsz,
            device=self.device, verbose=False, **self._extra,
        )[0]
        persons = persons_from(res)
        items, bags = [], []
        if self.objects is not None and persons and need_objects:
            with self._obj_lock:
                r = self.objects.predict(
                    frame, classes=list(BAG_CLASSES) + list(ITEM_CLASSES),
                    conf=float(self.cfg["object_conf"]), imgsz=self.imgsz,
                    device=self.device, verbose=False, **self._extra,
                )[0]
            items, bags = objects_from(r)
        return persons, items, bags
