"""SQLite storage for incidents, users, and push subscriptions."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

STATUSES = ("new", "theft", "false_alarm", "dismissed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL,
    camera_name TEXT NOT NULL,
    ts REAL NOT NULL,
    score REAL NOT NULL,
    region TEXT,
    reason TEXT,
    clip TEXT,
    thumb TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    notes TEXT NOT NULL DEFAULT '',
    reviewed_at REAL,
    is_test INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts DESC);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subs (
    endpoint TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql, args=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # ---- events ----
    def add_event(self, ev: dict):
        cols = ", ".join(ev)
        marks = ", ".join("?" for _ in ev)
        self._exec(f"INSERT INTO events ({cols}) VALUES ({marks})", tuple(ev.values()))

    def get_event(self, eid: str) -> dict | None:
        rows = self._all("SELECT * FROM events WHERE id = ?", (eid,))
        return rows[0] if rows else None

    def list_events(self, status: str | None = None, before: float | None = None,
                    limit: int = 50) -> list[dict]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if before:
            sql += " AND ts < ?"
            args.append(before)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return self._all(sql, args)

    def review(self, eid: str, status: str | None, notes: str | None):
        if status is not None:
            if status not in STATUSES:
                raise ValueError("bad status")
            self._exec("UPDATE events SET status = ?, reviewed_at = ? WHERE id = ?",
                       (status, time.time(), eid))
        if notes is not None:
            self._exec("UPDATE events SET notes = ? WHERE id = ?", (notes, eid))

    def delete_event(self, eid: str):
        self._exec("DELETE FROM events WHERE id = ?", (eid,))

    def expired_events(self, older_than: float) -> list[dict]:
        return self._all("SELECT * FROM events WHERE ts < ? AND status != 'theft'", (older_than,))

    def stats(self, since_day: float, since_week: float) -> dict:
        with self._lock:
            q = self._db.execute
            today = q("SELECT COUNT(*) FROM events WHERE ts >= ? AND is_test = 0", (since_day,)).fetchone()[0]
            week = q("SELECT COUNT(*) FROM events WHERE ts >= ? AND is_test = 0", (since_week,)).fetchone()[0]
            unreviewed = q("SELECT COUNT(*) FROM events WHERE status = 'new' AND is_test = 0").fetchone()[0]
            theft = q("SELECT COUNT(*) FROM events WHERE status = 'theft' AND ts >= ?", (since_week,)).fetchone()[0]
            fa = q("SELECT COUNT(*) FROM events WHERE status = 'false_alarm' AND ts >= ?", (since_week,)).fetchone()[0]
        return {"today": today, "week": week, "unreviewed": unreviewed,
                "theft_week": theft, "false_alarm_week": fa}

    def daily(self, days: int = 14) -> list[dict]:
        """Real (non-test) alerts per local day for the last `days` days, oldest first."""
        import datetime as dt
        today = dt.date.today()
        start = dt.datetime.combine(today - dt.timedelta(days=days - 1), dt.time()).timestamp()
        rows = self._all(
            "SELECT date(ts, 'unixepoch', 'localtime') AS d, COUNT(*) AS alerts, "
            "SUM(status = 'theft') AS theft, SUM(status = 'false_alarm') AS false_alarm, "
            "SUM(status = 'new') AS open FROM events WHERE is_test = 0 AND ts >= ? GROUP BY d", (start,))
        by_day = {r["d"]: r for r in rows}
        out = []
        for i in range(days):
            d = (today - dt.timedelta(days=days - 1 - i)).isoformat()
            r = by_day.get(d, {})
            out.append({"date": d, "alerts": r.get("alerts", 0) or 0, "theft": r.get("theft", 0) or 0,
                        "false_alarm": r.get("false_alarm", 0) or 0, "open": r.get("open", 0) or 0})
        return out

    def has_test_alert(self) -> bool:
        return bool(self._all("SELECT 1 FROM events WHERE is_test = 1 LIMIT 1"))

    # ---- users ----
    def add_user(self, name: str, email: str, password_hash: str) -> dict:
        cur = self._exec(
            "INSERT INTO users (name, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (name, email, password_hash, time.time()),
        )
        if cur.lastrowid is None:
            raise RuntimeError("failed to create user")
        user = self.get_user(int(cur.lastrowid))
        if user is None:
            raise RuntimeError("failed to load created user")
        return user

    def get_user(self, user_id: int) -> dict | None:
        rows = self._all("SELECT * FROM users WHERE id = ?", (user_id,))
        return rows[0] if rows else None

    def get_user_by_email(self, email: str) -> dict | None:
        rows = self._all("SELECT * FROM users WHERE email = ?", (email,))
        return rows[0] if rows else None

    def has_users(self) -> bool:
        return bool(self._all("SELECT 1 FROM users LIMIT 1"))

    def list_users(self) -> list[dict]:
        return self._all("SELECT * FROM users ORDER BY created")

    def delete_user(self, user_id: int) -> bool:
        return self._exec("DELETE FROM users WHERE id = ?", (user_id,)).rowcount > 0

    # ---- push subscriptions ----
    def add_sub(self, sub: dict, label: str):
        self._exec("INSERT OR REPLACE INTO push_subs (endpoint, data, label, created) VALUES (?, ?, ?, ?)",
                   (sub["endpoint"], json.dumps(sub), label, time.time()))

    def remove_sub(self, endpoint: str):
        self._exec("DELETE FROM push_subs WHERE endpoint = ?", (endpoint,))

    def subs(self) -> list[dict]:
        return self._all("SELECT * FROM push_subs ORDER BY created")
