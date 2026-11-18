"""Download the team test videos from S3 into eval/videos/.

    python tools/fetch_videos.py            # download any videos you don't have yet
    python tools/fetch_videos.py --upload   # project owner: upload new videos from eval/videos

Needs the AWS CLI (https://aws.amazon.com/cli/) signed in with `aws configure`.
Videos are already named video-01.mp4, video-02.mp4, ... (see eval/video_names.json),
so labels in eval/labels/ match them as soon as they're downloaded.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUCKET = "s3://oversyte-training-videos/videos/"
REGION = "us-east-2"
LOCAL = ROOT / "eval" / "videos"
WINDOWS_AWS = Path(r"C:\Program Files\Amazon\AWSCLIV2\aws.exe")


def find_aws() -> str:
    aws = shutil.which("aws") or (str(WINDOWS_AWS) if WINDOWS_AWS.exists() else None)
    if not aws:
        sys.exit("AWS CLI not found. Install it (https://aws.amazon.com/cli/), then run `aws configure`.")
    return aws


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upload", action="store_true", help="upload eval/videos to the bucket instead")
    args = ap.parse_args()

    LOCAL.mkdir(parents=True, exist_ok=True)
    src, dst = (str(LOCAL), BUCKET) if args.upload else (BUCKET, str(LOCAL))
    cmd = [find_aws(), "s3", "sync", src, dst, "--region", REGION,
           "--exclude", "*", "--include", "video-*"]
    print(("Uploading" if args.upload else "Downloading"), f"{src} -> {dst}")
    code = subprocess.call(cmd)
    if code != 0:
        sys.exit("Failed. Check that you're signed in (`aws sts get-caller-identity`) "
                 "and have access to the bucket.")
    n = len(list(LOCAL.glob("video-*")))
    print(f"Done. {n} video(s) in {LOCAL}")


if __name__ == "__main__":
    main()
