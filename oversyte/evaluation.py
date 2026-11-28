"""Accuracy measurement: run the detector over labeled videos and score it.

Folder layout (eval_dir, default ./eval):
    videos/<name>.mp4            test videos (exported from store recorders, phone clips…)
    labels/<name>.json           labels made in the dashboard's Accuracy lab
    cache/<name>.<hash>.pkl      saved AI output per video, so re-scoring is instant
    results.json                 last scoring run (shown in the Accuracy lab)

A label file looks like:
    {"events": [{"t": 42.3, "type": "pocket"}],
     "scenes": [{"start": 0, "shelf": [...], "ignore": [...]},    # camera angle 1
                {"start": 31.5, "shelf": [...], "ignore": [...]}], # recorder switched view
     "done": true, "notes": ""}
"done" means someone watched the whole video, so any alert without a matching
label counts as a false alarm.

The slow part (running the AI on every frame) is cached. The fast part (the theft
rules) is replayed from the cache on every run, so you can change thresholds and
zones and re-score in seconds.
"""
from __future__ import annotations

import hashlib
import json
import pickle
import re
import subprocess
import threading
import time
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from .concealment import ConcealmentDetector, Detection, Person

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm", ".ts"}
EVENT_TYPES = {
    "pocket": "Into pocket",
    "bag": "Into bag",
    "waistband": "Waistband / under clothes",
    "grab_run": "Grab and run",
    "other": "Other theft",
    "staff": "Employee theft (register / cash)",
}
CACHE_VERSION = 2


# ---------- files ----------
def dirs(eval_dir: Path) -> dict[str, Path]:
    d = {k: eval_dir / k for k in ("videos", "labels", "cache")}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


def list_videos(eval_dir: Path) -> list[Path]:
    return sorted(p for p in dirs(eval_dir)["videos"].iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS)


def empty_labels() -> dict:
    return {"events": [], "scenes": [{"start": 0.0, "shelf": [], "ignore": []}],
            "done": False, "notes": "", "labeled_by": ""}


def scenes_of(labels: dict) -> list[dict]:
    """Camera angles in a video, each with its own zones, sorted by start time.
    Older label files had one set of zones (top-level shelf/ignore): that becomes angle 1."""
    scenes = labels.get("scenes")
    if not scenes:
        scenes = [{"start": 0.0, "shelf": labels.get("shelf", []), "ignore": labels.get("ignore", [])}]
    scenes = sorted(scenes, key=lambda s: float(s.get("start", 0)))
    scenes[0] = {**scenes[0], "start": 0.0}
    return scenes


def zone_count(labels: dict) -> int:
    return sum(len(s.get("shelf", [])) for s in scenes_of(labels))


def load_labels(eval_dir: Path, name: str) -> dict:
    p = dirs(eval_dir)["labels"] / f"{name}.json"
    out = empty_labels()
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        out.update({k: v for k, v in raw.items() if k not in ("shelf", "ignore")})
        out["scenes"] = scenes_of(raw)
    return out


def _clean_polys(polys) -> list:
    return [[[min(max(float(x), 0), 1), min(max(float(y), 0), 1)] for x, y in poly]
            for poly in polys or [] if len(poly) >= 3]


def save_labels(eval_dir: Path, name: str, labels: dict) -> dict:
    clean = empty_labels()
    clean["done"] = bool(labels.get("done"))
    clean["notes"] = str(labels.get("notes", ""))[:2000]
    clean["labeled_by"] = str(labels.get("labeled_by", ""))[:60]
    clean["events"] = sorted(
        ({"t": round(float(e["t"]), 2),
          "type": e.get("type") if e.get("type") in EVENT_TYPES else "other"}
         for e in labels.get("events", [])), key=lambda e: e["t"])
    scenes = []
    for s in scenes_of(labels):
        start = round(max(float(s.get("start", 0)), 0.0), 2)
        if scenes and start - scenes[-1]["start"] < 0.5:
            continue  # two angles at the same moment: keep the first
        scenes.append({"start": start, "shelf": _clean_polys(s.get("shelf")),
                       "ignore": _clean_polys(s.get("ignore"))})
    clean["scenes"] = scenes
    p = dirs(eval_dir)["labels"] / f"{name}.json"
    p.write_text(json.dumps(clean, indent=1), encoding="utf-8")
    return clean


# ---------- camera-angle (scene cut) detection ----------
def detect_cuts(eval_dir: Path, path: Path, threshold: float = 0.4) -> list[float]:
    """Times (s) where the picture changes completely, e.g. the recorder switched cameras."""
    cache = dirs(eval_dir)["cache"] / f"{path.name}.cuts.json"
    st = path.stat()
    key = [st.st_size, int(st.st_mtime), threshold]
    if cache.exists():
        data = json.loads(cache.read_text())
        if data.get("key") == key:
            return data["cuts"]
    import imageio_ffmpeg
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path), "-an",
           "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    cuts = []
    for t in (float(m) for m in re.findall(r"pts_time:([\d.]+)", r.stderr)):
        if t > 0.5 and (not cuts or t - cuts[-1] > 2.0):  # ignore flicker right after a cut
            cuts.append(round(t, 2))
    cache.write_text(json.dumps({"key": key, "cuts": cuts}))
    return cuts


_info_cache: dict[tuple, dict] = {}


def video_info(path: Path) -> dict:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    if key not in _info_cache:
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        w, h = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        cap.release()
        _info_cache[key] = {"duration": round(n / fps, 1) if fps > 0 else None,
                            "fps": round(fps, 1) if 0 < fps < 240 else None,
                            "width": int(w), "height": int(h), "size": st.st_size}
    return _info_cache[key]


# ---------- browser preview (many recorders export H.265 etc. that browsers can't play) ----------
_converting: dict[str, str] = {}  # name -> "running" | "error: ..."
_conv_lock = threading.Lock()


_playable_cache: dict[tuple, bool] = {}


def _browser_playable(path: Path) -> bool:
    key = (str(path), path.stat().st_mtime)
    if key not in _playable_cache:
        _playable_cache[key] = _probe_playable(path)
    return _playable_cache[key]


def _probe_playable(path: Path) -> bool:
    if path.suffix.lower() not in (".mp4", ".m4v", ".mov"):
        return False
    import imageio_ffmpeg
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, errors="replace")
    m = re.search(r"Video: (\w+)(?: \((\w[\w ]*)\))?.*?, (yuv\w+)", r.stderr)
    return bool(m) and m.group(1) == "h264" and m.group(3).startswith("yuv420p")


def preview_status(eval_dir: Path, path: Path) -> dict:
    """{"ready": True, "file": Path} or {"ready": False, "error"?: str}; starts conversion if needed."""
    if _browser_playable(path):
        return {"ready": True, "file": path}
    out = dirs(eval_dir)["cache"] / f"{path.name}.preview.mp4"
    if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
        return {"ready": True, "file": out}
    with _conv_lock:
        st = _converting.get(path.name)
        if st and st.startswith("error"):
            return {"ready": False, "error": st}
        if st != "running":
            _converting[path.name] = "running"
            threading.Thread(target=_convert, args=(path, out), daemon=True).start()
    return {"ready": False}


def _convert(src: Path, out: Path):
    import imageio_ffmpeg
    tmp = out.with_suffix(".part.mp4")
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
           "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-pix_fmt", "yuv420p", "-vf", "scale='min(1280,iw)':-2", "-movflags", "+faststart", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    with _conv_lock:
        if r.returncode == 0 and tmp.exists():
            tmp.replace(out)
            _converting.pop(src.name, None)
        else:
            tmp.unlink(missing_ok=True)
            _converting[src.name] = "error: can't read this video. Export it from the recorder as MP4 or AVI."


# ---------- step 1: run the AI over a video (slow, cached) ----------
def _cache_key(path: Path, det_cfg: dict, clip_cfg: dict, name: str | None = None) -> str:
    st = path.stat()
    parts = [CACHE_VERSION, name or path.name, st.st_size, int(st.st_mtime),
             Path(det_cfg["pose_model"]).name, Path(det_cfg.get("object_model") or "-").name,
             det_cfg["analysis_fps"], det_cfg["imgsz"], det_cfg["person_conf"],
             det_cfg["object_conf"], clip_cfg["max_width"]]
    if str(det_cfg.get("device", "cpu")).lower() not in ("cpu", "mps"):
        parts.append("gpu-square")  # GPU runs pad differently (detector.gpu_args); CPU keys unchanged
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:12]


def extract(path: Path, eval_dir: Path, det_cfg: dict, clip_cfg: dict, object_model=None,
            use_cache=True, progress=None) -> dict:
    """Returns {"size": (w, h), "frames": [(t, persons, items, bags)], "seconds": float}."""
    cache = dirs(eval_dir)["cache"] / f"{path.name}.{_cache_key(path, det_cfg, clip_cfg)}.pkl"
    if use_cache and cache.exists():
        with open(cache, "rb") as f:
            return pickle.load(f)

    from .detector import FrameAnalyzer
    analyzer = FrameAnalyzer(det_cfg, object_model)  # fresh tracker for each video
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {path.name}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if not (1 <= fps <= 240):
        fps = 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = 1.0 / float(det_cfg["analysis_fps"])
    max_w = int(clip_cfg["max_width"])
    frames, next_t, idx, size, t0 = [], 0.0, 0, None, time.time()
    brightness, sharpness = [], []
    while True:
        if not cap.grab():
            break
        t = idx / fps
        idx += 1
        if t + 1e-6 < next_t:
            continue
        next_t += step
        ok, frame = cap.retrieve()
        if not ok:
            continue
        h, w = frame.shape[:2]
        if w > max_w:
            frame = cv2.resize(frame, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
        size = frame.shape[1::-1]
        if len(brightness) < 2000 and idx % 3 == 0:  # image-quality sample
            g = cv2.cvtColor(cv2.resize(frame, (480, int(480 * size[1] / size[0]))), cv2.COLOR_BGR2GRAY)
            brightness.append(float(g.mean()))
            sharpness.append(float(cv2.Laplacian(g, cv2.CV_32F).var()))
        persons, items, bags = analyzer.analyze(frame, need_objects=True)
        frames.append((t, [(p.track_id, p.box, p.kpts.astype(np.float32)) for p in persons],
                       items, bags))
        if progress and total:
            progress(min(idx / total, 1.0))
    cap.release()
    out = {"size": size or (1, 1), "frames": frames, "seconds": idx / fps,
           "ai_seconds": time.time() - t0,
           "image": {"brightness": float(np.median(brightness)) if brightness else None,
                     "sharpness": float(np.median(sharpness)) if sharpness else None}}
    with open(cache, "wb") as f:  # one cache per video *and* AI setup, so comparisons stay instant
        pickle.dump(out, f)
    return out


# ---------- video quality: is a miss the camera's fault or the AI's? ----------
def quality(extracted: dict, info: dict, imgsz: int) -> dict:
    """How well the AI can see people and hands in this video, with plain-English issues."""
    frames = extracted["frames"]
    w, h = extracted["size"]
    to_model = imgsz / max(w, h)  # the model sees the frame shrunk/grown to imgsz
    heights, wrists, with_people = [], [], 0
    for _, persons, _, _ in frames:
        with_people += bool(persons)
        for _, box, k in persons:
            heights.append((box[3] - box[1]) * to_model)
            wrists.append(float(max(k[9][2], k[10][2]) >= 0.3))
    person_px = float(np.median(heights)) if heights else None
    hands = float(np.mean(wrists)) if wrists else None
    img = extracted.get("image", {})
    bright, sharp = img.get("brightness"), img.get("sharpness")

    issues, bad = [], 0
    if not heights:
        issues.append("no people detected")
        bad += 2
    if info.get("height") and min(info["height"], info["width"]) < 400:
        issues.append(f"low resolution ({info['width']}x{info['height']})")
        bad += 1
    if info.get("fps") and info["fps"] < 8:
        issues.append(f"choppy ({info['fps']:.0f} fps)")
        bad += 1
    if person_px is not None and person_px < 110:
        issues.append("people look small / far away")
        bad += 1 if person_px >= 70 else 2
    if hands is not None and hands < 0.6:
        issues.append("hands often not visible")
        bad += 1 if hands >= 0.4 else 2
    if bright is not None and bright < 55:
        issues.append("dark")
        bad += 1
    if sharp is not None and sharp < 40:
        issues.append("blurry")
        bad += 1
    grade = "good" if bad == 0 else "fair" if bad <= 1 else "poor"
    return {"grade": grade, "issues": issues,
            "person_px": round(person_px) if person_px else None,
            "hands_visible": round(hands, 2) if hands is not None else None,
            "people_frames": round(with_people / len(frames), 2) if frames else 0,
            "brightness": round(bright) if bright is not None else None,
            "sharpness": round(sharp) if sharp is not None else None,
            "resolution": f"{info.get('width')}x{info.get('height')}", "fps": info.get("fps")}


# ---------- step 2: replay the theft rules (fast) ----------
def replay(extracted: dict, labels: dict, det_cfg: dict) -> list[Detection]:
    w, h = extracted["size"]
    to_px = lambda polys: [np.array([[x * w, y * h] for x, y in p], np.float32) for p in polys]
    scenes = [(s["start"], to_px(s.get("shelf", [])), to_px(s.get("ignore", []))) for s in scenes_of(labels)]
    si = 0
    _, shelf, ignore = scenes[0]
    det = ConcealmentDetector(det_cfg)
    out: list[Detection] = []
    for t, persons, items, bags in extracted["frames"]:
        if si + 1 < len(scenes) and t >= scenes[si + 1][0]:
            while si + 1 < len(scenes) and t >= scenes[si + 1][0]:
                si += 1
            _, shelf, ignore = scenes[si]
            det = ConcealmentDetector(det_cfg)  # new angle = new camera: forget everyone being tracked
        busy = det.busy()  # same gating as live: bags/items only while someone is mid-reach
        ps = [Person(tid, box, k) for tid, box, k in persons]
        out += det.update(t, ps, items if busy else [], bags if busy else [], shelf, ignore)
    return out


# ---------- step 3: compare with labels ----------
def match(dets: list[Detection], events: list[dict], before=4.0, after=4.0):
    """Pair each labeled theft with at most one alert close to it in time."""
    used, pairs, missed = set(), [], []
    for e in sorted(events, key=lambda e: e["t"]):
        best = None
        for i, d in enumerate(dets):
            if i in used or not (e["t"] - before <= d.ts <= e["t"] + after):
                continue
            if best is None or abs(d.ts - e["t"]) < abs(dets[best].ts - e["t"]):
                best = i
        if best is None:
            missed.append(e)
        else:
            used.add(best)
            pairs.append((e, dets[best]))
    false_alarms = [d for i, d in enumerate(dets) if i not in used]
    return pairs, missed, false_alarms


def summarize(per_video: list[dict]) -> dict:
    events = sum(v["events"] for v in per_video)
    caught = sum(v["caught"] for v in per_video)
    fa = sum(v["false_alarms"] for v in per_video)
    hours = sum(v["seconds"] for v in per_video) / 3600
    by_type: dict[str, list[int]] = {}
    for v in per_video:
        for t, (c, n) in v["by_type"].items():
            agg = by_type.setdefault(t, [0, 0])
            agg[0] += c
            agg[1] += n
    return {
        "videos": len(per_video),
        "hours": round(hours, 2),
        "events": events,
        "caught": caught,
        "catch_rate": round(caught / events, 3) if events else None,
        "false_alarms": fa,
        "false_alarms_per_hour": round(fa / hours, 2) if hours else None,
        "precision": round(caught / (caught + fa), 3) if caught + fa else None,
        "by_type": {t: {"caught": c, "events": n} for t, (c, n) in by_type.items()},
        "by_quality": {g: {"videos": len(vs), "caught": sum(v["caught"] for v in vs),
                           "events": sum(v["events"] for v in vs),
                           "false_alarms": sum(v["false_alarms"] for v in vs)}
                       for g in ("good", "fair", "poor")
                       if (vs := [v for v in per_video if v.get("quality", {}).get("grade") == g])},
    }


def score_video(name: str, extracted: dict, labels: dict, det_cfg: dict, tol: float) -> dict:
    dets = replay(extracted, labels, det_cfg)
    pairs, missed, fas = match(dets, labels["events"], tol, tol)
    by_type: dict[str, list[int]] = {}
    for e in labels["events"]:
        by_type.setdefault(e["type"], [0, 0])[1] += 1
    for e, _ in pairs:
        by_type[e["type"]][0] += 1
    matched = {id(d) for _, d in pairs}
    return {
        "name": name,
        "seconds": round(extracted["seconds"], 1),
        "events": len(labels["events"]),
        "caught": len(pairs),
        "false_alarms": len(fas),
        "by_type": by_type,
        "detections": [{**{k: v for k, v in asdict(d).items() if k != "box"},
                        "ts": round(d.ts, 2), "reach_ts": round(d.reach_ts, 2),
                        "matched": id(d) in matched} for d in dets],
        "missed": missed,
    }


def sweep(cached: list[tuple[str, dict, dict]], det_cfg: dict, tol: float,
          thresholds=(0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9)) -> list[dict]:
    rows = []
    for th in thresholds:
        cfg = {**det_cfg, "alert_threshold": th}
        s = summarize([score_video(n, ex, lb, cfg, tol) for n, ex, lb in cached])
        rows.append({"threshold": th, **{k: s[k] for k in
                     ("catch_rate", "false_alarms", "false_alarms_per_hour", "precision")}})
    return rows
