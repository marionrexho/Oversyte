"""Score Oversyte on your labeled test videos.

    python eval.py --check          analyse every video and rate its quality (do this first,
                                    no labels needed; it also makes later scoring instant)
    python eval.py                  score every fully-labeled video in eval/videos
    python eval.py --threshold 0.7  try a different alert threshold
    python eval.py --pose-model models/yolo11s-pose.pt --imgsz 960
                                    try a bigger / sharper AI (slower, may help low-quality video)
    python eval.py --only clip3     only videos whose name contains "clip3"
    python eval.py --fresh          ignore the AI cache

Label videos in the dashboard: Settings -> Accuracy lab.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from oversyte import evaluation as ev
from oversyte.config import load_config


def pct(x):
    return "  -  " if x is None else f"{x * 100:4.0f}%"


def rate(caught, events):
    return f"{caught}/{events} ({caught / events * 100:.0f}%)" if events else "no thefts"


def load_results(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        return {}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--check", action="store_true", help="analyse + rate quality of ALL videos, no scoring")
    ap.add_argument("--threshold", type=float, help="alert threshold to test (default: from config)")
    ap.add_argument("--tolerance", type=float, default=4.0,
                    help="seconds an alert may be off from the labeled moment (default 4)")
    ap.add_argument("--pose-model", help="e.g. models/yolo11s-pose.pt (downloaded automatically)")
    ap.add_argument("--object-model", help="e.g. models/yolo11s.pt, or 'none'")
    ap.add_argument("--imgsz", type=int, help="AI input size, e.g. 960 for small/far people (slower)")
    ap.add_argument("--only", help="only videos whose file name contains this text")
    ap.add_argument("--fresh", action="store_true", help="re-run the AI instead of using the cache")
    args = ap.parse_args()

    cfg = load_config(args.config)
    root = Path(args.config).resolve().parent
    det_cfg = dict(cfg["detection"])
    if args.threshold is not None:
        det_cfg["alert_threshold"] = args.threshold
    if args.pose_model:
        det_cfg["pose_model"] = str((root / args.pose_model).resolve())
    if args.object_model:
        det_cfg["object_model"] = None if args.object_model.lower() == "none" else str((root / args.object_model).resolve())
    if args.imgsz:
        det_cfg["imgsz"] = args.imgsz
    eval_dir: Path = cfg["eval_dir"]
    results_path = eval_dir / "results.json"

    videos = [v for v in ev.list_videos(eval_dir) if not args.only or args.only in v.name]
    if not videos:
        print(f"No videos found. Put them in {eval_dir / 'videos'} (or upload in Settings -> Accuracy lab).")
        return 1
    labeled = [(v, ev.load_labels(eval_dir, v.name)) for v in videos]
    todo = labeled if args.check else [(v, lb) for v, lb in labeled if lb["done"]]
    skipped = [v.name for v, lb in labeled if not lb["done"]]
    if not todo:
        print("No fully-labeled videos yet. Label them in the dashboard (Settings -> Accuracy lab)")
        print("and tick 'Finished labeling this video'. Tip: run  python eval.py --check  first.")
        return 1

    setup = f"{Path(det_cfg['pose_model']).name} @ {det_cfg['imgsz']}px"
    if args.check:
        print(f"Checking {len(todo)} video(s) with {setup}")
    else:
        print(f"Scoring {len(todo)} video(s) with {setup}, alert threshold {det_cfg['alert_threshold']}"
              + (f"  ({len(skipped)} not finished labeling, skipped)" if skipped else ""))
    object_model = None
    if det_cfg.get("object_model"):
        from oversyte.detector import FrameAnalyzer
        object_model = FrameAnalyzer.load_object_model(det_cfg)

    cached, per_video, qualities = [], [], {}
    for i, (v, lb) in enumerate(todo, 1):
        label = f"[{i}/{len(todo)}] {v.name}"

        def progress(f, label=label):
            sys.stdout.write(f"\r{label}  analysing… {f * 100:3.0f}%")
            sys.stdout.flush()

        t0 = time.time()
        try:
            ex = ev.extract(v, eval_dir, det_cfg, cfg["clips"], object_model,
                            use_cache=not args.fresh, progress=progress)
        except Exception as e:  # unreadable file etc.: report it, keep going
            sys.stdout.write("\r" + " " * 90 + "\r")
            print(f"{label}  ERROR: {e}")
            continue
        q = ev.quality(ex, ev.video_info(v), int(det_cfg["imgsz"]))
        qualities[v.name] = q
        sys.stdout.write("\r" + " " * 90 + "\r")
        took = time.time() - t0
        if args.check:
            print(f"{label}  {q['grade'].upper():5s} {', '.join(q['issues']) or 'looks fine'}  ({took:.0f}s)")
            continue
        res = ev.score_video(v.name, ex, lb, det_cfg, args.tolerance)
        res["quality"] = q
        print(f"{label}  thefts {res['caught']}/{res['events']} caught, "
              f"{res['false_alarms']} false alarm(s)  [{q['grade']}]  ({took:.0f}s)")
        cached.append((v.name, ex, lb))
        per_video.append(res)

    if qualities:
        print("\n  VIDEO QUALITY (what the AI can see)")
        print("  " + "-" * 92)
        print(f"  {'video':34s} {'resolution':>11s} {'fps':>4s} {'person':>7s} {'hands':>6s}  grade  issues")
        for name, q in qualities.items():
            print(f"  {name[:34]:34s} {q['resolution']:>11s} {q['fps'] or '?':>4} "
                  f"{(str(q['person_px']) + 'px') if q['person_px'] else '-':>7s} "
                  f"{pct(q['hands_visible']):>6s}  {q['grade']:5s}  {', '.join(q['issues'])}")
        print("  person = typical height of a person as the AI sees it (under ~110px gets unreliable)")
        print("  hands  = how often at least one wrist is visible on a detected person")

    prev = load_results(results_path)
    prev.setdefault("quality", {}).update(qualities)
    if args.check:
        results_path.write_text(json.dumps(prev, indent=1), encoding="utf-8")
        n_bad = sum(q["grade"] == "poor" for q in qualities.values())
        print(f"\nDone. {n_bad} of {len(qualities)} video(s) rated poor. Quality now shows in the Accuracy lab.")
        print("Next: label the videos in the lab, then run  python eval.py")
        return 0
    if not per_video:
        return 1

    s = ev.summarize(per_video)
    mins = s["hours"] * 60
    footage = f"{mins:.0f} min" if mins >= 1 else f"{sum(v['seconds'] for v in per_video):.0f} sec"
    print("\n" + "=" * 60)
    print(f"  Videos:              {s['videos']}  ({footage} of footage)")
    print(f"  Thefts labeled:      {s['events']}")
    print(f"  CATCH RATE:          {pct(s['catch_rate'])}   ({s['caught']} of {s['events']} thefts alerted)")
    fph = s["false_alarms_per_hour"]
    print(f"  FALSE ALARMS:        {s['false_alarms']}  ({fph if fph is not None else '-'} per hour of footage)")
    print(f"  Alerts that were real: {pct(s['precision'])}")
    if s["by_type"]:
        print("\n  By theft type:")
        for t, r in s["by_type"].items():
            print(f"    {ev.EVENT_TYPES.get(t, t):28s} {rate(r['caught'], r['events'])}")
    if s["by_quality"]:
        print("\n  By video quality:")
        for g, r in s["by_quality"].items():
            print(f"    {g:6s} {r['videos']:3d} video(s)   caught {rate(r['caught'], r['events'])}, "
                  f"{r['false_alarms']} false alarm(s)")
    print("=" * 60)

    rows = ev.sweep(cached, det_cfg, args.tolerance)
    print("\n  If you changed alert_threshold:")
    print("    threshold   catch rate   false alarms   real alerts")
    for r in rows:
        mark = "  <- current" if abs(r["threshold"] - det_cfg["alert_threshold"]) < 1e-9 else ""
        print(f"      {r['threshold']:.2f}       {pct(r['catch_rate'])}        "
              f"{r['false_alarms']:5d}          {pct(r['precision'])}{mark}")

    missed = [(v["name"], m) for v in per_video for m in v["missed"]]
    fas = [(v["name"], d) for v in per_video for d in v["detections"] if not d["matched"]]
    if missed:
        print("\n  Missed thefts (open them in the Accuracy lab to see why):")
        for n, m in missed[:25]:
            print(f"    {n} @ {m['t']:.1f}s  ({ev.EVENT_TYPES.get(m['type'], m['type'])})")
    if fas:
        print("\n  False alarms:")
        for n, d in fas[:25]:
            print(f"    {n} @ {d['ts']:.1f}s  score {d['score']:.2f}  {d['reason']}")

    prev.update({"run_at": time.time(), "threshold": det_cfg["alert_threshold"], "setup": setup,
                 "tolerance": args.tolerance, "summary": s, "sweep": rows,
                 "videos": {v["name"]: v for v in per_video}})
    results_path.write_text(json.dumps(prev, indent=1), encoding="utf-8")
    print(f"\nSaved {results_path}. Open a video in the Accuracy lab to see its alerts on the timeline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
