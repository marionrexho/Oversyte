"""Publish the Oversyte pitch demo to a free Hugging Face Space.

One-time:   .venv\\Scripts\\python -m pip install huggingface_hub
            .venv\\Scripts\\hf auth login        (paste a token from huggingface.co/settings/tokens)
Publish:    .venv\\Scripts\\python deploy\\publish_hf.py --password YOUR-DEMO-PASSWORD

What it does:
  1. uploads the demo videos to a PRIVATE dataset (only your account can see them)
  2. creates a Docker Space and uploads the app code (the Space is public, so the code is visible)
  3. stores the password, dataset name and a token as Space secrets (never visible to visitors)
Re-run it any time to push code updates. The Space rebuilds itself (about 5-10 minutes).
"""
import argparse
from pathlib import Path

from huggingface_hub import HfApi, get_token

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VIDEOS = [
    ROOT / "eval" / "videos" / "video-14.mp4",
    ROOT / "eval" / "videos" / "video-02.mp4",
]
CODE = ["Dockerfile", ".dockerignore", "requirements.txt", "run.py", "oversyte/*.py", "web/**", "deploy/start_demo.py"]
SPACE_README = """---
title: Oversyte Demo
emoji: 🛡️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

Oversyte pitch demo: AI theft alerts from existing store cameras. Sign-in required.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--password", required=True, help="dashboard password for the demo")
    ap.add_argument("--space", default="oversyte-demo", help="Space name (default oversyte-demo)")
    ap.add_argument("--store", default="Demo Gas Station", help="store name shown in the demo")
    ap.add_argument("--videos", nargs="*", type=Path, default=DEFAULT_VIDEOS, help="demo videos (1-4)")
    ap.add_argument("--skip-videos", action="store_true", help="don't re-upload the videos")
    args = ap.parse_args()

    token = get_token()
    if not token:
        raise SystemExit("Not logged in. Run:  .venv\\Scripts\\hf auth login")
    api = HfApi(token=token)
    user = api.whoami()["name"]
    dataset, space = f"{user}/oversyte-demo-videos", f"{user}/{args.space}"

    if not args.skip_videos:
        api.create_repo(dataset, repo_type="dataset", private=True, exist_ok=True)
        for v in args.videos[:4]:
            print(f"Uploading video {v.name} to private dataset {dataset} …")
            api.upload_file(path_or_fileobj=str(v), path_in_repo=v.name, repo_id=dataset, repo_type="dataset")

    api.create_repo(space, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    for key, value in {"DEMO_PASSWORD": args.password, "DEMO_DATASET": dataset,
                       "HF_TOKEN": token, "DEMO_STORE": args.store}.items():
        api.add_space_secret(space, key, value)
    print(f"Uploading app code to Space {space} …")
    api.upload_folder(folder_path=str(ROOT), repo_id=space, repo_type="space", allow_patterns=CODE,
                      commit_message="Update Oversyte demo")
    api.upload_file(path_or_fileobj=SPACE_README.encode(), path_in_repo="README.md", repo_id=space, repo_type="space")

    host = f"{user}-{args.space}".lower().replace("_", "-").replace(".", "-")
    print("\nDone. The Space is building (about 5-10 minutes the first time).")
    print(f"  Build progress: https://huggingface.co/spaces/{space}")
    print(f"  Demo link:      https://{host}.hf.space")
    print(f"  Password:       {args.password}")


if __name__ == "__main__":
    main()
