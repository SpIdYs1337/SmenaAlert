import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from shiftbot.config import MSK
from shiftbot.monitor import Monitor
from shiftbot.state import Store
from tests.helpers import FakeIiko, FakeMessenger, now, outlet, settings, shift


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.client = FakeIiko()
        self.messenger = FakeMessenger()
        self.settings = settings()
        self.monitor = Monitor(self.settings, self.store, {"main": self.client}, self.messenger)

    def tearDown(self):
        self.store.close()

    async def test_only_one_group_alert_and_responsible_name(self):
        self.store.assign("arbat", "ivan@example.org", "Иван [менеджер]")
        await self.monitor.tick(now())
        await self.monitor.tick(now(minute=6))
        self.assertEqual(len(self.messenger.messages), 1)
        text = self.messenger.messages[0][0]
        self.assertIn("Арбат & кофе", text)
        self.assertIn(r"Иван \[менеджер\] (ivan@example.org)", text)
        self.assertIsNone(self.messenger.messages[0][2])

    async def test_optional_private_alert_addresses_only_responsible_for_point(self):
        self.store.assign("arbat", "ivan@example.org", "Иван")
        self.store.assign("another", "anna@example.org", "Анна")
        monitor = Monitor(settings(notify_responsibles_privately=True, repeat_minutes=1),
                          self.store, {"main": self.client}, self.messenger)
        await monitor.tick(now())
        await monitor.tick(now(minute=6))
        self.assertEqual([message[2] for message in self.messenger.messages], [None, "ivan@example.org", None])

    async def test_no_api_calls_before_deadline(self):
        await self.monitor.tick(now(8, 59))
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.messenger.messages, [])

    async def test_grace_delays_alert_but_does_not_move_deadline(self):
        monitor = Monitor(settings(grace_seconds=120), self.store, {"main": self.client}, self.messenger)
        await monitor.tick(now(9, 1))
        self.assertEqual(self.messenger.messages, [])
        self.client.items = [shift(9, 1)]
        await monitor.tick(now(9, 3))
        self.assertIn("Опоздание: 1 мин", self.messenger.messages[0][0])

    async def test_send_failure_is_retried(self):
        self.messenger.failure = True
        await self.monitor.tick(now())
        self.assertIsNone(self.store.daily("arbat", "2026-11-18")["last_alert_at"])
        self.messenger.failure = False
        await self.monitor.tick(now(minute=6))
        self.assertEqual(len(self.messenger.messages), 1)

    async def test_late_opening_sends_one_resolution(self):
        await self.monitor.tick(now())
        self.client.items = [shift(9, 6)]
        await self.monitor.tick(now(minute=7))
        await self.monitor.tick(now(minute=8))
        self.assertEqual(len(self.messenger.messages), 2)
        self.assertIn("Кассовая смена открыта с опозданием", self.messenger.messages[1][0])

    async def test_late_opening_detected_when_bot_started_later(self):
        self.client.items = [shift(9, 3)]
        await self.monitor.tick(now())
        self.assertIn("Открыта в 09:03:00", self.messenger.messages[0][0])
        self.assertEqual(self.store.daily("arbat", "2026-11-18")["resolved"], 1)

    async def test_on_time_opening_is_silent_and_stops_polling_point(self):
        self.client.items = [shift()]
        await self.monitor.tick(now())
        await self.monitor.tick(now(minute=7))
        self.assertEqual(self.messenger.messages, [])
        self.assertEqual(len(self.client.calls), 1)
        await self.monitor.tick(now(minute=8), force=True)
        self.assertEqual(len(self.client.calls), 2)

    async def test_delayed_iiko_data_corrects_initial_alert(self):
        await self.monitor.tick(now())
        self.client.items = [shift()]
        await self.monitor.tick(now(minute=7))
        self.assertEqual(len(self.messenger.messages), 2)
        self.assertIn("Уточнение", self.messenger.messages[1][0])
        self.assertIn("вовремя", self.messenger.messages[1][0])

    async def test_api_outage_is_not_lateness(self):
        self.client.failure = True
        await self.monitor.tick(now())
        self.assertEqual(self.messenger.messages, [])
        await self.monitor.tick(now(minute=8))
        await self.monitor.tick(now(minute=9))
        self.assertEqual(len(self.messenger.messages), 1)
        self.assertIn("Статус открытия неизвестен", self.messenger.messages[0][0])
        self.assertNotIn("Опоздание с открытием", self.messenger.messages[0][0])
        self.client.failure = False
        self.client.items = [shift()]
        await self.monitor.tick(now(minute=10))
        self.assertIn("восстановлена", self.messenger.messages[1][0])
        self.assertEqual(self.store.daily("arbat", "2026-11-18")["status"], "on_time")

    async def test_optional_reminder_interval(self):
        monitor = Monitor(settings(repeat_minutes=10), self.store, {"main": self.client}, self.messenger)
        await monitor.tick(now())
        await monitor.tick(now(minute=14))
        await monitor.tick(now(minute=15))
        self.assertEqual(len(self.messenger.messages), 2)

    async def test_new_moscow_date_has_own_alert(self):
        await self.monitor.tick(now())
        await self.monitor.tick(now(day=19))
        self.assertEqual(len(self.messenger.messages), 2)

    async def test_day_off_is_silent(self):
        self.store.set_schedule("arbat", ["wed"], "off")
        await self.monitor.tick(now())
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.messenger.messages, [])

    async def test_no_replay_after_restart_with_persistent_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "state.sqlite3")
            one = Store(path)
            one.assign("arbat", "anna@example.org", "Анна")
            await Monitor(self.settings, one, {"main": self.client}, self.messenger).tick(now())
            one.close()
            two = Store(path)
            try:
                await Monitor(self.settings, two, {"main": self.client}, self.messenger).tick(now(minute=7))
                self.assertEqual(two.people("arbat"), [("anna@example.org", "Анна")])
                self.assertEqual(len(self.messenger.messages), 1)
            finally:
                two.close()

    async def test_unavailable_server_does_not_stop_other_outlet(self):
        bad = FakeIiko()
        bad.failure = True
        monitor = Monitor(settings(outlets=(outlet(), outlet(id="tverskaya", server="second"))),
                          self.store, {"main": bad, "second": self.client}, self.messenger)
        await monitor.tick(now())
        self.assertEqual(len(self.messenger.messages), 1)
        self.assertIsNotNone(self.store.daily("tverskaya", "2026-11-18")["last_alert_at"])

    async def test_crossing_midnight_does_not_alert_yesterdays_date(self):
        class AfterMidnight(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 19, 0, 0, 1, tzinfo=MSK)

        with patch("shiftbot.monitor.datetime", AfterMidnight):
            await self.monitor._check(outlet(), now(23, 59, 59), True, False)
        self.assertEqual(self.messenger.messages, [])

    async def test_schedule_change_during_request_prevents_stale_alert(self):
        store = self.store

        class ChangeDuringRequest(FakeIiko):
            async def shifts(self, point, when):
                store.set_schedule(point.id, ["wed"], "off")
                return []

        monitor = Monitor(self.settings, store, {"main": ChangeDuringRequest()}, self.messenger)
        await monitor.tick(now())
        self.assertEqual(self.messenger.messages, [])


if __name__ == "__main__":
    unittest.main()
