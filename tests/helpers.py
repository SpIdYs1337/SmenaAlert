from dataclasses import replace
from datetime import datetime

from shiftbot.config import DAYS, MSK, Outlet, Server, Settings
from shiftbot.domain import Shift
from shiftbot.iiko import IikoError
from shiftbot.messenger import MessengerError


CHAT_ID = "0/0/11111111-1111-4111-8111-111111111111"
ADMIN_LOGIN = "admin@example.org"


def outlet(**changes):
    base = Outlet("arbat", "Арбат & кофе", "main", week={day: "09:00" for day in DAYS})
    return replace(base, **changes)


def settings(**changes):
    base = Settings(CHAT_ID, frozenset({ADMIN_LOGIN}),
                    {"main": Server("main", "https://example.org/resto", "LOGIN", "PASSWORD", MSK)},
                    (outlet(),))
    return replace(base, **changes)


def now(hour=9, minute=5, second=0, day=18):
    return datetime(2026, 11, day, hour, minute, second, tzinfo=MSK)


def shift(hour=8, minute=55, day=18, register=1):
    return Shift(register, now(hour, minute, day=day))


class FakeIiko:
    def __init__(self, shifts=None):
        self.items = shifts or []
        self.failure = False
        self.calls = []

    async def shifts(self, outlet, when):
        self.calls.append((outlet.id, when))
        if self.failure:
            raise IikoError("Нет связи")
        return self.items


class FakeMessenger:
    def __init__(self):
        self.messages = []
        self.failure = False

    async def send(self, text, chat_id=None, *, login=None, event_id=None, important=False):
        if self.failure:
            raise MessengerError("Нет связи")
        self.messages.append((text, chat_id, login, event_id))

    async def send_lines(self, lines, chat_id=None):
        await self.send("\n".join(lines), chat_id)
