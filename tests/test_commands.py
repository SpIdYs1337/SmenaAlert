import unittest
from datetime import datetime, timezone

from shiftbot.commands import Commands
from shiftbot.state import Store
from tests.helpers import ADMIN_LOGIN, FakeMessenger, settings


class MonitorStub:
    def __init__(self):
        self.calls = 0

    async def tick(self, **kwargs):
        self.calls += 1


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.settings = settings()
        self.messenger = FakeMessenger()
        self.monitor = MonitorStub()
        self.commands = Commands(self.settings, self.store, self.monitor, self.messenger, "ShiftBot")

    def tearDown(self):
        self.store.close()

    def message(self, text, sender=ADMIN_LOGIN, chat=None, **kwargs):
        return {"text": text, "timestamp": int(datetime.now(timezone.utc).timestamp()),
                "from": {"login": sender, "robot": False},
                "chat": {"id": self.settings.chat_id if chat is None else chat}, **kwargs}

    async def test_reply_assigns_user_by_login_and_display_name(self):
        await self.commands.handle(self.message("/assign arbat", reply_to_message={
            "from": {"login": "ivan@example.org", "display_name": "Иван", "robot": False}}))
        self.assertEqual(self.store.people("arbat"), [("ivan@example.org", "Иван")])
        self.assertIn("Иван (ivan@example.org)", self.messenger.messages[-1][0])

    async def test_member_cannot_change_responsibles(self):
        await self.commands.handle(self.message("/assign arbat manager@example.org", sender="member@example.org"))
        self.assertEqual(self.store.people("arbat"), [])

    async def test_other_chat_cannot_manage_points(self):
        await self.commands.handle(self.message("/assign arbat manager@example.org", chat="another-chat"))
        self.assertEqual(self.messenger.messages, [])
        self.assertEqual(self.store.people("arbat"), [])

    async def test_commands_addressed_to_other_bot_are_ignored(self):
        await self.commands.handle(self.message("/assign@AnotherBot arbat manager@example.org"))
        self.assertEqual(self.store.people("arbat"), [])

    async def test_assigns_multiple_people_and_unassigns_one(self):
        await self.commands.handle(self.message("/assign arbat manager@example.org"))
        await self.commands.handle(self.message("/assign arbat second@example.org"))
        await self.commands.handle(self.message("/unassign arbat manager@example.org"))
        self.assertEqual(self.store.people("arbat"), [("second@example.org", "second@example.org")])

    async def test_admin_can_change_schedule_and_exception(self):
        await self.commands.handle(self.message("/schedule arbat mon,tue 10:30"))
        self.assertEqual(self.store.override("arbat", "mon"), "10:30")
        await self.commands.handle(self.message("/exception arbat 2026-12-31 off"))
        self.assertEqual(self.store.override("arbat", "2026-12-31"), "off")
        await self.commands.handle(self.message("/exception arbat 2026-12-31 reset"))
        self.assertIsNone(self.store.override("arbat", "2026-12-31"))

    async def test_whoami_is_available_before_chat_is_configured(self):
        await self.commands.handle(self.message("/whoami", sender="member@example.org", chat="another-chat"))
        self.assertIn("member@example.org", self.messenger.messages[-1][0])
        self.assertEqual(self.messenger.messages[-1][1], "another-chat")

    async def test_stale_management_commands_are_not_replayed(self):
        message = self.message("/assign arbat manager@example.org")
        message["timestamp"] -= 3600
        await self.commands.handle(message)
        self.assertEqual(self.store.people("arbat"), [])


if __name__ == "__main__":
    unittest.main()
