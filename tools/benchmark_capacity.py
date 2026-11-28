"""How many cameras can one machine watch? Runs N fake cameras and measures if they keep up.

    python tools/benchmark_capacity.py                       # 1, 2, 4, 8, ... cameras until it can't keep up
    python tools/benchmark_capacity.py --cams 10,20,30,40    # pick the steps yourself
    python tools/benchmark_capacity.py --device 0            # use the NVIDIA GPU (on the cloud server)
    python tools/benchmark_capacity.py --procs 2             # split the cameras over 2 programs
    python tools/benchmark_capacity.py --mode per-camera      # the old way (one AI call per camera), to compare

Each fake camera is a test video from eval/videos played on a loop in real time, through the
same capture code (camera.py), AI (analysis.py: frames from all cameras batched on the GPU) and
theft rules (concealment.py) that a live camera uses. Cameras analyse at analysis_fps while
someone is in view and at idle_analysis_fps when the view is empty. A camera "keeps up"
("slowest cam" >= 90%) when it gets at least 90% of the frames it asked for. The answer is the
largest step where every camera kept up.

The test videos have people in view most of the time, so this is close to a busy store's worst
case; real cameras spend much of the day on empty aisles and cost less.
"""
import argparse
import multiprocessing as mp
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from oversyte.analysis import Scheduler, use_batching  # noqa: E402
from oversyte.camera import Camera  # noqa: E402
from oversyte.concealment import ConcealmentDetector  # noqa: E402
from oversyte.config import load_config  # noqa: E402
from oversyte.detector import FrameAnalyzer  # noqa: E402

KEEP_UP = 0.9  # a camera keeps up at >= 90% of the target analysis rate


class BenchCam:
    """A fake camera with the theft rules attached, counting what it gets analysed."""

    def __init__(self, cam: Camera, det_cfg: dict, fps: float):
        self.camera, self.id = cam, cam.id
        self.interval = 1.0 / fps
        self.idle_interval = 1.0 / min(float(det_cfg.get("idle_analysis_fps") or fps), fps)
        self.detector = ConcealmentDetector(det_cfg)
        self.last_analysis = self.last_ts = 0.0
        self.active = True
        self.count = self.busy_frames = 0
        self.ms_total = 0.0

    def need_objects(self) -> bool:
        return self.detector.busy()

    def handle(self, ts, persons, items, bags, ms):
        self.detector.update(ts, persons, items, bags, [], [])
        self.count += 1
        self.ms_total += ms
        self.active = bool(persons) or self.detector.busy()
        self.busy_frames += self.active


class PerCameraThread(threading.Thread):
    """The previous design: every camera calls the AI on its own."""

    def __init__(self, bc: BenchCam, det_cfg: dict, object_model):
        super().__init__(daemon=True)
        self.bc, self.det_cfg, self.object_model = bc, det_cfg, object_model
        self.ready, self.stop_ev = threading.Event(), threading.Event()

    def run(self):
        analyzer = FrameAnalyzer(self.det_cfg, self.object_model)
        self.ready.set()
        bc = self.bc
        while not self.stop_ev.is_set():
            t0 = time.time()
            ts, frame = bc.camera.latest()
            if frame is None or ts == bc.last_ts:
                self.stop_ev.wait(0.01)
                continue
            bc.last_ts = ts
            persons, items, bags = analyzer.analyze(frame, need_objects=bc.need_objects())
            bc.handle(ts, persons, items, bags, (time.time() - t0) * 1000)
            interval = bc.interval if bc.active else bc.idle_interval
            self.stop_ev.wait(max(0.0, interval - (time.time() - t0)))


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


def run_cams(n: int, first: int, videos: list, cfg: dict, fps: float, warmup: float, measure: float,
             mode: str, barrier=None) -> list[dict]:
    """Run n fake cameras in this process; return per-camera results for the measuring window."""
    det = cfg["detection"]
    object_model = FrameAnalyzer.load_object_model(det)
    cams = []
    for i in range(first, first + n):
        cam = Camera({"id": f"bench{i}", "name": f"bench{i}", "source": str(videos[i % len(videos)]),
                      "loop": True}, cfg["clips"], analysis_fps=fps)
        cam.start()
        cams.append(BenchCam(cam, det, fps))
    if mode == "batched":
        runners = [Scheduler(det, lambda: cams, object_model)]
    else:
        runners = [PerCameraThread(bc, det, object_model) for bc in cams]
    for r in runners:
        r.start()
    for r in runners:
        r.ready.wait(300)
    if barrier is not None:
        barrier.wait()  # all programs start measuring together
    time.sleep(warmup)
    start = [(c.count, c.ms_total, c.busy_frames) for c in cams]
    t0 = time.time()
    time.sleep(measure / 2)
    gpu = gpu_util()  # GPU use in the middle of the measuring window
    time.sleep(max(0.0, measure - (time.time() - t0)))
    elapsed = time.time() - t0
    out = []
    for c, (c0, m0, a0) in zip(cams, start):
        frames, active = c.count - c0, c.busy_frames - a0
        # keeps up when its frames cover the window at the rate it asked for
        covered = active * c.interval + (frames - active) * c.idle_interval
        out.append({"kept": covered / elapsed, "rate": frames / elapsed,
                    "ms": (c.ms_total - m0) / frames if frames else float("inf"),
                    "people": active / frames if frames else 0.0, "gpu": gpu})
    for r in runners:
        (r.stop if hasattr(r, "stop") else r.stop_ev.set)()
    for c in cams:
        c.camera.stop()
    time.sleep(1)
    return out


def _child(q, *args):
    q.put(run_cams(*args))


def run_step(n: int, procs: int, videos: list, cfg: dict, fps: float, warmup: float, measure: float,
             mode: str) -> dict:
    procs = max(1, min(procs, n))
    if procs == 1:
        per_cam = run_cams(n, 0, videos, cfg, fps, warmup, measure, mode)
    else:
        ctx = mp.get_context("spawn")
        q, barrier = ctx.Queue(), ctx.Barrier(procs)
        shares = [n // procs + (1 if i < n % procs else 0) for i in range(procs)]
        ps = [ctx.Process(target=_child, args=(q, s, sum(shares[:i]), videos, cfg, fps, warmup, measure,
                                               mode, barrier)) for i, s in enumerate(shares)]
        for p in ps:
            p.start()
        per_cam = [c for _ in ps for c in q.get(timeout=warmup + measure + 900)]
        for p in ps:
            p.join(30)
    kept = min(c["kept"] for c in per_cam)
    return {"n": n, "kept": kept, "avg": sum(c["rate"] for c in per_cam) / n,
            "ms": sum(c["ms"] for c in per_cam) / n, "people": sum(c["people"] for c in per_cam) / n,
            "gpu": per_cam[0]["gpu"], "ok": kept >= KEEP_UP}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--cams", help="comma-separated camera counts, e.g. 5,10,20 (default: doubling until it fails)")
    ap.add_argument("--max", type=int, default=128, help="stop doubling at this many cameras")
    ap.add_argument("--device", help="cpu, or 0 for the first NVIDIA GPU (default: from config)")
    ap.add_argument("--imgsz", type=int)
    ap.add_argument("--fps", type=float, help="analysis_fps per camera (default: from config)")
    ap.add_argument("--procs", type=int, default=1, help="split the cameras over this many programs")
    ap.add_argument("--mode", choices=("auto", "batched", "per-camera"), default="auto",
                    help="auto = what Oversyte itself does: batched on a GPU, per-camera on a CPU")
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
    if args.mode == "auto":
        args.mode = "batched" if use_batching(det) else "per-camera"
    videos = sorted((Path(cfg["eval_dir"]) / "videos").glob("video-*.mp4"))
    if not videos:
        sys.exit("No test videos in eval/videos. Run: python tools/fetch_videos.py")

    steps = [int(x) for x in args.cams.split(",")] if args.cams else None
    print(f"device={det['device']}  imgsz={det['imgsz']}  target={fps:g} frames/s per camera  "
          f"mode={args.mode}  programs={args.procs}  videos={len(videos)}  {args.seconds:g}s per step\n")
    print(f"{'cameras':>7}  {'slowest cam':>11}  {'frames/s':>8}  {'ms/frame':>8}  {'people in view':>14}  "
          f"{'GPU use / mem':>15}  result")

    best, n, results = 0, 1, []
    while True:
        if steps is not None:
            if not steps:
                break
            n = steps.pop(0)
        r = run_step(n, args.procs, videos, cfg, fps, args.warmup, args.seconds, args.mode)
        results.append(r)
        print(f"{r['n']:>7}  {r['kept'] * 100:>10.0f}%  {r['avg']:>8.1f}  {r['ms']:>8.0f}  "
              f"{r['people'] * 100:>13.0f}%  {r['gpu']:>15}  "
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
