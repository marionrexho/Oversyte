"""Web Push (VAPID) for phone alerts via the installed dashboard PWA."""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from .storage import Store

log = logging.getLogger("oversyte.push")


class Pusher:
    def __init__(self, data_dir: Path, store: Store, contact: str):
        self.store = store
        self.contact = contact if contact.startswith(("mailto:", "https://")) else f"mailto:{contact}"
        key_path = data_dir / "vapid_private.pem"
        if key_path.exists():
            self.vapid = Vapid.from_file(str(key_path))
        else:
            data_dir.mkdir(parents=True, exist_ok=True)
            self.vapid = Vapid()
            self.vapid.generate_keys()
            self.vapid.save_key(str(key_path))
        raw = self.vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.public_key = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def send_all(self, payload: dict) -> dict:
        sent, failed = 0, 0
        for row in self.store.subs():
            sub = json.loads(row["data"])
            try:
                webpush(sub, json.dumps(payload), vapid_private_key=self.vapid,
                        vapid_claims={"sub": self.contact}, ttl=600,
                        headers={"Urgency": "high"})
                sent += 1
            except WebPushException as e:
                failed += 1
                code = getattr(e.response, "status_code", None)
                if code in (404, 410):  # subscription expired or revoked
                    self.store.remove_sub(row["endpoint"])
                log.warning("push failed (%s): %s", code, e)
            except Exception as e:  # network error etc.
                failed += 1
                log.warning("push failed: %s", e)
        return {"sent": sent, "failed": failed}
