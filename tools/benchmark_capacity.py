"""How many cameras can one machine watch? Runs N fake cameras and measures if they keep up.

    python tools/benchmark_capacity.py                       # 1, 2, 4, 8, ... cameras until it can't keep up
    python tools/benchmark_capacity.py --cams 10,20,30,40    # pick the steps yourself
    python tools/benchmark_capacity.py --device 0            # use the NVIDIA GPU (on the cloud server)

Each fake camera is a test video from eval/videos played on a loop in real time, through the
same capture code (camera.py), AI (detector.py) and theft rules (concealment.py) that a live
camera uses. A camera "keeps up" when it gets at least 90% of analysis_fps frames analysed.
The answer is the largest step where every camera kept up.
"""
import argparse
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from oversyte.camera import Camera  # noqa: E402
from oversyte.concealment import ConcealmentDetector  # noqa: E402
from oversyte.config import load_config  # noqa: E402
from oversyte.detector import FrameAnalyzer  # noqa: E402

KEEP_UP = 0.9  # a camera keeps up at >= 90% of the target analysis rate


class FakeCameraWorker(threading.Thread):
    """Same loop as engine.CameraWorker, minus alerts/clips, counting analysed frames."""

    def __init__(self, cam: Camera, det_cfg: dict, object_model, fps: float):
        super().__init__(daemon=True)
        self.cam, self.det_cfg, self.object_model = cam, det_cfg, object_model
        self.interval = 1.0 / fps
        self.detector = ConcealmentDetector(det_cfg)
        self.ready = threading.Event()
        self.stop_ev = threading.Event()
        self.count = 0
        self.busy_s = 0.0

    def run(self):
        analyzer = FrameAnalyzer(self.det_cfg, self.object_model)
        self.ready.set()
        last_ts = 0.0
        while not self.stop_ev.is_set():
            t0 = time.time()
            ts, frame = self.cam.latest()
            if frame is None or ts == last_ts:
                self.stop_ev.wait(0.01)
                continue
            last_ts = ts
            persons, items, bags = analyzer.analyze(frame, need_objects=self.detector.busy())
            self.detector.update(ts, persons, items, bags, [], [])
            self.count += 1
            self.busy_s += time.time() - t0
            self.stop_ev.wait(max(0.0, self.interval - (time.time() - t0)))


def gpu_util() -> str:
    if not shutil.which("nvidia-smi"):
        return "-"
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        util, mem = out.stdout.strip().splitlines()[0].split(",")
        return f"{util.strip()}% / {int(mem)} MB"
    except Exception:
        return "?"


def run_step(n: int, videos: list[Path], cfg: dict, object_model, fps: float,
             warmup: float, measure: float) -> dict:
    cams, workers = [], []
    for i in range(n):
        cam = Camera({"id": f"bench{i}", "name": f"bench{i}", "source": str(videos[i % len(videos)]),
                      "loop": True}, cfg["clips"])
        cam.start()
        w = FakeCameraWorker(cam, cfg["detection"], object_model, fps)
        w.start()
        cams.append(cam)
        workers.append(w)
    for w in workers:
        w.ready.wait(120)
    time.sleep(warmup)
    start = [(w.count, w.busy_s) for w in workers]
    t0 = time.time()
    gpu = []
    while time.time() - t0 < measure:
        time.sleep(min(5, measure))
        gpu.append(gpu_util())
    elapsed = time.time() - t0
    rates, ms = [], []
    for w, (c0, b0) in zip(workers, start):
        frames = w.count - c0
        rates.append(frames / elapsed)
        ms.append((w.busy_s - b0) / frames * 1000 if frames else float("inf"))
    for w in workers:
        w.stop_ev.set()
    for c in cams:
        c.stop()
    for w in workers:
        w.join(5)
    time.sleep(1)
    return {"n": n, "min": min(rates), "avg": sum(rates) / n, "ms": sum(ms) / n,
            "gpu": gpu[len(gpu) // 2] if gpu else "-",
            "ok": min(rates) >= KEEP_UP * fps}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--cams", help="comma-separated camera counts, e.g. 5,10,20 (default: doubling until it fails)")
    ap.add_argument("--max", type=int, default=128, help="stop doubling at this many cameras")
    ap.add_argument("--device", help="cpu, or 0 for the first NVIDIA GPU (default: from config)")
    ap.add_argument("--imgsz", type=int)
    ap.add_argument("--fps", type=float, help="analysis_fps per camera (default: from config)")
    ap.add_argument("--warmup", type=float, default=10)
    ap.add_argument("--seconds", type=float, default=30, help="measuring time per step")
    args = ap.parse_args()

    cfg = load_config(args.config)
    det = cfg["detection"]
    if args.device is not None:
        det["device"] = args.device
    if args.imgsz:
        det["imgsz"] = args.imgsz
    fps = args.fps or float(det["analysis_fps"])
    videos = sorted((ROOT / cfg.get("eval_dir", "eval") / "videos").glob("video-*.mp4"))
    if not videos:
        sys.exit("No test videos in eval/videos. Run: python tools/fetch_videos.py")

    object_model = FrameAnalyzer.load_object_model(det)
    steps = [int(x) for x in args.cams.split(",")] if args.cams else None
    print(f"device={det['device']}  imgsz={det['imgsz']}  target={fps:g} frames/s per camera  "
          f"videos={len(videos)}  {args.seconds:g}s per step\n")
    print(f"{'cameras':>7}  {'slowest cam':>11}  {'average':>8}  {'ms/frame':>8}  {'GPU use / mem':>15}  result")

    best, n, results = 0, 1, []
    while True:
        if steps is not None:
            if not steps:
                break
            n = steps.pop(0)
        r = run_step(n, videos, cfg, object_model, fps, args.warmup, args.seconds)
        results.append(r)
        print(f"{r['n']:>7}  {r['min']:>9.1f}/s  {r['avg']:>6.1f}/s  {r['ms']:>8.0f}  {r['gpu']:>15}  "
              f"{'keeps up' if r['ok'] else 'TOO SLOW'}", flush=True)
        if r["ok"]:
            best = max(best, n)
        elif steps is None:
            break
        if steps is None:
            if n >= args.max:
                break
            n *= 2

    print()
    if best:
        print(f"This machine kept up with {best} camera(s) at {fps:g} frames/s each.")
        if steps is None and not results[-1]["ok"]:
            print(f"The limit is between {best} and {results[-1]['n']}; narrow it with --cams.")
        print("Plan on about 80% of that in production, to leave headroom for busy moments.")
    else:
        print("Even the smallest step could not keep up.")


if __name__ == "__main__":
    main()
