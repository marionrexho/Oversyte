"""Give the test videos short team names (video-01.mp4, video-02.mp4, ...) without losing work.

Labels, calibration labels, the AI cache and the calibration list are matched to videos by
file name, so this renames all of them together. The old -> new names are kept in
eval/video_names.json, which is shared through git, so everyone ends up with the same names.

    python tools/rename_videos.py                  # show what would be renamed (changes nothing)
    python tools/rename_videos.py --apply          # rename your copies to the shared names
    python tools/rename_videos.py --assign-new --apply
        # project owner only: also give the next numbers to new videos in eval/videos,
        # then commit eval/video_names.json and eval/labels, and upload the videos

Developers: commit and push your labels, pull, then run with --apply.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from oversyte import evaluation as ev  # noqa: E402
from oversyte.config import load_config  # noqa: E402

TEAM_NAME = re.compile(r"^video-\d+\.[A-Za-z0-9]+$")


def load_map(eval_dir: Path) -> dict:
    p = eval_dir / "video_names.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_map(eval_dir: Path, mapping: dict):
    (eval_dir / "video_names.json").write_text(json.dumps(mapping, indent=1) + "\n", encoding="utf-8")


def assign_new(videos: list[Path], mapping: dict) -> dict:
    """Next free number for every video that has no team name yet (in name order)."""
    taken = set(mapping.values()) | {v.name for v in videos if TEAM_NAME.match(v.name)}
    nums = [int(re.search(r"\d+", n).group()) for n in taken if TEAM_NAME.match(n)]
    nxt = max(nums, default=0) + 1
    out = dict(mapping)
    for v in videos:
        if v.name in out or TEAM_NAME.match(v.name):
            continue
        out[v.name] = f"video-{nxt:02d}{v.suffix.lower()}"
        nxt += 1
    return out


def plan(eval_dir: Path, mapping: dict, cfg: dict | None) -> list[tuple[Path, Path]]:
    """(old path, new path) for every file that should move."""
    d = ev.dirs(eval_dir)
    moves = []
    for old, new in mapping.items():
        video = d["videos"] / old
        moves.append((video, d["videos"] / new))
        moves.append((d["labels"] / f"{old}.json", d["labels"] / f"{new}.json"))
        for person in [p for p in (eval_dir / "calibration").glob("*") if p.is_dir()]:
            moves.append((person / f"{old}.json", person / f"{new}.json"))
        for suffix in (".cuts.json", ".preview.mp4"):
            moves.append((d["cache"] / f"{old}{suffix}", d["cache"] / f"{new}{suffix}"))
        # AI cache: its key includes the file name, so re-key the one for the current settings
        if cfg and video.exists():
            key_old = ev._cache_key(video, cfg["detection"], cfg["clips"])
            key_new = ev._cache_key(video, cfg["detection"], cfg["clips"], name=new)
            moves.append((d["cache"] / f"{old}.{key_old}.pkl", d["cache"] / f"{new}.{key_new}.pkl"))
    return [(a, b) for a, b in moves if a.exists()]


def rewrite_text(path: Path, mapping: dict) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    new = text
    for old, name in mapping.items():
        new = new.replace(old, name)
    if new != text:
        path.write_text(new, encoding="utf-8")
    return new != text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually rename (default: only show the plan)")
    ap.add_argument("--assign-new", action="store_true", help="owner only: number videos that have no team name yet")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = ap.parse_args()

    cfg_path = Path(args.config) if Path(args.config).exists() else ROOT / "config.example.yaml"
    cfg = load_config(str(cfg_path))
    eval_dir = cfg["eval_dir"]
    mapping = load_map(eval_dir)
    if args.assign_new:
        mapping = assign_new(ev.list_videos(eval_dir), mapping)
    if not mapping:
        sys.exit("No eval/video_names.json yet. Pull the latest code, or (owner) run with --assign-new.")

    clashes = [new for old, new in mapping.items()
               if (eval_dir / "videos" / old).exists() and (eval_dir / "videos" / new).exists()]
    if clashes:
        sys.exit(f"Stopped: both the old and the new file exist for {', '.join(clashes)}. Check them by hand.")

    moves = plan(eval_dir, mapping, cfg)
    for old, new in sorted(mapping.items(), key=lambda kv: kv[1]):
        if (eval_dir / "videos" / new).exists():
            status = "already renamed"
        elif (eval_dir / "videos" / old).exists():
            status = f"<- {old}"
        else:
            status = "(you don't have this video)"
        print(f"{new:16} {status}")
    print(f"\n{len(moves)} file(s) to rename (videos, labels, calibration labels, AI cache).")
    if not args.apply:
        print("Nothing changed. Run again with --apply to rename.")
        return

    for old, new in moves:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    edited = [p.relative_to(ROOT) for p in (eval_dir / "calibration" / "videos.txt", eval_dir / "results.json")
              if rewrite_text(p, mapping)]
    if args.assign_new:
        save_map(eval_dir, mapping)
    print(f"Renamed {len(moves)} file(s)." + (f" Updated {', '.join(map(str, edited))}." if edited else ""))
    if args.assign_new:
        print("Now commit eval/video_names.json, eval/labels and eval/calibration, and upload eval/videos.")


if __name__ == "__main__":
    main()
