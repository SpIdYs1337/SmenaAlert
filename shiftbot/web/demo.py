from __future__ import annotations

from datetime import datetime, time, timedelta
from uuid import UUID

from ..config import DAYS, MSK
from ..domain import Shift
from ..iiko import IikoError
from .service import Service, revision, stamp


SAMPLES = (
    ("Арбат", "09:00", "08:53", "on_time", (0, 2)),
    ("Сокол", "09:00", "09:08", "late_open", (1,)),
    ("Парк культуры", "08:30", "08:24", "on_time", (3,)),
    ("Тверская", "09:00", "", "not_open", (0, 1)),
    ("Хамовники", "09:30", "09:19", "on_time", (2, 3)),
    ("Марьино", "10:30", "", "waiting", (4,)),
    ("Белорусская", "10:30", "", "waiting", (2,)),
    ("Кунцево", "09:00", "", "api_error", (4,)),
)


def department(index: int) -> str:
    return str(UUID(int=index + 100))


class DemoIiko:
    def __init__(self, _endpoint, _http):
        pass

    async def probe(self):
        pass

    async def shifts(self, outlet, now):
        for index, (_, _, opened, state, _) in enumerate(SAMPLES):
            if department(index) == outlet.department_id:
                if state == "api_error":
                    raise IikoError("В тестовых данных нет связи с iiko.")
                return [Shift(1, datetime.combine(now.date(), time.fromisoformat(opened), MSK))] if opened else []
        return []

    async def discover(self, _kind):
        return [{"id": department(index), "name": entry[0]} for index, entry in enumerate(SAMPLES)]

    async def close(self):
        pass


class DemoMail:
    def probe(self, _settings, _password):
        pass

    def send(self, *_args, key):
        return f"<demo-{key}@example.org>"

    def contains(self, _settings, _password, _message_id):
        return True


def demo_now():
    return datetime.now(MSK).replace(hour=10, minute=5, second=0, microsecond=0)


def seed(service: Service):
    db, vault, now = service.db, service.vault, service.now()
    server_id = db.save("servers", {"name": "iikoChain · Москва", "base_url": "https://chain.example.org/resto",
                                   "login": "api_monitor", "secret": vault.encrypt("demo-connection"), "timezone": "Europe/Moscow"})
    db.save_setting(f"health:{server_id}", {"ok": True, "message": "iiko принял учётные данные."})
    db.save_setting("smtp", {"host": "smtp.yandex.ru", "port": 465, "security": "ssl", "sender_name": "Контроль открытия",
                             "login": "alerts@example.org", "sender": "alerts@example.org", "secret": vault.encrypt("demo-mail")})
    db.save_setting("health:smtp", {"ok": True, "message": "Соединение с SMTP установлено."})
    contacts = [("Анна Смирнова", "anna@example.org"), ("Иван Петров", "ivan@example.org"),
                ("Мария Волкова", "maria@example.org"), ("Ольга Соколова", "olga@example.org"),
                ("Денис Орлов", "denis@example.org")]
    ids = [db.save("people", {"name": name, "email": email, "active": True}) for name, email in contacts]
    for index, (name, scheduled, opened, status, target_ids) in enumerate(SAMPLES):
        point = {"name": name, "server": server_id, "department_id": department(index), "group_id": "",
                 "registers": [], "require_all_registers": False, "week": dict.fromkeys(DAYS, scheduled),
                 "exceptions": {}, "active": True}
        point_id = db.save("points", point, links=[ids[i] for i in target_ids])
        as_time = lambda value: stamp(datetime.combine(now.date(), time.fromisoformat(value), MSK))
        checked = {"status": status, "checked_at": stamp(now) if status != "waiting" else None,
                   "opened_at": as_time(opened) if opened else None, "deadline": as_time(scheduled),
                   "error": "Нет связи с iiko." if status == "api_error" else None,
                   "revision": revision(db.get("points", point_id), db.get("servers", server_id), db.preferences())}
        db.save_check(point_id, now.date().isoformat(), checked)
        for age in range(1, 7):
            day = now.date() - timedelta(days=age)
            planned = datetime.combine(day, time.fromisoformat(scheduled), MSK)
            minutes = (index * 3 + age) % 11 - 7
            outcome = "on_time" if minutes < 0 else "late_open"
            actual = planned + timedelta(minutes=minutes)
            if (index + age * 2) % 11 == 0:
                outcome, actual = "not_open", None
            db.save_check(point_id, day.isoformat(), {
                "status": outcome, "deadline": stamp(planned), "opened_at": stamp(actual) if actual else None,
                "checked_at": stamp(planned + timedelta(minutes=25)),
            })
        if status in ("late_open", "not_open"):
            service._queue_auto(db.get("points", point_id), checked, now)
    for job in db.queued(stamp(now)):
        db.finish(job["id"], "sent", 1, delivered_at=stamp(now))
