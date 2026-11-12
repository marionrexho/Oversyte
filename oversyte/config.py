"""Loads config.yaml and fills in defaults."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULTS = {
    "store_name": "My Store",
    "dashboard_password": "change-me",
    "public_url": "http://localhost:8000",
    "push_contact": "mailto:admin@example.com",
    "data_dir": "data",
    "eval_dir": "eval",
    "server": {"host": "0.0.0.0", "port": 8000},
    "detection": {
        "pose_model": "models/yolo11n-pose.pt",
        "object_model": "models/yolo11n.pt",
        "device": "cpu",
        "analysis_fps": 6,
        "imgsz": 640,
        "person_conf": 0.4,
        "object_conf": 0.25,
        "reach_min_seconds": 0.25,
        "conceal_window_seconds": 4.0,
        "conceal_dwell_seconds": 0.35,
        "alert_threshold": 0.6,
        "person_cooldown_seconds": 30,
    },
    "clips": {
        "pre_seconds": 6,
        "post_seconds": 3,
        "fps": 12,
        "max_width": 960,
        "retention_days": 30,
    },
    "cameras": [],
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path) -> dict:
    path = Path(path)
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = _merge(DEFAULTS, raw)
    root = path.resolve().parent
    data_dir = Path(cfg["data_dir"])
    if not data_dir.is_absolute():
        data_dir = root / data_dir
    cfg["data_dir"] = data_dir
    eval_dir = Path(cfg["eval_dir"])
    cfg["eval_dir"] = eval_dir if eval_dir.is_absolute() else root / eval_dir
    for key in ("pose_model", "object_model"):
        p = cfg["detection"].get(key)
        if p and not Path(p).is_absolute():
            cfg["detection"][key] = str(root / p)
    seen = set()
    for cam in cfg["cameras"]:
        cam.setdefault("name", cam["id"])
        cam.setdefault("loop", False)
        cam.setdefault("enabled", True)
        if cam["id"] in seen:
            raise ValueError(f"Duplicate camera id: {cam['id']}")
        seen.add(cam["id"])
    return cfg
