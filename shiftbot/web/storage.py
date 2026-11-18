from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .settings import InputError, identifier


DEFAULTS = {"enabled": False, "poll_seconds": 30, "delay_seconds": 0, "inclusive": False}
ENTITIES = {"servers", "points", "people"}


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS servers (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS points (
                    id TEXT PRIMARY KEY, server TEXT NOT NULL REFERENCES servers(id), data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS people (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL COLLATE NOCASE UNIQUE, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS point_people (
                    point TEXT REFERENCES points(id) ON DELETE CASCADE,
                    person TEXT REFERENCES people(id) ON DELETE CASCADE,
                    PRIMARY KEY (point,person)
                );
                CREATE TABLE IF NOT EXISTS checks (
                    point TEXT REFERENCES points(id) ON DELETE CASCADE,
                    day TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (point,day)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE, point TEXT,
                    point_name TEXT NOT NULL, day TEXT, kind TEXT NOT NULL, outcome TEXT,
                    recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
                    created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0, retry_at TEXT, delivered_at TEXT,
                    error TEXT, revision TEXT, message_id TEXT
                );
                CREATE INDEX IF NOT EXISTS deliveries_ready ON deliveries(status,retry_at,id);
            """)
            db.execute("BEGIN IMMEDIATE")
            if not any(column["name"] == "message_id" for column in db.execute("PRAGMA table_info(deliveries)")):
                db.execute("ALTER TABLE deliveries ADD COLUMN message_id TEXT")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=8)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _entry(row):
        return {**json.loads(row["data"]), "id": row["id"]} if row else None

    def get_setting(self, key: str, default=None):
        with self.connection() as db:
            row = db.execute("SELECT data FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def save_setting(self, key: str, value):
        with self.connection() as db:
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                       (key, json.dumps(value)))

    def preferences(self):
        return {**DEFAULTS, **self.get_setting("monitor", {})}

    def get(self, entity: str, item_id: str) -> dict | None:
        if entity not in ENTITIES:
            raise ValueError("Unexpected entity")
        with self.connection() as db:
            return self._entry(db.execute(f"SELECT id,data FROM {entity} WHERE id=?", (item_id,)).fetchone())

    def list(self, entity: str) -> list[dict]:
        if entity not in ENTITIES:
            raise ValueError("Unexpected entity")
        with self.connection() as db:
            values = [self._entry(row) for row in db.execute(f"SELECT id,data FROM {entity}")]
        return sorted(values, key=lambda value: (value["name"].casefold(), value["id"]))

    def links(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        with self.connection() as db:
            for row in db.execute("SELECT point, person FROM point_people ORDER BY point,person"):
                result.setdefault(row[0], []).append(row[1])
        return result

    def targets(self, point: str) -> list[dict]:
        with self.connection() as db:
            people = [self._entry(row) for row in db.execute(
                "SELECT people.id,people.data FROM people JOIN point_people ON people.id=person WHERE point=? ORDER BY email",
                (point,))]
        return [person for person in people if person["active"]]

    def save(self, entity: str, value: dict, item_id: str | None = None, links: list[str] | None = None) -> str:
        if entity not in ENTITIES:
            raise ValueError("Unexpected entity")
        is_update = item_id is not None
        item_id = item_id or identifier()
        columns = {"servers": (), "points": ("server",), "people": ("email",)}[entity]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if is_update and db.execute(f"SELECT 1 FROM {entity} WHERE id=?", (item_id,)).fetchone() is None:
                raise InputError("Запись была удалена. Обновите список.")
            if entity == "points":
                existing = db.execute("SELECT data FROM points WHERE server=? AND id<>?", (value["server"], item_id))
                scope = (value["department_id"], value["group_id"], set(value["registers"]))
                for row in existing:
                    point = json.loads(row[0])
                    if scope == (point["department_id"], point["group_id"], set(point["registers"])):
                        raise InputError("Точка с тем же предприятием и фильтром касс уже добавлена.")
            values = (item_id, json.dumps(value), *(value[column] for column in columns))
            if is_update:
                updates = ",".join(f"{column}=?" for column in ("data", *columns))
                db.execute(f"UPDATE {entity} SET {updates} WHERE id=?", (*values[1:], item_id))
            else:
                placeholders = ",".join("?" for _ in values)
                db.execute(f"INSERT INTO {entity} ({','.join(('id','data',*columns))}) VALUES ({placeholders})", values)
            if links is not None and entity == "points":
                db.execute("DELETE FROM point_people WHERE point=?", (item_id,))
                db.executemany("INSERT INTO point_people VALUES (?,?)", ((item_id, person) for person in links))
            if links is not None and entity == "people":
                db.execute("DELETE FROM point_people WHERE person=?", (item_id,))
                db.executemany("INSERT INTO point_people VALUES (?,?)", ((point, item_id) for point in links))
        return item_id

    def delete(self, entity: str, item_id: str):
        if entity not in ENTITIES:
            raise ValueError("Unexpected entity")
        with self.connection() as db:
            if entity == "servers" and db.execute("SELECT 1 FROM points WHERE server=?", (item_id,)).fetchone():
                raise InputError("Сначала перенесите точки на другое подключение.")
            db.execute(f"DELETE FROM {entity} WHERE id=?", (item_id,))

    def checks(self, day: str) -> dict[str, dict]:
        with self.connection() as db:
            return {row[0]: json.loads(row[1]) for row in db.execute(
                "SELECT point,data FROM checks WHERE day=?", (day,))}

    def save_check(self, point: str, day: str, value: dict):
        with self.connection() as db:
            db.execute("INSERT INTO checks VALUES (?,?,?) ON CONFLICT(point,day) DO UPDATE SET data=excluded.data",
                       (point, day, json.dumps(value)))

    def enqueue(self, jobs: list[dict]) -> list[int]:
        ids = []
        with self.connection() as db:
            for job in jobs:
                result = db.execute("""
                    INSERT INTO deliveries
                        (event_key,point,point_name,day,kind,outcome,recipient,subject,body,created_at,revision)
                    VALUES (:event_key,:point,:point_name,:day,:kind,:outcome,:recipient,:subject,:body,:created_at,:revision)
                    ON CONFLICT(event_key) DO UPDATE SET
                        status='queued', outcome=excluded.outcome, subject=excluded.subject, body=excluded.body,
                        revision=excluded.revision, attempts=0, retry_at=NULL, error=NULL
                    WHERE deliveries.status='cancelled' OR (
                        deliveries.status='queued' AND deliveries.revision IS NOT excluded.revision
                    )
                    RETURNING id
                """, job).fetchone()
                if result:
                    ids.append(result[0])
        return ids

    def auto_sent(self, point: str, day: str) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT recipient,outcome FROM deliveries WHERE point=? AND day=? AND kind='auto' AND status='sent'",
                (point, day))]

    def queued(self, when: str, limit: int = 20) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM deliveries WHERE status='queued' AND (retry_at IS NULL OR retry_at<=?) ORDER BY id LIMIT ?",
                (when, limit))]

    def finish(self, job_id: int, status: str, attempts: int, *, error=None, retry_at=None, delivered_at=None,
               message_id=None):
        with self.connection() as db:
            db.execute("UPDATE deliveries SET status=?,attempts=?,error=?,retry_at=?,delivered_at=?,message_id=? WHERE id=?",
                       (status, attempts, error, retry_at, delivered_at, message_id, job_id))

    def refresh_letter(self, job_id: int, outcome: str, subject: str, body: str):
        with self.connection() as db:
            db.execute("UPDATE deliveries SET outcome=?,subject=?,body=? WHERE id=? AND status='queued'",
                       (outcome, subject, body, job_id))

    def retry(self, job_id: int) -> bool:
        with self.connection() as db:
            return bool(db.execute("UPDATE deliveries SET status='queued',retry_at=NULL,error=NULL "
                                   "WHERE id=? AND status='failed'", (job_id,)).rowcount)

    def history(self, limit: int = 200) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,point_name,kind,recipient,subject,status,attempts,created_at,delivered_at,error FROM deliveries ORDER BY id DESC LIMIT ?",
                (limit,))]

    def delivery(self, job_id: int) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM deliveries WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None
