"""Start Oversyte:  python run.py [--config config.yaml]"""
import argparse
import logging
import shutil
from pathlib import Path

import uvicorn

from oversyte.config import load_config
from oversyte.server import create_app


def main():
    ap = argparse.ArgumentParser(description="Oversyte theft detection")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        shutil.copy(Path(__file__).parent / "config.example.yaml", cfg_path)
        print(f"Created {cfg_path} from the example. Edit it to add your cameras.")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(cfg_path)
    if cfg["dashboard_password"] == "change-me":
        logging.warning("Dashboard password is still 'change-me'. Set dashboard_password in %s", cfg_path)
    app = create_app(cfg)
    print(f"\n  Oversyte dashboard: http://localhost:{cfg['server']['port']}\n")
    uvicorn.run(app, host=cfg["server"]["host"], port=int(cfg["server"]["port"]), log_level="warning")


if __name__ == "__main__":
    main()
