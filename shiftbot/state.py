from __future__ import annotations

import fcntl
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: str):
        self._lock = None
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._lock = open(path + ".lock", "a")
            try:
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._lock.close()
                raise RuntimeError("Уже запущен процесс с этой базой данных") from None
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS schedules (
                outlet TEXT NOT NULL, day TEXT NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY (outlet, day)
            );
            CREATE TABLE IF NOT EXISTS messenger_responsibles (
                outlet TEXT NOT NULL, identity TEXT NOT NULL, label TEXT NOT NULL,
                PRIMARY KEY (outlet, identity)
            );
            CREATE TABLE IF NOT EXISTS daily (
                outlet TEXT NOT NULL, day TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unknown',
                checked_at TEXT, deadline TEXT, opened_at TEXT,
                last_alert_at TEXT, resolved INTEGER NOT NULL DEFAULT 0,
                error_since TEXT, error_notified INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (outlet, day)
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()
        if self._lock:
            self._lock.close()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str):
        with self.db:
            self.db.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, value))

    def override(self, outlet: str, day: str) -> str | None:
        row = self.db.execute("SELECT value FROM schedules WHERE outlet=? AND day=?", (outlet, day)).fetchone()
        return row[0] if row else None

    def set_schedule(self, outlet: str, days: list[str], value: str):
        with self.db:
            for day in days:
                if value == "reset":
                    self.db.execute("DELETE FROM schedules WHERE outlet=? AND day=?", (outlet, day))
                else:
                    self.db.execute("INSERT INTO schedules VALUES (?,?,?) ON CONFLICT(outlet,day) DO UPDATE SET value=excluded.value",
                                    (outlet, day, value))

    def people(self, outlet: str) -> list[tuple[str, str]]:
        return [tuple(row) for row in self.db.execute(
            "SELECT identity,label FROM messenger_responsibles WHERE outlet=? ORDER BY identity", (outlet,))]

    def assign(self, outlet: str, identity: str, label: str):
        with self.db:
            self.db.execute("INSERT INTO messenger_responsibles VALUES (?,?,?) ON CONFLICT(outlet,identity) DO UPDATE SET label=excluded.label",
                            (outlet, identity, label))

    def unassign(self, outlet: str, identity: str) -> bool:
        with self.db:
            return bool(self.db.execute("DELETE FROM messenger_responsibles WHERE outlet=? AND identity=?", (outlet, identity)).rowcount)

    def daily(self, outlet: str, day: str) -> dict:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO daily (outlet,day) VALUES (?,?)", (outlet, day))
        return dict(self.db.execute("SELECT * FROM daily WHERE outlet=? AND day=?", (outlet, day)).fetchone())

    def update_daily(self, outlet: str, day: str, **values):
        allowed = {"status", "checked_at", "deadline", "opened_at", "last_alert_at", "resolved", "error_since", "error_notified"}
        if not values or not set(values) <= allowed:
            raise ValueError("Unknown daily column")
        self.daily(outlet, day)
        columns = ",".join(f"{key}=?" for key in values)
        with self.db:
            self.db.execute(f"UPDATE daily SET {columns} WHERE outlet=? AND day=?", (*values.values(), outlet, day))
