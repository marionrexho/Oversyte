"""Calibration: see where two people labeled the same videos differently.

Step 1. Each person labels the same calibration videos in the Accuracy lab, then runs:
            python tools/compare_labels.py --save YOURNAME
        This MOVES your finished labels into eval/calibration/YOURNAME/ (so they don't
        clash in git with anyone else's), then commit that folder.

Step 2. Anyone compares two people:
            python tools/compare_labels.py alex sam

It lists thefts only one person marked, thefts marked at different moments or with
different types, and how much your shelf zones overlap. Every difference is a rule
that needs to be clearer in LABELING_GUIDE.md.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from oversyte.evaluation import EVENT_TYPES, scenes_of  # noqa: E402

LABELS = ROOT / "eval" / "labels"
CALIB = ROOT / "eval" / "calibration"
MATCH_SECONDS = 2.0   # same theft if marked within this many seconds
TIMING_OK = 1.0       # closer than this counts as the same moment


def save(name: str) -> int:
    dest = CALIB / name
    dest.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in sorted(LABELS.glob("*.json")):
        if json.loads(f.read_text(encoding="utf-8")).get("done"):
            shutil.move(str(f), dest / f.name)
            moved.append(f.stem)
    if not moved:
        print("No finished labels found. Tick 'Finished labeling this video' first.")
        return 1
    shown = dest.relative_to(ROOT) if dest.is_relative_to(ROOT) else dest
    print(f"Moved {len(moved)} finished label file(s) to {shown}:")
    for m in moved:
        print("  ", m)
    print(f"\nNow commit them:  git add eval/calibration/{name}  &&  git commit -m \"Calibration labels: {name}\"")
    return 0


def zone_mask(polys, size=240):
    m = np.zeros((size, size), np.uint8)
    for p in polys:
        cv2.fillPoly(m, [np.array([[x * size, y * size] for x, y in p], np.int32)], 1)
    return m


def zone_overlap(a_scenes, b_scenes):
    """Average overlap (0-100%) of shelf zones, angle by angle (matched by start time)."""
    scores = []
    for sa in a_scenes:
        sb = min(b_scenes, key=lambda s: abs(s["start"] - sa["start"]))
        if abs(sb["start"] - sa["start"]) > 2:
            continue
        ma, mb = zone_mask(sa["shelf"]), zone_mask(sb["shelf"])
        union = (ma | mb).sum()
        if union:
            scores.append((ma & mb).sum() / union)
    return round(100 * float(np.mean(scores))) if scores else None


def fmt(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def compare(a: str, b: str) -> int:
    da, db = CALIB / a, CALIB / b
    for d in (da, db):
        if not d.is_dir():
            print(f"No calibration labels for '{d.name}'. They need to run:  python tools/compare_labels.py --save {d.name}")
            return 1
    common = sorted({f.name for f in da.glob("*.json")} & {f.name for f in db.glob("*.json")})
    if not common:
        print(f"{a} and {b} have no videos in common to compare.")
        return 1

    total = agree = 0
    for name in common:
        la = json.loads((da / name).read_text(encoding="utf-8"))
        lb = json.loads((db / name).read_text(encoding="utf-8"))
        ea, eb = sorted(la["events"], key=lambda e: e["t"]), sorted(lb["events"], key=lambda e: e["t"])
        used, pairs = set(), []
        for x in ea:
            cands = [(abs(y["t"] - x["t"]), j) for j, y in enumerate(eb) if j not in used and abs(y["t"] - x["t"]) <= MATCH_SECONDS]
            if cands:
                _, j = min(cands)
                used.add(j)
                pairs.append((x, eb[j]))
        only_a = [x for x in ea if all(x is not p[0] for p in pairs)]
        only_b = [y for j, y in enumerate(eb) if j not in used]

        sa, sb = scenes_of(la), scenes_of(lb)
        overlap = zone_overlap(sa, sb)
        print(f"\n=== {name.removesuffix('.json')}")
        print(f"  thefts marked: {a} {len(ea)}, {b} {len(eb)}   angles: {a} {len(sa)}, {b} {len(sb)}   "
              f"shelf-zone overlap: {str(overlap) + '%' if overlap is not None else '-'}")
        for x, y in pairs:
            same_type, close = x["type"] == y["type"], abs(x["t"] - y["t"]) <= TIMING_OK
            total += 1
            agree += same_type and close
            if same_type and close:
                print(f"  OK       {fmt(x['t'])}  both: {EVENT_TYPES.get(x['type'], x['type'])}")
            else:
                why = []
                if not close:
                    why.append(f"{a} at {fmt(x['t'])}, {b} at {fmt(y['t'])}")
                if not same_type:
                    why.append(f"{a} says '{EVENT_TYPES.get(x['type'])}', {b} says '{EVENT_TYPES.get(y['type'])}'")
                print(f"  DIFFER   {'; '.join(why)}")
        for x in only_a:
            total += 1
            print(f"  ONLY {a}: {fmt(x['t'])} {EVENT_TYPES.get(x['type'], x['type'])}")
        for y in only_b:
            total += 1
            print(f"  ONLY {b}: {fmt(y['t'])} {EVENT_TYPES.get(y['type'], y['type'])}")
        if overlap is not None and overlap < 60:
            print(f"  ZONES: only {overlap}% overlap. Compare the shapes side by side and agree on what to outline.")

    pct = round(100 * agree / total) if total else 100
    print(f"\nAgreement: {agree} of {total} thefts marked the same way ({pct}%).")
    print("Aim for 90%+ before splitting up the videos. Discuss every DIFFER / ONLY line and update the guide.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="two labeler names to compare")
    ap.add_argument("--save", metavar="NAME", help="move your finished labels into eval/calibration/NAME")
    args = ap.parse_args()
    if args.save:
        return save(args.save)
    if len(args.names) != 2:
        ap.print_help()
        return 1
    return compare(*args.names)


if __name__ == "__main__":
    sys.exit(main())
