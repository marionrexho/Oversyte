"""Turns buffered JPEG frames into a browser-playable H.264 MP4 plus a thumbnail."""
from __future__ import annotations

import bisect
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

RED = (40, 40, 235)


def _nearest(annotations: list[tuple[float, dict]], ts: float, max_gap=0.6):
    if not annotations:
        return None
    keys = [a[0] for a in annotations]
    i = bisect.bisect_left(keys, ts)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(annotations):
            if best is None or abs(annotations[j][0] - ts) < abs(best[0] - ts):
                best = annotations[j]
    return best if best and abs(best[0] - ts) <= max_gap else None


def _draw(frame: np.ndarray, box, label: str):
    x1, y1, x2, y2 = (int(v) for v in box)
    cv2.rectangle(frame, (x1, y1), (x2, y2), RED, 2)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
    y = max(y1, th + 8)
    cv2.rectangle(frame, (x1, y - th - 8), (x1 + tw + 10, y), RED, -1)
    cv2.putText(frame, label, (x1 + 5, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (255, 255, 255), 1, cv2.LINE_AA)


def _stamp(frame: np.ndarray, text: str):
    cv2.putText(frame, text, (10, frame.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(frame, text, (10, frame.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (255, 255, 255), 1, cv2.LINE_AA)


def write_clip(frames: list[tuple[float, bytes]], out_mp4: Path, out_thumb: Path, fps: float,
               event_ts: float, track_id: int | None,
               annotations: list[tuple[float, dict]], camera_name: str, stamp_time: bool = True) -> bool:
    """frames: [(ts, jpeg)], annotations: [(ts, {track_id: box})] sorted by ts."""
    if not frames:
        return False
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    out_thumb.parent.mkdir(parents=True, exist_ok=True)
    import time as _t

    first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
    h, w = first.shape[:2]
    writer = imageio_ffmpeg.write_frames(
        str(out_mp4), (w, h), fps=fps, codec="libx264", pix_fmt_in="rgb24",
        pix_fmt_out="yuv420p", quality=None, macro_block_size=2,
        output_params=["-crf", "26", "-preset", "veryfast", "-movflags", "+faststart"],
        ffmpeg_log_level="error",
    )
    writer.send(None)
    thumb, thumb_gap = None, 1e9
    try:
        for ts, jpg in frames:
            frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None or frame.shape[:2] != (h, w):
                continue
            ann = _nearest(annotations, ts) if track_id is not None else None
            if ann and track_id in ann[1]:
                _draw(frame, ann[1][track_id], "Possible concealment" if ts >= event_ts else "Suspect")
            _stamp(frame, f"{camera_name}  {_t.strftime('%Y-%m-%d %H:%M:%S', _t.localtime(ts))}" if stamp_time else camera_name)
            gap = abs(ts - event_ts)
            if gap < thumb_gap:
                thumb, thumb_gap = frame.copy(), gap
            writer.send(np.ascontiguousarray(frame[:, :, ::-1]))
    finally:
        writer.close()
    if thumb is not None:
        cv2.imwrite(str(out_thumb), thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return True
