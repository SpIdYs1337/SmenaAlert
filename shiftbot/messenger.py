from __future__ import annotations

import asyncio
import re
from hashlib import sha256

from .http import Http, NetworkError


class MessengerError(Exception):
    pass


def literal(value: str) -> str:
    """Render names and logins as text in the Messenger's markup."""
    return re.sub(r"([\\`*_~+\[\]])", r"\\\1", value)


def responsible_line(people: list[tuple[str, str]]) -> str:
    if not people:
        return "Ответственные пока не назначены."
    names = [literal(login if label == login else f"{label} ({login})") for login, label in people]
    return "Ответственные: " + ", ".join(names)


class Messenger:
    def __init__(self, token: str, chat_id: str, http: Http, thread_id: int | None = None):
        self._base = "https://botapi.messenger.yandex.net/bot/v1/"
        self._headers = {"Authorization": f"OAuth {token}"}
        self.chat_id, self.http, self.thread_id = chat_id, http, thread_id
        self._blocked_until = 0.0

    async def call(self, method: str, path: str, *, params=None, payload=None) -> dict:
        loop = asyncio.get_running_loop()
        if loop.time() < self._blocked_until:
            raise MessengerError("Мессенджер ограничил частоту запросов")
        try:
            response = await self.http.request(method, self._base + path, params=params,
                                               payload=payload, headers=self._headers)
            body = response.json()
        except NetworkError as error:
            if error.status == 429:
                self._blocked_until = loop.time() + max(error.retry_after, 5)
            raise MessengerError(f"Яндекс Мессенджер: {error}") from None
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise MessengerError("Яндекс Мессенджер отклонил запрос")
        return body

    async def updates(self, offset: int) -> list[dict]:
        body = await self.call("GET", "messages/getUpdates/", params={"limit": 100, "offset": offset})
        updates = body.get("updates")
        if not isinstance(updates, list) or any(
            not isinstance(update, dict) or type(update.get("update_id")) is not int for update in updates
        ):
            raise MessengerError("Яндекс Мессенджер вернул неверный список обновлений")
        return sorted(updates, key=lambda update: update["update_id"])

    async def send(self, text: str, chat_id: str | None = None, *, login: str | None = None,
                   event_id: str | None = None, important: bool = False):
        if login is not None and chat_id is not None:
            raise ValueError("Выберите один адрес доставки")
        payload = {"text": text, "disable_web_page_preview": True}
        if login is None:
            payload["chat_id"] = self.chat_id if chat_id is None else chat_id
            if chat_id is None and self.thread_id:
                payload["thread_id"] = self.thread_id
        else:
            payload["login"] = login
        if important:
            payload["important"] = True
        if event_id is not None:
            route = (payload.get("chat_id"), payload.get("thread_id"), login, event_id)
            payload["payload_id"] = sha256(repr(route).encode("utf-8")).hexdigest()
        result = await self.call("POST", "messages/sendText/", payload=payload)
        if type(result.get("message_id")) is not int:
            raise MessengerError("Яндекс Мессенджер не подтвердил ID сообщения")
        return result

    async def send_lines(self, lines: list[str]):
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 5500 and chunk:
                await self.send(chunk)
                chunk = ""
            chunk += ("\n" if chunk else "") + line
        if chunk:
            await self.send(chunk)
