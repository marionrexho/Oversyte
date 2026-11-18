"""Start the Oversyte pitch demo in the cloud (Hugging Face Space or any Docker host).

Settings come from environment variables (Space "secrets"):
  DEMO_PASSWORD   dashboard password                      (required)
  DEMO_DATASET    private dataset holding the demo videos, e.g. "yourname/oversyte-demo-videos"
  HF_TOKEN        read token that can access that private dataset
  DEMO_VIDEOS_DIR use videos from this folder instead of downloading (for local testing)
  DEMO_STORE      store name shown in the dashboard        (default "Demo Gas Station")
  PORT            port to listen on                        (default 7860)

Each video becomes one looping "live camera". Data is not kept between restarts.
"""
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "cloud-demo"
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
CAMERA_NAMES = ["Aisle 1", "Snacks & coolers", "Front counter", "Aisle 2"]


def get_videos() -> list[Path]:
    local = os.environ.get("DEMO_VIDEOS_DIR")
    if local:
        folder = Path(local)
    else:
        repo = os.environ.get("DEMO_DATASET")
        if not repo:
            sys.exit("Set DEMO_DATASET (the private dataset with the demo videos) or DEMO_VIDEOS_DIR.")
        from huggingface_hub import snapshot_download
        print(f"Downloading demo videos from {repo} …", flush=True)
        folder = Path(snapshot_download(repo_id=repo, repo_type="dataset", token=os.environ.get("HF_TOKEN"),
                                        local_dir=WORK / "videos"))
    videos = sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXTS)
    if not videos:
        sys.exit(f"No videos found in {folder}")
    return videos[:4]  # the free CPU handles a few cameras at most


def main():
    password = os.environ.get("DEMO_PASSWORD")
    if not password:
        sys.exit("Set the DEMO_PASSWORD secret.")
    videos = get_videos()
    WORK.mkdir(exist_ok=True)
    cfg = {
        "store_name": os.environ.get("DEMO_STORE", "Demo Gas Station"),
        "dashboard_password": password,
        "team_mode": False,
        "data_dir": str(WORK / "data"),
        "eval_dir": str(WORK / "eval"),
        "server": {"host": "0.0.0.0", "port": int(os.environ.get("PORT", 7860))},
        "detection": {"pose_model": str(ROOT / "models" / "yolo11n-pose.pt"),
                      "object_model": str(ROOT / "models" / "yolo11n.pt"),
                      "analysis_fps": 3 if len(videos) > 1 else 4},
        "clips": {"max_width": 800},
        "cameras": [{"id": f"cam{i + 1}", "name": CAMERA_NAMES[i % len(CAMERA_NAMES)],
                     "source": str(v), "loop": True} for i, v in enumerate(videos)],
    }
    config_path = WORK / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    print(f"Demo: {len(videos)} camera(s), store '{cfg['store_name']}'", flush=True)
    sys.exit(subprocess.call([sys.executable, str(ROOT / "run.py"), "--config", str(config_path)], cwd=ROOT))


if __name__ == "__main__":
    main()
