from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from .config import DAYS, MSK, ConfigError, Settings, clock, messenger_login
from .domain import opening_time
from .messenger import Messenger, literal, responsible_line
from .monitor import Monitor
from .state import Store

log = logging.getLogger(__name__)

HELP = [
    "**Контроль открытия iiko • МСК**",
    "/whoami — ваш логин и ID чата",
    "/points — точки, время на сегодня, ответственные",
    "/status — результаты последних проверок за сегодня",
    "/responsibles КОД — ответственные точки",
    "",
    "**Команды администраторов**",
    "/assign КОД — ответом на сообщение сотрудника",
    "/assign КОД логин — добавить ответственного",
    "/unassign КОД логин — снять назначение",
    "/schedule КОД mon,tue 09:00 — время по дням недели",
    "/schedule КОД all 09:00 — время на всю неделю",
    "/schedule КОД sun off — выходной",
    "/schedule КОД all reset — убрать изменения из чата",
    "/exception КОД 2026-12-31 off — исключение на дату",
    "/exception КОД 2026-12-31 10:00 — другое время на дату",
    "/exception КОД 2026-12-31 reset — убрать изменение из чата",
    "/check — проверить точки сейчас (уведомления включены)",
    "",
    "Дни: mon,tue,wed,thu,fri,sat,sun. Время и даты всегда по МСК.",
    "Расписание действует сразу. Уже отправленные уведомления за день сохраняются.",
]

STATUS = {
    "unknown": "⏳ Ещё не проверялась",
    "waiting": "⏳ Ожидает контрольного времени",
    "day_off": "⚪ Выходной",
    "on_time": "🟢 Открыта вовремя",
    "late_open": "🟠 Открыта с опозданием",
    "not_open": "🔴 Нет открытия за сегодня в данных iiko",
    "api_error": "⚠️ Статус неизвестен: ошибка iiko",
}


class Commands:
    def __init__(self, settings: Settings, store: Store, monitor: Monitor, messenger: Messenger,
                 bot_login: str):
        self.settings, self.store, self.monitor, self.messenger = settings, store, monitor, messenger
        self.bot_login = bot_login.lower()
        self.outlets = {outlet.id: outlet for outlet in settings.outlets}

    async def handle(self, message: dict):
        command = incoming_command(message, self.bot_login)
        if command is None:
            return
        cmd, args, sender, chat_id = command
        if cmd == "/whoami":
            await self.messenger.send(whoami_text(sender, chat_id), chat_id)
            return
        if chat_id != self.settings.chat_id:
            return
        sent_at = message.get("timestamp")
        # Do not replay old management commands after a long outage.
        if type(sent_at) is not int or datetime.now(timezone.utc).timestamp() - sent_at > 600:
            return
        if cmd in ("/start", "/help"):
            await self.messenger.send_lines(HELP)
            return
        if cmd == "/points":
            now = datetime.now(MSK)
            lines = [f"**Точки • {now:%d.%m.%Y}, МСК**"]
            for outlet in self.settings.outlets:
                time = opening_time(outlet, now.date(), self.store)
                value = time.strftime("%H:%M") if time else "выходной"
                lines.append(f"`{outlet.id}` — {literal(outlet.name)} • {value}\n"
                             + responsible_line(self.store.people(outlet.id)))
            await self.messenger.send_lines(lines)
            return
        if cmd == "/status":
            now = datetime.now(MSK)
            lines = [f"**Открытие • {now:%d.%m.%Y}, МСК**"]
            for outlet in self.settings.outlets:
                row = self.store.daily(outlet.id, now.date().isoformat())
                scheduled = opening_time(outlet, now.date(), self.store)
                status = row["status"] if scheduled else "day_off"
                details = ""
                if row["opened_at"]:
                    details += f"; открытие {datetime.fromisoformat(row['opened_at']):%H:%M:%S}"
                if row["checked_at"]:
                    details += f"; данные на {datetime.fromisoformat(row['checked_at']):%H:%M:%S}"
                lines.append(f"{literal(outlet.name)}: {STATUS.get(status, status)}{details}")
            await self.messenger.send_lines(lines)
            return
        if cmd == "/responsibles":
            if len(args) != 1 or args[0] not in self.outlets:
                await self.messenger.send("Формат: /responsibles КОД. Коды: /points")
            else:
                await self.messenger.send(responsible_line(self.store.people(args[0])))
            return
        if cmd not in ("/assign", "/unassign", "/schedule", "/exception", "/check"):
            return
        if sender not in self.settings.admin_logins:
            await self.messenger.send("Эта команда доступна администраторам бота.")
            return
        try:
            await self._admin(cmd, args, message, sender)
        except (ConfigError, ValueError) as error:
            await self.messenger.send(literal(str(error)))

    async def _admin(self, cmd: str, args: list[str], message: dict, sender: str):
        if cmd == "/check":
            if args:
                raise ValueError("Формат: /check")
            await self.monitor.tick(force=True)
            await self.messenger.send("Проверка завершена. Результаты: /status")
            return
        if not args or args[0] not in self.outlets:
            raise ValueError("Неизвестный код точки. Посмотрите /points")
        outlet = args[0]
        if cmd == "/assign":
            if len(args) == 1:
                reply = message.get("reply_to_message") or {}
                person = reply.get("from") or {}
                identity = sender_login(person)
                if not identity:
                    raise ValueError("Ответьте /assign КОД на сообщение сотрудника или укажите /assign КОД логин")
                name = person.get("display_name")
                label = " ".join(name.split())[:120] if isinstance(name, str) else ""
                label = label or identity
            elif len(args) == 2:
                identity = messenger_login(args[1])
                label = identity
            else:
                raise ValueError("Формат: /assign КОД логин или ответ на сообщение: /assign КОД")
            people = dict(self.store.people(outlet))
            if len(people) >= 15 and identity not in people:
                raise ValueError("Для точки уже назначено 15 ответственных")
            self.store.assign(outlet, identity, label)
            log.info("Ответственный изменён: точка %s, администратор %s", outlet, sender)
            await self.messenger.send(f"Точка: {literal(self.outlets[outlet].name)}\n"
                                      + responsible_line(self.store.people(outlet)))
        elif cmd == "/unassign":
            if len(args) != 2:
                raise ValueError("Формат: /unassign КОД логин")
            removed = self.store.unassign(outlet, messenger_login(args[1]))
            await self.messenger.send("Назначение удалено." if removed else "Такого назначения нет.")
        elif cmd in ("/schedule", "/exception"):
            if len(args) != 3:
                raise ValueError("Формат: /schedule КОД mon,tue 09:00 или /exception КОД ГГГГ-ММ-ДД off")
            value = args[2]
            if value != "reset":
                clock(value)
            if cmd == "/schedule":
                days = list(DAYS) if args[1] == "all" else args[1].split(",")
                if not days or not set(days) <= set(DAYS):
                    raise ValueError("Дни: mon,tue,wed,thu,fri,sat,sun или all")
            else:
                try:
                    parsed = date.fromisoformat(args[1])
                    if parsed.isoformat() != args[1]:
                        raise ValueError()
                except ValueError:
                    raise ValueError("Дата: ГГГГ-ММ-ДД") from None
                days = [args[1]]
            self.store.set_schedule(outlet, days, value)
            log.info("Расписание изменено: точка %s, администратор %s", outlet, sender)
            await self.messenger.send("Расписание обновлено. Время на сегодня: /points. Действует сразу.")


def sender_login(sender) -> str | None:
    if not isinstance(sender, dict) or sender.get("robot"):
        return None
    try:
        return messenger_login(sender.get("login"))
    except ConfigError:
        return None


def incoming_command(update: dict, bot_login: str) -> tuple[str, list[str], str, str] | None:
    text, chat = update.get("text"), update.get("chat")
    if not isinstance(text, str) or not text.startswith("/") or not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    sender = sender_login(update.get("from"))
    if not sender or sender == bot_login or not isinstance(chat_id, str) or not chat_id:
        return None
    first, *args = text.split()
    cmd, _, target = first.partition("@")
    if target and target.lower() != bot_login:
        return None
    return cmd.lower(), args, sender, chat_id


def whoami_text(login: str, chat_id: str) -> str:
    return f"Ваш логин: `{login}`\nID чата: `{literal(chat_id)}`"
