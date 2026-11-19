from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from .settings import InputError
from .storage import Database


OUTCOMES = ("on_time", "late_open", "not_open", "api_error")


def delay_in_seconds(check: dict) -> int | None:
    if check.get("status") != "late_open":
        return None
    try:
        opened = datetime.fromisoformat(check.get("opened_at"))
        planned = datetime.fromisoformat(check.get("deadline"))
        return max(0, int((opened - planned).total_seconds()))
    except (ValueError, TypeError):
        return None


def summary(counts: Counter) -> dict:
    confirmed = counts["on_time"] + counts["late_open"] + counts["not_open"]
    return {**{status: counts[status] for status in OUTCOMES}, "confirmed": confirmed,
            "on_time_percent": round(100 * counts["on_time"] / confirmed, 1) if confirmed else None,
            "average_delay_seconds": round(counts["delay_sum"] / counts["delay_count"]) if counts["delay_count"] else None}


def point_names(db: Database, point: str | None) -> dict[str, str]:
    if point is None:
        return {item["id"]: item["name"] for item in db.list("points")}
    item = db.get("points", point)
    if item is None:
        raise InputError("Точка недоступна. Выберите другую точку или обновите страницу.")
    return {point: item["name"]}


def opening_statistics(db: Database, today: date, period: int, point: str | None = None) -> dict:
    first = today - timedelta(days=period - 1)
    names = point_names(db, point)
    by_day = {(first + timedelta(days=offset)).isoformat(): Counter() for offset in range(period)}
    by_point = {item: Counter() for item in names}
    totals = Counter()
    for item, day, check in db.check_history(first.isoformat(), today.isoformat(), point):
        status = check.get("status")
        if status not in OUTCOMES or item not in by_point:
            continue
        delay = delay_in_seconds(check)
        for counts in (totals, by_day[day], by_point[item]):
            counts[status] += 1
            if delay is not None:
                counts["delay_count"] += 1
                counts["delay_sum"] += delay
    return {"from_date": first.isoformat(), "to_date": today.isoformat(), "totals": summary(totals),
            "days": [{"day": day, **summary(counts)} for day, counts in by_day.items()],
            "points": [{"id": item, "name": names[item], **summary(counts)} for item, counts in by_point.items()]}


def openings_on_day(db: Database, day: date, point: str | None = None) -> dict:
    checks = db.checks(day.isoformat())
    rows = []
    for item, name in point_names(db, point).items():
        check = checks.get(item, {})
        rows.append({"name": name, "status": check.get("status", "unknown"),
                     "deadline": check.get("deadline"), "opened_at": check.get("opened_at"),
                     "checked_at": check.get("checked_at"), "delay_seconds": delay_in_seconds(check)})
    return {"day": day.isoformat(), "points": rows}
