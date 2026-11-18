from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta

from .config import MSK, Outlet, Settings
from .domain import Result, evaluate, opening_time
from .state import Store
from .messenger import Messenger, MessengerError, literal, responsible_line

log = logging.getLogger(__name__)


def stamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def lateness(opened: datetime, deadline: datetime) -> int:
    return max(0, math.ceil((opened - deadline).total_seconds() / 60))


class Monitor:
    def __init__(self, settings: Settings, store: Store, clients: dict, messenger: Messenger):
        self.settings, self.store, self.clients, self.messenger = settings, store, clients, messenger
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(4)
        self.last_cycle: datetime | None = None

    async def tick(self, now: datetime | None = None, *, force: bool = False):
        async with self._lock:
            current = (now or datetime.now(MSK)).astimezone(MSK)
            await asyncio.gather(*(self._check(outlet, current, now is None, force) for outlet in self.settings.outlets))
            self.last_cycle = current

    async def _check(self, outlet: Outlet, now: datetime, refresh: bool, force: bool):
        async with self._semaphore:
            try:
                await self._check_inner(outlet, now, refresh, force)
            except MessengerError as error:
                log.warning("Уведомление %s не доставлено: %s", outlet.id, error)
            except Exception as error:
                # An unexpected failure of one outlet must not stop the rest of the network.
                log.error("Ошибка обработки %s: %s", outlet.id, type(error).__name__)

    async def _check_inner(self, outlet: Outlet, now: datetime, refresh: bool, force: bool):
        day = now.date().isoformat()
        scheduled = opening_time(outlet, now.date(), self.store)
        if scheduled is None:
            self.store.update_daily(outlet.id, day, status="day_off", checked_at=now.isoformat(), deadline=None)
            return
        deadline = datetime.combine(now.date(), scheduled, MSK)
        if not force and now < deadline + timedelta(seconds=self.settings.grace_seconds):
            self.store.update_daily(outlet.id, day, status="waiting", deadline=deadline.isoformat())
            return
        previous = self.store.daily(outlet.id, day)
        if (not force and not previous["error_since"] and previous["deadline"] == deadline.isoformat()
                and (previous["status"] == "on_time"
                     or (previous["status"] == "late_open" and previous["resolved"]))):
            return
        try:
            shifts = await self.clients[outlet.server].shifts(outlet, now)
        except Exception as error:
            log.warning("Проверка iiko %s не выполнена: %s", outlet.id, type(error).__name__)
            checked_at = datetime.now(MSK) if refresh else now
            if checked_at.date() == now.date() and opening_time(outlet, now.date(), self.store) == scheduled:
                await self._api_failure(outlet, checked_at, previous, deadline)
            return
        # Discard a response requested for yesterday if the request crossed Moscow midnight.
        checked_at = datetime.now(MSK) if refresh else now
        if checked_at.date() != now.date() or opening_time(outlet, now.date(), self.store) != scheduled:
            return
        now = checked_at
        result = evaluate(outlet, shifts, now, self.store, self.settings.at_deadline_is_on_time)
        self.store.update_daily(outlet.id, day, status=result.status, deadline=deadline.isoformat(),
                                opened_at=result.opened_at.isoformat() if result.opened_at else None,
                                checked_at=now.isoformat())
        if previous["error_notified"]:
            await self.messenger.send(f"🔌 Связь с iiko восстановлена: **{literal(outlet.name)}**.\n"
                                      f"Проверка за {now:%d.%m.%Y}, {now:%H:%M:%S} МСК.",
                                      event_id=f"{outlet.id}/{day}/iiko-restored/{previous['error_since']}")
        self.store.update_daily(outlet.id, day, error_since=None, error_notified=0)
        if now >= deadline + timedelta(seconds=self.settings.grace_seconds):
            await self._notify(outlet, now, result, previous)

    async def _api_failure(self, outlet: Outlet, now: datetime, previous: dict, deadline: datetime):
        day = now.date().isoformat()
        since = stamp(previous["error_since"]) or now
        self.store.update_daily(outlet.id, day, status="api_error", error_since=since.isoformat(),
                                deadline=deadline.isoformat())
        if now < deadline + timedelta(seconds=self.settings.grace_seconds):
            return
        if not previous["error_notified"] and now - since >= timedelta(minutes=self.settings.error_after_minutes):
            await self.messenger.send("⚠️ **Не удалось проверить открытие**\n"
                                      f"Точка: {literal(outlet.name)}\nДата: {now:%d.%m.%Y}, МСК\n"
                                      f"Контрольное время: {deadline:%H:%M}\n"
                                      "Статус открытия неизвестен. Проверьте связь и доступ к iiko.",
                                      event_id=f"{outlet.id}/{day}/iiko-unavailable/{since.isoformat()}")
            self.store.update_daily(outlet.id, day, error_notified=1)

    async def _notify(self, outlet: Outlet, now: datetime, result: Result, previous: dict):
        if result.status not in ("on_time", "not_open", "late_open"):
            return
        day = now.date().isoformat()
        people = self.store.people(outlet.id)
        prefix = (f"Точка: **{literal(outlet.name)}**\nДата: {now:%d.%m.%Y}, МСК\n"
                  f"Открытие по графику: {result.deadline:%H:%M}\n")
        if previous["last_alert_at"] and result.opened_at:
            if not previous["resolved"]:
                if result.status == "on_time":
                    text = ("ℹ️ **Уточнение по данным iiko**\n" + prefix +
                            f"Получено время открытия: {result.opened_at:%H:%M:%S}. Открыта вовремя.\n")
                else:
                    text = ("✅ **Кассовая смена открыта с опозданием**\n" + prefix +
                            f"Открыта в {result.opened_at:%H:%M:%S}. "
                            f"Опоздание: {lateness(result.opened_at, result.deadline)} мин.\n")
                await self.messenger.send(text + responsible_line(people), event_id=f"{outlet.id}/{day}/opened")
                self.store.update_daily(outlet.id, day, resolved=1)
            return
        if result.status == "on_time":
            return
        last_alert = stamp(previous["last_alert_at"])
        if last_alert:
            if not self.settings.repeat_minutes or previous["resolved"]:
                return
            if now - last_alert < timedelta(minutes=self.settings.repeat_minutes):
                return
        if result.opened_at:
            details = (f"Открыта в {result.opened_at:%H:%M:%S}. "
                       f"Опоздание: {lateness(result.opened_at, result.deadline)} мин.\n")
        else:
            details = (f"По данным iiko на {now:%H:%M:%S} кассовая смена за сегодня не открыта.\n"
                       f"С контрольного времени прошло: {lateness(now, result.deadline)} мин.\n")
        text = "🚨 **Опоздание с открытием**\n" + prefix + details
        event_id = f"{outlet.id}/{day}/late/{previous['last_alert_at'] or 'first'}"
        await self.messenger.send(text + responsible_line(people), event_id=event_id, important=True)
        self.store.update_daily(outlet.id, day, last_alert_at=now.isoformat(),
                                resolved=int(result.opened_at is not None))
        if self.settings.notify_responsibles_privately and last_alert is None:
            for login, _ in people:
                try:
                    await self.messenger.send(text.rstrip(), login=login, event_id=event_id, important=True)
                except MessengerError as error:
                    log.warning("Личное уведомление о точке %s не доставлено: %s", outlet.id, error)
