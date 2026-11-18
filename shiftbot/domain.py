from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from .config import DAYS, MSK, Outlet, clock


class ScheduleOverrides(Protocol):
    def override(self, outlet: str, day: str) -> str | None: ...


@dataclass(frozen=True)
class Shift:
    register: int | None
    opened_at: datetime


@dataclass(frozen=True)
class Result:
    status: str
    deadline: datetime | None
    opened_at: datetime | None = None


def opening_time(outlet: Outlet, day: date, store: ScheduleOverrides | None = None):
    iso, weekday = day.isoformat(), DAYS[day.weekday()]
    value = store.override(outlet.id, iso) if store else None
    if value is None:
        value = outlet.exceptions.get(iso)
    if value is None and store:
        value = store.override(outlet.id, weekday)
    if value is None:
        value = outlet.week[weekday]
    return clock(value)


def evaluate(outlet: Outlet, shifts: list[Shift], now: datetime, store: ScheduleOverrides | None = None,
             at_deadline_is_on_time: bool = False) -> Result:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(MSK)
    scheduled = opening_time(outlet, now.date(), store)
    if scheduled is None:
        return Result("day_off", None)
    deadline = datetime.combine(now.date(), scheduled, MSK)
    earliest: dict[int | None, datetime] = {}
    for shift in shifts:
        if shift.opened_at.tzinfo is None:
            raise ValueError("Shift time must be timezone-aware")
        opened = shift.opened_at.astimezone(MSK)
        if opened.date() != now.date() or opened > now:
            continue
        if outlet.registers and shift.register not in outlet.registers:
            continue
        earliest[shift.register] = min(earliest.get(shift.register, opened), opened)
    opened_at = None
    if outlet.require_all_registers:
        if all(register in earliest for register in outlet.registers):
            opened_at = max(earliest[register] for register in outlet.registers)
    elif earliest:
        opened_at = min(earliest.values())
    if now < deadline:
        return Result("waiting", deadline, opened_at)
    if opened_at is None:
        return Result("not_open", deadline)
    on_time = opened_at <= deadline if at_deadline_is_on_time else opened_at < deadline
    return Result("on_time" if on_time else "late_open", deadline, opened_at)


def moscow_day_bounds(now: datetime) -> tuple[datetime, datetime]:
    day = now.astimezone(MSK).date()
    begin = datetime.combine(day, datetime.min.time(), MSK)
    return begin, begin + timedelta(days=1)
