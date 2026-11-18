"""Build a static Oversyte demo site that can be hosted free, 24/7 (Vercel, Netlify, ...).

    .venv\\Scripts\\python deploy\\build_static_demo.py --password oversyte2026

The Oversyte AI runs ONCE, here, over the labeled test videos (reusing the eval cache).
Everything in the site is its real output: alerts, clips, suspect boxes, camera overlays.
Nothing is analysed live on the hosted site; camera views are recorded AI output on a loop.

Output: site/   (deploy this folder)
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from oversyte import evaluation as ev  # noqa: E402
from oversyte.clips import write_clip  # noqa: E402
from oversyte.concealment import ConcealmentDetector, Person  # noqa: E402
from oversyte.config import load_config  # noqa: E402

SITE = ROOT / "site" / "oversyte-demo"  # folder name becomes the Vercel project name
NAMES = ["Aisle 1", "Snacks & coolers", "Front counter", "Coolers", "Aisle 2", "Register",
         "Candy rack", "Back aisle", "Entrance", "Drinks", "Aisle 3", "Chips", "Hot food", "Aisle 4", "Side door"]
PHASES = ["idle", "reaching", "carrying", "concealing"]
COLORS = {"idle": (200, 200, 200), "reaching": (0, 200, 255), "carrying": (0, 140, 255), "concealing": (40, 40, 235)}
LIVE_SECONDS, FPS = 60, 12


def replay(ex, labels, det_cfg):
    """The theft rules over cached AI output; returns detections and per-frame person states."""
    w, h = ex["size"]
    to_px = lambda polys: [np.array([[x * w, y * h] for x, y in p], np.float32) for p in polys]
    scenes = [(s["start"], to_px(s["shelf"]), to_px(s["ignore"])) for s in ev.scenes_of(labels)]
    si, (_, shelf, ignore) = 0, scenes[0]
    det, dets, states = ConcealmentDetector(det_cfg), [], []
    for t, persons, items, bags in ex["frames"]:
        if si + 1 < len(scenes) and t >= scenes[si + 1][0]:
            while si + 1 < len(scenes) and t >= scenes[si + 1][0]:
                si += 1
            _, shelf, ignore = scenes[si]
            det = ConcealmentDetector(det_cfg)
        busy = det.busy()
        ps = [Person(tid, box, k) for tid, box, k in persons]
        dets += det.update(t, ps, items if busy else [], bags if busy else [], shelf, ignore)
        frame_state = {}
        for p in ps:
            st = det.tracks.get(p.track_id)
            phase = max((st.hands[x].phase for x in ("left", "right")), key=PHASES.index) if st else "idle"
            frame_state[p.track_id] = (p.box, phase)
        states.append((t, frame_state))
    return dets, states


def read_frames(path, t0, t1, max_w):
    """Frames between t0 and t1 seconds at FPS, resized like the AI saw them."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    cap.set(cv2.CAP_PROP_POS_MSEC, max(t0, 0) * 1000)
    out, idx, nxt = [], int(max(t0, 0) * fps), max(t0, 0)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = idx / fps
        idx += 1
        if t > t1:
            break
        if t + 1e-6 < nxt:
            continue
        nxt += 1 / FPS
        h, w = frame.shape[:2]
        if w > max_w:
            frame = cv2.resize(frame, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
        fh, fw = frame.shape[:2]
        out.append((t, frame[: fh - fh % 2, : fw - fw % 2]))
    cap.release()
    return out


def nearest(states, t):
    best = min(states, key=lambda s: abs(s[0] - t), default=None)
    return best[1] if best and abs(best[0] - t) < 0.6 else {}


def render_live(path, states, out, name, max_w):
    frames = read_frames(path, 0, LIVE_SECONDS, max_w)
    if not frames:
        return None
    h, w = frames[0][1].shape[:2]
    scale = 720 / w if w > 720 else 1.0
    W, H = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
    writer = imageio_ffmpeg.write_frames(str(out), (W, H), fps=FPS, codec="libx264", pix_fmt_out="yuv420p",
                                         quality=None, macro_block_size=2, ffmpeg_log_level="error",
                                         output_params=["-crf", "28", "-preset", "veryfast", "-movflags", "+faststart"])
    writer.send(None)
    peak = 0
    for t, frame in frames:
        st = nearest(states, t)
        peak = max(peak, len(st))
        for tid, (box, phase) in st.items():
            x1, y1, x2, y2 = (int(v) for v in box)
            c = COLORS[phase]
            cv2.rectangle(frame, (x1, y1), (x2, y2), c, 2)
            cv2.putText(frame, f"#{tid} {phase}", (x1 + 4, max(14, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
        frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA) if scale != 1 else frame
        writer.send(np.ascontiguousarray(frame[:H, :W, ::-1]))
    writer.close()
    return peak


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--password", required=True)
    ap.add_argument("--store", default="Demo Gas Station")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--live", nargs="*", default=["video-14", "video-02"], help="video name prefixes shown as live cameras")
    ap.add_argument("--max-alerts", type=int, default=10)
    args = ap.parse_args()

    cfg = load_config(args.config)
    det_cfg, eval_dir, max_w = cfg["detection"], cfg["eval_dir"], int(cfg["clips"]["max_width"])
    videos = ev.list_videos(eval_dir)
    if not videos:
        sys.exit(f"No videos in {eval_dir / 'videos'}")
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "media").mkdir(parents=True)

    object_model = None
    found, live = [], []
    for i, v in enumerate(videos):
        name = NAMES[i % len(NAMES)]
        print(f"[{i + 1}/{len(videos)}] {v.name[:12]} ({name}) … ", end="", flush=True)
        try:
            ex = ev.extract(v, eval_dir, det_cfg, cfg["clips"], object_model, use_cache=True)
        except Exception as e:  # noqa: BLE001
            print("skipped:", e)
            continue
        dets, states = replay(ex, ev.load_labels(eval_dir, v.name), det_cfg)
        print(f"{len(dets)} alert(s)")
        found += [(v, name, d, states) for d in dets]
        if any(v.name.startswith(p) for p in args.live):
            out = SITE / "media" / f"live-{len(live) + 1}.mp4"
            peak = render_live(v, states, out, name, max_w)
            if peak is not None:
                live.append({"id": f"cam{len(live) + 1}", "name": name, "video_url": f"/media/{out.name}",
                             "people": min(peak, 3), "analysis_ms": 120})

    # strongest alerts first, at most 3 per video, then spread them over the last couple of days
    found.sort(key=lambda f: -f[2].score)
    picked, per_video = [], {}
    for f in found:
        if per_video.get(f[0].name, 0) < 3 and len(picked) < args.max_alerts:
            per_video[f[0].name] = per_video.get(f[0].name, 0) + 1
            picked.append(f)
    events = []
    for n, (v, name, d, states) in enumerate(picked):
        eid = f"demo{n + 1:02d}"
        frames = read_frames(v, d.ts - 6, d.ts + 3, max_w)
        jpgs = [(t, cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()) for t, f in frames]
        ann = [(t, {tid: box for tid, (box, _) in st.items()}) for t, st in states]
        ok = write_clip(jpgs, SITE / "media" / f"{eid}.mp4", SITE / "media" / f"{eid}.jpg", FPS, d.ts,
                        d.track_id, ann, name, stamp_time=False)
        if not ok:
            continue
        events.append({"id": eid, "camera_id": "-", "camera_name": name, "ago": int(n * 4.7 * 3600 + 25 * 60),
                       "score": d.score, "region": d.region, "reason": d.reason, "status": "new", "notes": "",
                       "is_test": 0, "clip_url": f"/media/{eid}.mp4", "thumb_url": f"/media/{eid}.jpg"})
        print(f"  clip {eid}: {name}, score {d.score:.2f}, {d.reason}")

    data = {"store_name": args.store, "password_sha256": hashlib.sha256(args.password.encode()).hexdigest(),
            "cameras": live, "events": events, "built_at": int(time.time())}
    (SITE / "demo-data.json").write_text(json.dumps(data, indent=1), encoding="utf-8")

    web = ROOT / "web"
    for p in web.rglob("*"):
        if p.is_file():
            dest = SITE / p.relative_to(web)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    v = int(time.time())
    html = (web / "index.html").read_text(encoding="utf-8")
    html = html.replace('"/app.js"', f'"/app.js?v={v}"').replace('"/styles.css"', f'"/styles.css?v={v}"')
    html = html.replace("<script src=", f'<script>window.OVERSYTE_DEMO = {{ data: "/demo-data.json?v={v}" }};</script>\n'
                                         f'  <script src="/demo.js?v={v}"></script>\n  <script src=', 1)
    (SITE / "index.html").write_text(html, encoding="utf-8")
    size = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file()) / 1e6
    print(f"\nBuilt {SITE}  ({len(live)} cameras, {len(events)} alerts, {size:.1f} MB)")


if __name__ == "__main__":
    main()
