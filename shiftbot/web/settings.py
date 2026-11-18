from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import date
from email.errors import HeaderParseError, MessageDefect
from email.headerregistry import Address
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet, InvalidToken

from ..config import DAYS, Outlet, clock, uuid_or_empty


class InputError(ValueError):
    pass


class Vault:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            try:
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
                    handle.write(Fernet.generate_key())
            except FileExistsError:
                pass
        self.key = path.read_bytes().strip()
        self.fernet = Fernet(self.key)

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii") if value else ""

    def decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode("ascii")).decode("utf-8") if value else ""
        except (InvalidToken, UnicodeError):
            raise InputError("Пароль не удалось расшифровать. Сохраните его заново в настройках.") from None

    def session_key(self, user: str, password: str) -> bytes:
        return hashlib.sha256(self.key + b"\0panel-session\0" + user.encode() + b"\0" + password.encode()).digest()


def identifier() -> str:
    return uuid.uuid4().hex[:16]


def text(data: dict, key: str, label: str, *, required: bool = True, limit: int = 160) -> str:
    value = data.get(key, "")
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise InputError(f"Проверьте поле «{label}».")
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise InputError(f"Проверьте поле «{label}».")
    return value


def number(value, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise InputError(f"Поле «{label}» должно быть от {low} до {high}.")
    return value


def flag(data: dict, key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if type(value) is not bool:
        raise InputError("Некорректный переключатель.")
    return value


def email_address(value: object) -> str:
    if (not isinstance(value, str) or len(value) > 254 or not value.strip()
            or any(c in value for c in "\r\n,<>;")):
        raise InputError("Укажите один корректный адрес электронной почты.")
    try:
        addr = Address(addr_spec=value.strip())
        domain = addr.domain.encode("idna").decode("ascii").lower()
        if not addr.username or not domain or any(c.isspace() for c in domain):
            raise ValueError()
        return Address(username=addr.username, domain=domain).addr_spec
    except (ValueError, UnicodeError, IndexError, HeaderParseError, MessageDefect):
        raise InputError("Укажите один корректный адрес электронной почты.") from None


def list_ids(data: dict, key: str) -> list[str]:
    values = data.get(key, [])
    if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values) or len(values) > 500:
        raise InputError("Проверьте список назначений.")
    return list(dict.fromkeys(values))


def store_secret(data: dict, before: dict, identity_fields: tuple, result: dict, vault: Vault) -> str:
    password = data.get("password", "")
    if not isinstance(password, str) or len(password) > 1024 or "\x00" in password:
        raise InputError("Проверьте пароль подключения.")
    if not result["login"]:
        return ""
    if password:
        return vault.encrypt(password)
    if before.get("secret") and all(before.get(key) == result[key] for key in identity_fields):
        return before["secret"]
    raise InputError("Введите пароль подключения.")


def public(settings: dict) -> dict:
    return {**{key: value for key, value in settings.items() if key != "secret"},
            "has_password": bool(settings.get("secret"))}


def mail_connection(data: dict, protocol: str, *, login_required: bool) -> dict:
    host = text(data, "host", f"{protocol}-сервер", limit=253)
    if any(c.isspace() or c in "/@?#\\" for c in host):
        raise InputError(f"Укажите имя {protocol}-сервера без протокола и пути.")
    security = data.get("security")
    if security not in ("ssl", "starttls"):
        raise InputError("Выберите SSL/TLS или STARTTLS.")
    return {
        "host": host.lower(),
        "port": number(data.get("port"), "Порт", 1, 65535),
        "security": security,
        "login": text(data, "login", f"Логин {protocol}", required=login_required, limit=254),
    }


def smtp_settings(data: dict, before: dict, vault: Vault) -> dict:
    result = mail_connection(data, "SMTP", login_required=False) | {
        "sender": email_address(data.get("sender")),
        "sender_name": text(data, "sender_name", "Имя отправителя", required=False, limit=120),
    }
    result["secret"] = store_secret(data, before, ("host", "port", "security", "login"), result, vault)
    return result


def imap_settings(data: dict, before: dict, vault: Vault) -> dict:
    result = mail_connection(data, "IMAP", login_required=True) | {"email": email_address(data.get("email"))}
    result["secret"] = store_secret(data, before, ("host", "port", "security", "login"), result, vault)
    return result


def iiko_settings(data: dict, before: dict, vault: Vault) -> dict:
    url = text(data, "base_url", "Адрес iiko", limit=310).rstrip("/")
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or not parts.path.endswith("/resto")
                or any(c.isspace() or c == "\\" for c in url)):
            raise ValueError()
        if parts.port is not None and parts.port <= 0:
            raise ValueError()
    except ValueError:
        raise InputError("Адрес iiko должен начинаться с https:// и заканчиваться на /resto.") from None
    zone = text(data, "timezone", "Часовой пояс", limit=80)
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise InputError("Укажите часовой пояс сервера, например Europe/Moscow.") from None
    result = {"name": text(data, "name", "Название подключения"), "base_url": url, "timezone": zone,
              "login": text(data, "login", "Логин iiko", limit=254)}
    result["secret"] = store_secret(data, before, ("base_url", "login"), result, vault)
    return result


def point_settings(data: dict) -> dict:
    week = data.get("week")
    if not isinstance(week, dict) or set(week) != set(DAYS):
        raise InputError("Укажите время или выходной для каждого дня недели.")
    exceptions = data.get("exceptions", {})
    if not isinstance(exceptions, dict) or len(exceptions) > 366:
        raise InputError("Проверьте исключения из графика.")
    for day, value in exceptions.items():
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError()
        except (ValueError, TypeError):
            raise InputError("Дата исключения должна быть в формате ГГГГ-ММ-ДД.") from None
        clock(value)
    for value in week.values():
        clock(value)
    registers = data.get("registers", [])
    if not isinstance(registers, list) or len(registers) > 100:
        raise InputError("Укажите номера касс через запятую.")
    registers = list(dict.fromkeys(number(item, "Номер кассы", 0, 2**31 - 1) for item in registers))
    require_all = flag(data, "require_all_registers")
    if require_all and not registers:
        raise InputError("Перечислите кассы, которые должны открыться.")
    return {"name": text(data, "name", "Название точки", limit=120),
            "server": text(data, "server", "Подключение iiko", limit=32),
            "department_id": uuid_or_empty(text(data, "department_id", "UUID предприятия", limit=36)),
            "group_id": uuid_or_empty(text(data, "group_id", "UUID группы касс", required=False, limit=36)),
            "registers": registers, "require_all_registers": require_all, "week": week,
            "exceptions": exceptions, "active": flag(data, "active", True)}


def as_outlet(point: dict) -> Outlet:
    return Outlet(point["id"], point["name"], point["server"], point["department_id"], point["group_id"],
                  tuple(point["registers"]), point["require_all_registers"], point["week"], point["exceptions"])


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    timezone: ZoneInfo
    login: str
    password: str = field(repr=False)

    def credentials(self):
        return self.login, self.password
