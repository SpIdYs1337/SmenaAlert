from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
from concurrent.futures import TimeoutError as WorkerTimeout
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import MSK
from ..domain import evaluate, opening_time
from ..http import Http
from ..iiko import Iiko, IikoError
from .mail import Mailbox, Mailer, MailError
from .settings import Endpoint, InputError, Vault, as_outlet, imap_settings, public
from .storage import Database

log = logging.getLogger(__name__)
SHIFT_CHECK_DELAY = timedelta(minutes=15)


def stamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def clock_label(value: str | None) -> str:
    return datetime.fromisoformat(value).astimezone(MSK).strftime("%H:%M:%S") if value else "—"


def opening_grace(prefs: dict) -> timedelta:
    return timedelta(minutes=1) if prefs["inclusive"] else timedelta()


def revision(point: dict, server: dict, prefs: dict) -> str:
    data = [point, server, opening_grace(prefs).total_seconds(), SHIFT_CHECK_DELAY.total_seconds()]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def can_check_receipt(job: dict, config: dict) -> bool:
    return (job["kind"] == "test" and job["status"] == "sent" and bool(job["message_id"])
            and job["recipient"] == config.get("email"))


AUTO_ALERT_TITLES = {
    "late_open": "Смена открыта с опозданием",
    "not_open": "Смена не открыта",
}


STATES = {
    "on_time": "Смена открыта вовремя.", "late_open": "Смена открыта с опозданием.",
    "not_open": "В iiko нет открытия смены за сегодня.", "waiting": "Ожидается время проверки смены.",
    "unknown": "Статус открытия ещё не проверен.", "api_error": "iiko не подтвердил статус смены.",
    "day_off": "По графику выходной.", "paused": "Точка на паузе.",
}


def letter(point: dict, state: dict, now: datetime, kind: str, note: str = "") -> tuple[str, str]:
    title = AUTO_ALERT_TITLES[state["status"]] if kind == "auto" else "Статус открытия"
    lines = [title, "", f"Точка: {point['name']}", f"Дата: {now:%d.%m.%Y} (МСК)",
             f"Открытие по графику: {clock_label(state.get('deadline'))}",
             f"Фактическое открытие: {clock_label(state.get('opened_at'))}", "",
             STATES.get(state["status"], STATES["unknown"])]
    if state.get("checked_at"):
        lines.append(f"Данные на {clock_label(state['checked_at'])} МСК.")
    if note:
        lines.extend(("", note))
    return f"{title} · {point['name']} · {now:%d.%m.%Y}", "\n".join(lines)


class Service:
    def __init__(self, db: Database, vault: Vault, *, mailer=None, mailbox=None, iiko_factory=Iiko, now=None):
        self.db, self.vault = db, vault
        self.mailer = mailer or Mailer()
        self.mailbox = mailbox or Mailbox()
        self.iiko_factory = iiko_factory
        self.now = now or (lambda: datetime.now(MSK))
        self.http = Http()
        self._clients: dict[str, tuple[dict, Iiko]] = {}
        self._check_lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()
        self._smtp_lock = asyncio.Lock()
        self._imap_lock = asyncio.Lock()
        self._thread = None
        self._loop = None
        self._ready = threading.Event()

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=lambda: asyncio.run(self._run()), name="shift-monitor", daemon=True)
            self._thread.start()
            if not self._ready.wait(5):
                raise RuntimeError("Монитор не запустился.")

    def close(self):
        if self._thread and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._stop.set)
            self._thread.join(timeout=24)

    def call(self, method, *args):
        if not self._loop or not self._thread or not self._thread.is_alive():
            raise InputError("Монитор пока недоступен. Повторите попытку.")
        future = asyncio.run_coroutine_threadsafe(method(*args), self._loop)
        try:
            return future.result(timeout=48)
        except WorkerTimeout:
            raise InputError("Проверка ещё выполняется. Обновите данные чуть позже.") from None

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._ready.set()
        try:
            await asyncio.gather(self._periodic(self.check, lambda: self.db.preferences()["poll_seconds"]),
                                 self._periodic(self.deliver, lambda: 3))
        finally:
            await asyncio.gather(*(client.close() for _, client in self._clients.values()), return_exceptions=True)

    async def _periodic(self, action, interval):
        while not self._stop.is_set():
            try:
                await action()
            except Exception as error:
                log.error("%s: %s", action.__name__, type(error).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval())
            except TimeoutError:
                pass

    async def _client(self, server: dict):
        async with self._client_lock:
            server_id = server["id"]
            connection = {key: server[key] for key in ("base_url", "login", "secret", "timezone")}
            cached = self._clients.get(server_id)
            if cached and cached[0] == connection:
                return cached[1]
            if cached:
                await cached[1].close()
                self._clients.pop(server_id, None)
            endpoint = Endpoint(connection["base_url"], ZoneInfo(connection["timezone"]), connection["login"],
                                self.vault.decrypt(connection["secret"]))
            client = self.iiko_factory(endpoint, self.http)
            self._clients[server_id] = (connection, client)
            return client

    async def probe_iiko(self, server_id: str, discover: bool):
        async with self._check_lock:
            server = self.db.get("servers", server_id)
            if not server:
                raise InputError("Сначала сохраните подключение iiko.")
            client = await self._client(server)
            if discover:
                return await client.discover("departments")
            try:
                await client.probe()
            except IikoError as error:
                self.db.save_setting(f"health:{server_id}", {"ok": False, "message": str(error)})
                raise
            self.db.save_setting(f"health:{server_id}", {"ok": True, "message": "iiko принял учётные данные."})

    async def check(self, force=False, point_id=None):
        async with self._check_lock:
            prefs = self.db.preferences()
            if not (force or prefs["enabled"]):
                return 0
            now = self.now()
            points = self.db.list("points")
            if point_id:
                points = [point for point in points if point["id"] == point_id]
            servers = {server["id"]: server for server in self.db.list("servers")}
            for server_id in self._clients.keys() - servers.keys():
                _, client = self._clients.pop(server_id)
                await client.close()
            previous = self.db.checks(now.date().isoformat())
            capacity = asyncio.Semaphore(6)

            async def check_point(point):
                async with capacity:
                    try:
                        return await self._check_one(point, servers[point["server"]], prefs, now,
                                                     previous.get(point["id"]), force)
                    except Exception as error:
                        log.error("Проверка точки %s: %s", point["id"], type(error).__name__)
                        return False

            return sum(await asyncio.gather(*(check_point(point) for point in points)))

    async def _check_one(self, point: dict, server: dict, prefs: dict, now: datetime, previous, force: bool) -> bool:
        if not point["active"]:
            return False
        outlet, day = as_outlet(point), now.date().isoformat()
        scheduled = opening_time(outlet, now.date())
        if scheduled is None:
            return False
        deadline = datetime.combine(now.date(), scheduled, MSK)
        if now < deadline + SHIFT_CHECK_DELAY:
            return False
        fingerprint = revision(point, server, prefs)
        if not force and previous and previous.get("revision") == fingerprint and previous["status"] in ("on_time", "late_open"):
            self._queue_auto(point, previous, now)
            return False
        try:
            client = await self._client(server)
            shifts = await client.shifts(outlet, now)
            error = None
        except (IikoError, InputError) as exc:
            shifts, error = [], str(exc)
        checked_at = self.now()
        if (checked_at.date() != now.date() or self.db.get("points", point["id"]) != point
                or self.db.get("servers", server["id"]) != server):
            return False
        result = evaluate(outlet, shifts, checked_at, grace=opening_grace(prefs))
        state = {"status": "api_error" if error else result.status, "opened_at": stamp(result.opened_at) if result.opened_at else None,
                 "deadline": stamp(deadline), "checked_at": stamp(checked_at), "error": error, "revision": fingerprint}
        self.db.save_check(point["id"], day, state)
        if not force and not error:
            self._queue_auto(point, state, checked_at)
        return True

    def _jobs(self, recipients: list[dict], point: dict | None, kind: str, outcome, subject: str,
              body: str, now: datetime, source_revision=None) -> list[dict]:
        point_id = point["id"] if point else None
        day = now.date().isoformat()
        return [{"point": point_id, "point_name": point["name"] if point else "",
                 "day": day, "kind": kind, "outcome": outcome, "recipient": contact["email"],
                 "subject": subject, "body": body, "created_at": stamp(now), "revision": source_revision,
                 "event_key": f"{point_id}/{day}/{kind}/{contact['email'].casefold()}"
                 if kind == "auto" else None} for contact in recipients]

    def _queue_auto(self, point: dict, state: dict, now: datetime):
        status = state["status"]
        if status not in AUTO_ALERT_TITLES:
            return
        recipients = self.db.targets(point["id"])
        if recipients:
            subject, body = letter(point, state, now, "auto")
            self.db.enqueue(self._jobs(recipients, point, "auto", status, subject, body, now, state["revision"]))

    def views(self) -> list[dict]:
        now = self.now()
        checks = self.db.checks(now.date().isoformat())
        servers = {server["id"]: server for server in self.db.list("servers")}
        prefs = self.db.preferences()
        links = self.db.links()
        points = []
        for point in self.db.list("points"):
            scheduled = opening_time(as_outlet(point), now.date())
            deadline = datetime.combine(now.date(), scheduled, MSK) if scheduled is not None else None
            check_at = deadline + SHIFT_CHECK_DELAY if deadline is not None else None
            saved = checks.get(point["id"], {})
            if saved.get("revision") != revision(point, servers.get(point["server"], {}), prefs):
                saved = {}
            status = "paused" if not point["active"] else "day_off" if deadline is None else saved.get("status")
            if not status:
                status = "waiting" if now < check_at else "unknown"
            points.append({**point, "people": links.get(point["id"], []), "today": {
                **saved, "status": status, "deadline": stamp(deadline) if deadline else None,
                "check_at": stamp(check_at) if check_at else None,
            }})
        return points

    def snapshot(self):
        return {"points": self.views(), "people": self.db.list("people"),
                "servers": [public(server) | {"health": self.db.get_setting(f"health:{server['id']}")}
                            for server in self.db.list("servers")],
                "smtp": public(self.db.get_setting("smtp", {})), "smtp_health": self.db.get_setting("health:smtp"),
                "imap": public(self.db.get_setting("imap", {})),
                "monitor": self.db.preferences(), "history": self.db.history(), "now": stamp(self.now())}

    def manual_preview(self, point_id: str, note: str):
        point = next((point for point in self.views() if point["id"] == point_id), None)
        if not point:
            raise InputError("Точка не найдена.")
        subject, body = letter(point, point["today"], self.now(), "manual", note)
        return point, subject, body

    def queue_manual(self, point: dict, ids: list[str], subject: str, body: str) -> list[int]:
        targets = [contact for contact in self.db.targets(point["id"]) if contact["id"] in ids]
        if not targets or len(targets) != len(ids):
            raise InputError("Выберите активных получателей, закреплённых за этой точкой.")
        self._smtp_config()
        return self.db.enqueue(self._jobs(targets, point, "manual", None, subject, body, self.now()))

    def queue_test(self, email: str) -> int:
        self._smtp_config()
        now = self.now()
        return self.db.enqueue(self._jobs([{"email": email}], None, "test", None, "Проверка почты · Контроль открытия",
            f"Проверка отправки из панели контроля открытия.\n{now:%d.%m.%Y %H:%M} МСК.\n", now))[0]

    def _smtp_config(self):
        config = self.db.get_setting("smtp")
        if not config:
            raise InputError("Сначала сохраните настройки SMTP.")
        return config, self.vault.decrypt(config["secret"])

    async def probe_smtp(self):
        async with self._smtp_lock:
            config, password = self._smtp_config()
            try:
                await asyncio.to_thread(self.mailer.probe, config, password)
            except MailError as error:
                self.db.save_setting("health:smtp", {"ok": False, "message": str(error)})
                raise
            self.db.save_setting("health:smtp", {"ok": True, "message": "Соединение с SMTP установлено."})

    async def save_imap(self, payload: dict):
        async with self._imap_lock:
            config = imap_settings(payload, self.db.get_setting("imap", {}), self.vault)
            self.db.save_setting("imap", config)

    def _imap_config(self):
        config = self.db.get_setting("imap")
        if not config:
            raise InputError("Сначала сохраните настройки IMAP.")
        return config, self.vault.decrypt(config["secret"])

    async def probe_imap(self):
        async with self._imap_lock:
            config, password = self._imap_config()
            try:
                await asyncio.to_thread(self.mailbox.probe, config, password)
            except MailError as error:
                config["health"] = {"ok": False, "message": str(error)}
                self.db.save_setting("imap", config)
                raise
            config["health"] = {"ok": True, "message": "Папка «Входящие» доступна по IMAP."}
            self.db.save_setting("imap", config)

    async def check_receipt(self, job: dict) -> bool:
        async with self._imap_lock:
            config, password = self._imap_config()
            if not can_check_receipt(job, config):
                raise InputError("Отправьте тестовое письмо на адрес подключённого ящика IMAP.")
            return await asyncio.to_thread(self.mailbox.contains, config, password, job["message_id"])

    def _auto_state(self, job: dict, now: datetime) -> dict | None:
        prefs = self.db.preferences()
        if (job["kind"] != "auto" or job["outcome"] not in AUTO_ALERT_TITLES or not prefs["enabled"]
                or job["day"] != now.date().isoformat()):
            return None
        point = self.db.get("points", job["point"])
        if not point or not point["active"]:
            return None
        server = self.db.get("servers", point["server"])
        if not server or job["revision"] != revision(point, server, prefs):
            return None
        state = self.db.checks(job["day"]).get(point["id"], {})
        if state.get("status") not in AUTO_ALERT_TITLES or state.get("revision") != job["revision"]:
            return None
        check_at = datetime.fromisoformat(state["deadline"]) + SHIFT_CHECK_DELAY
        return state if check_at <= min(now, datetime.fromisoformat(state["checked_at"])) else None

    async def deliver(self):
        async with self._smtp_lock:
            for job in self.db.queued(stamp(self.now())):
                if self._thread and self._stop.is_set():
                    break
                now = self.now()
                if job["point"] and not any(contact["email"] == job["recipient"]
                                            for contact in self.db.targets(job["point"])):
                    self.db.finish(job["id"], "cancelled", job["attempts"], error="Получатель больше не закреплён за точкой.")
                    continue
                subject, body = job["subject"], job["body"]
                if job["kind"] in ("auto", "followup"):
                    state = self._auto_state(job, now)
                    if state is None:
                        self.db.finish(job["id"], "cancelled", job["attempts"], error="Условия отправки изменились.")
                        continue
                    subject, body = letter({"name": job["point_name"]}, state, now, "auto")
                    if (state["status"], subject, body) != (job["outcome"], job["subject"], job["body"]):
                        self.db.refresh_letter(job["id"], state["status"], subject, body)
                attempts = job["attempts"] + 1
                try:
                    config, password = self._smtp_config()
                    message_id = await asyncio.to_thread(self.mailer.send, config, password, job["recipient"],
                                                         subject, body, key=job["id"])
                except (MailError, InputError) as error:
                    temporary = isinstance(error, MailError) and error.retryable and attempts < 3 and job["kind"] != "test"
                    retry = stamp(now + timedelta(minutes=attempts)) if temporary else None
                    self.db.finish(job["id"], "queued" if temporary else "failed", attempts, error=str(error), retry_at=retry)
                else:
                    self.db.finish(job["id"], "sent", attempts, delivered_at=stamp(self.now()),
                                   message_id=message_id if job["kind"] == "test" else None)
