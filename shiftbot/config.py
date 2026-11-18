from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class ConfigError(ValueError):
    pass


def clock(value: str) -> time | None:
    if value == "off":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ConfigError("Время: ЧЧ:ММ или off")
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise ConfigError("Некорректное время") from None


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", value):
        raise ConfigError("Код: 1–40 символов a-z, 0-9, _ или -")
    return value


def uuid_or_empty(value: str) -> str:
    if value == "":
        return ""
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ConfigError("department_id и group_id должны быть UUID") from None


def messenger_login(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}(?:@[A-Za-z0-9][A-Za-z0-9.-]{0,126})?", value
    ):
        raise ConfigError("Укажите логин Яндекс Мессенджера, например manager@example.org")
    return value.lower()


@dataclass(frozen=True)
class Server:
    id: str
    base_url: str
    login_env: str
    password_env: str
    timezone: ZoneInfo

    def credentials(self) -> tuple[str, str]:
        login, password = os.getenv(self.login_env), os.getenv(self.password_env)
        if not login or not password:
            raise ConfigError(f"Не заданы {self.login_env} и/или {self.password_env}")
        return login, password


@dataclass(frozen=True)
class Outlet:
    id: str
    name: str
    server: str
    department_id: str = ""
    group_id: str = ""
    registers: tuple[int, ...] = ()
    require_all_registers: bool = False
    week: dict[str, str] = field(default_factory=dict)
    exceptions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Settings:
    chat_id: str
    admin_logins: frozenset[str]
    servers: dict[str, Server]
    outlets: tuple[Outlet, ...]
    token_env: str = "YANDEX_MESSENGER_BOT_TOKEN"
    state_path: str = "data/state.sqlite3"
    interval: int = 30
    repeat_minutes: int = 0
    error_after_minutes: int = 3
    grace_seconds: int = 0
    at_deadline_is_on_time: bool = False
    thread_id: int | None = None
    notify_responsibles_privately: bool = False

    def token(self) -> str:
        value = os.getenv(self.token_env, "")
        if not re.fullmatch(r"[\x21-\x7e]+", value):
            raise ConfigError(f"Не задан корректный {self.token_env}")
        return value


def load_env(path: str) -> None:
    """Small .env reader; never executes shell substitutions or overwrites the environment."""
    file = Path(path)
    if not file.exists():
        return
    for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError(f"Ошибка .env, строка {number}")
        if value[:1] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigError(f"Ошибка кавычек .env, строка {number}")
            value = value[1:-1]
        os.environ.setdefault(key, value)


def integer(data: dict, key: str, default: int, low: int, high: int) -> int:
    value = data.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ConfigError(f"{key}: целое число от {low} до {high}")
    return value


def boolean(data: dict, key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if type(value) is not bool:
        raise ConfigError(f"{key}: true или false")
    return value


def env_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ConfigError("Некорректное имя переменной окружения")
    return value


def read_config(path: str) -> Settings:
    with open(path, "rb") as file:
        raw = tomllib.load(file)
    bot = raw["bot"]
    chat_id = bot["chat_id"]
    admins = bot.get("admin_logins")
    if not isinstance(chat_id, str) or not re.fullmatch(r"[\x21-\x7e]{1,256}", chat_id):
        raise ConfigError('chat_id должен быть строковым ID чата Яндекс Мессенджера')
    if not isinstance(admins, list) or not admins:
        raise ConfigError("admin_logins: непустой список логинов Яндекс Мессенджера")
    admin_logins = frozenset(messenger_login(login) for login in admins)
    servers: dict[str, Server] = {}
    for sid, item in raw["servers"].items():
        identifier(sid)
        url = item["base_url"].rstrip("/")
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.netloc or parts.username or parts.password
                or parts.query or parts.fragment or not parts.path.endswith("/resto")):
            raise ConfigError("base_url: HTTPS-адрес iikoServer, заканчивающийся на /resto, без пароля")
        servers[sid] = Server(sid, url, env_name(item["login_env"]),
                              env_name(item["password_env"]), ZoneInfo(item.get("timezone", "Europe/Moscow")))
    if not servers:
        raise ConfigError("Добавьте хотя бы один сервер")
    outlets = []
    for item in raw["outlets"]:
        oid = identifier(item["id"])
        name = item["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 120 or any(ord(c) < 32 for c in name):
            raise ConfigError("Название точки: 1–120 символов без перевода строки")
        if item["server"] not in servers:
            raise ConfigError(f"Неизвестный сервер у точки {oid}")
        week = item["week"]
        if set(week) != set(DAYS):
            raise ConfigError(f"У {oid} задайте все 7 дней: {', '.join(DAYS)}")
        exceptions = item.get("exceptions", {})
        for key, value in exceptions.items():
            if date.fromisoformat(key).isoformat() != key:
                raise ConfigError("Дата исключения: ГГГГ-ММ-ДД")
            clock(value)
        for value in week.values():
            clock(value)
        registers = item.get("cash_register_numbers", [])
        if (not isinstance(registers, list) or any(type(n) is not int or n < 0 for n in registers)
                or len(set(registers)) != len(registers)):
            raise ConfigError(f"Некорректные номера касс у {oid}")
        require_all = boolean(item, "require_all_registers")
        if require_all and not registers:
            raise ConfigError(f"Для всех касс у {oid} задайте cash_register_numbers")
        outlets.append(Outlet(oid, name, item["server"], uuid_or_empty(item.get("department_id", "")),
                              uuid_or_empty(item.get("group_id", "")), tuple(registers),
                              require_all, dict(week), dict(exceptions)))
    if not outlets or len({o.id for o in outlets}) != len(outlets):
        raise ConfigError("Добавьте точки с уникальными id")
    for sid in servers:
        group = [o for o in outlets if o.server == sid]
        if len(group) > 1 and any(not o.department_id for o in group):
            raise ConfigError(f"На общем сервере {sid} нужен department_id каждой точки")
        scopes = [(o.department_id, o.group_id, o.registers) for o in group]
        if len(scopes) != len(set(scopes)):
            raise ConfigError(f"Одинаковые фильтры точек на сервере {sid}")
    state_path = bot.get("state_path", "data/state.sqlite3")
    if not isinstance(state_path, str) or not state_path:
        raise ConfigError("Задайте state_path")
    thread_id = bot.get("thread_id")
    if thread_id is not None and (type(thread_id) is not int or thread_id <= 0):
        raise ConfigError("thread_id должен быть положительным числовым ID треда")
    return Settings(chat_id=chat_id, admin_logins=admin_logins, servers=servers, outlets=tuple(outlets),
                    token_env=env_name(bot.get("token_env", "YANDEX_MESSENGER_BOT_TOKEN")), state_path=state_path,
                    interval=integer(bot, "poll_interval_seconds", 30, 5, 300),
                    repeat_minutes=integer(bot, "repeat_after_minutes", 0, 0, 1440),
                    error_after_minutes=integer(bot, "api_error_after_minutes", 3, 0, 60),
                    grace_seconds=integer(bot, "notification_delay_seconds", 0, 0, 3600),
                    at_deadline_is_on_time=boolean(bot, "at_deadline_is_on_time"), thread_id=thread_id,
                    notify_responsibles_privately=boolean(bot, "notify_responsibles_privately"))
