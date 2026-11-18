from __future__ import annotations

import asyncio
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from .config import Outlet
from .domain import Shift, moscow_day_bounds
from .http import Http, NetworkError


class IikoError(Exception):
    pass


class IikoEndpoint(Protocol):
    base_url: str
    timezone: ZoneInfo

    def credentials(self) -> tuple[str, str]: ...


def parse_shifts(payload, timezone, *, require_register: bool = False) -> list[Shift]:
    if not isinstance(payload, list):
        raise IikoError("Ответ смен не является списком JSON")
    result = []
    for row in payload:
        if not isinstance(row, dict):
            raise IikoError("Неверный формат записи смены")
        opened = row.get("openDate")
        if not isinstance(opened, str):
            raise IikoError("В смене нет корректного openDate")
        register = None
        if require_register:
            register = row.get("cashRegNumber")
            if type(register) is not int or register < 0:
                raise IikoError("В смене нет корректного cashRegNumber для отбора по кассам")
        try:
            stamp = datetime.fromisoformat(opened.replace("Z", "+00:00"))
        except ValueError:
            raise IikoError("Некорректное время открытия смены") from None
        if "T" not in opened and " " not in opened:
            raise IikoError("В openDate отсутствует время")
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone)
        result.append(Shift(register, stamp))
    return result


class Iiko:
    def __init__(self, server: IikoEndpoint, http: Http):
        self.server, self.http = server, http
        self.key: str | None = None
        self._lock = asyncio.Lock()
        self._blocked_until = 0.0

    async def _login(self):
        login, password = self.server.credentials()
        password_hash = hashlib.sha1(password.encode("utf-8")).hexdigest()
        url = self.server.base_url + "/api/auth"
        params = {"login": login, "pass": password_hash}
        try:
            response = await self.http.request("POST", url, params=params)
        except NetworkError as error:
            if error.status != 405:
                raise
            # Compatibility for installations which only offer GET /auth.
            response = await self.http.request("GET", url, params=params)
        key = response.body.strip()
        if key.startswith('"'):
            try:
                key = json.loads(key)
            except ValueError:
                raise IikoError("Неверный ответ авторизации") from None
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,128}", key):
            raise IikoError("Неверный ответ авторизации")
        self.key = key

    async def _get(self, path: str, params=None):
        async with self._lock:
            loop = asyncio.get_running_loop()
            if loop.time() < self._blocked_until:
                raise IikoError("iiko: пауза после ошибки связи/ограничения API")
            for attempt in range(2):
                authorized = False
                try:
                    if not self.key:
                        await self._login()
                    authorized = True
                    return await self.http.request("GET", self.server.base_url + path,
                                                   params={**(params or {}), "key": self.key})
                except NetworkError as error:
                    if error.status == 401 and authorized:
                        self.key = None
                        if attempt == 0:
                            continue
                    if error.status in (0, 401, 429) or error.status >= 500:
                        self._blocked_until = loop.time() + max(error.retry_after, 30)
                    raise IikoError(f"iiko: {error}") from None
                except IikoError:
                    self._blocked_until = loop.time() + 30
                    raise
        raise IikoError("Не удалось получить данные iiko")

    async def shifts(self, outlet: Outlet, now: datetime) -> list[Shift]:
        begin, end = moscow_day_bounds(now)
        # The API takes inclusive calendar dates. Its timezone can differ from Moscow.
        last_instant = end - timedelta(microseconds=1)
        params = {
            "openDateFrom": begin.astimezone(self.server.timezone).date().isoformat(),
            "openDateTo": last_instant.astimezone(self.server.timezone).date().isoformat(),
            "status": "ANY",
        }
        if outlet.department_id:
            params["departmentId"] = outlet.department_id
        if outlet.group_id:
            params["groupId"] = outlet.group_id
        response = await self._get("/api/v2/cashshifts/list", params)
        try:
            payload = response.json()
        except NetworkError:
            raise IikoError("iiko вернул некорректный JSON смен") from None
        return parse_shifts(payload, self.server.timezone, require_register=bool(outlet.registers))

    async def discover(self, kind: str) -> list[dict]:
        if kind not in ("departments", "groups"):
            raise ValueError("Unknown kind")
        response = await self._get(f"/api/corporation/{kind}")
        try:
            root = ET.fromstring(response.body)
        except ET.ParseError:
            raise IikoError("Неверный XML справочника") from None
        rows = []
        for element in root.iter():
            fields = {child.tag.rsplit("}", 1)[-1]: child.text for child in element}
            if fields.get("id") and fields.get("name"):
                rows.append({key: fields.get(key) for key in ("id", "name", "parentId", "departmentId")})
        return rows

    async def close(self):
        async with self._lock:
            if self.key:
                key, self.key = self.key, None
                try:
                    await self.http.request("GET", self.server.base_url + "/api/logout", params={"key": key})
                except NetworkError:
                    pass
