import unittest
from datetime import datetime, timezone

from shiftbot.config import ConfigError, MSK, clock
from shiftbot.domain import evaluate, moscow_day_bounds
from shiftbot.state import Store
from tests.helpers import now, outlet, shift


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.outlet = outlet()

    def tearDown(self):
        self.store.close()

    def status(self, items, when=None, point=None, inclusive=False):
        return evaluate(point or self.outlet, items, when or now(), self.store, inclusive).status

    def test_before_deadline_does_not_alarm(self):
        self.assertEqual(self.status([], now(8, 59, 59)), "waiting")

    def test_empty_list_at_deadline_is_late(self):
        self.assertEqual(self.status([], now(9, 0)), "not_open")

    def test_yesterdays_open_shift_does_not_count(self):
        self.assertEqual(self.status([shift(day=17)]), "not_open")

    def test_closed_shift_still_proves_opening(self):
        # Domain only consumes openDate; closing it later cannot undo the opening.
        self.assertEqual(self.status([shift()]), "on_time")

    def test_actual_late_opening_is_late_after_restart(self):
        self.assertEqual(self.status([shift(9, 3)]), "late_open")

    def test_exact_boundary_is_strict_by_default(self):
        self.assertEqual(self.status([shift(9, 0)]), "late_open")
        self.assertEqual(self.status([shift(9, 0)], inclusive=True), "on_time")

    def test_reopened_shift_uses_first_opening(self):
        self.assertEqual(self.status([shift(9, 3), shift(8, 50)]), "on_time")

    def test_future_time_does_not_prove_current_opening(self):
        self.assertEqual(self.status([shift(10, 0)]), "not_open")

    def test_moscow_day_even_when_utc_day_is_yesterday(self):
        point = outlet(week={day: "00:01" for day in self.outlet.week})
        when = datetime(2026, 11, 17, 21, 5, tzinfo=timezone.utc)
        self.assertEqual(self.status([shift(0, 0)], when, point), "on_time")
        start, end = moscow_day_bounds(when)
        self.assertEqual(start.date().isoformat(), "2026-11-18")
        self.assertEqual((end - start).total_seconds(), 86400)

    def test_requires_all_configured_registers(self):
        point = outlet(registers=(1, 2), require_all_registers=True)
        self.assertEqual(self.status([shift(register=1)], point=point), "not_open")
        self.assertEqual(self.status([shift(register=1), shift(9, 3, register=2)], point=point), "late_open")
        self.assertEqual(self.status([shift(register=1), shift(register=2)], point=point), "on_time")

    def test_excluded_reserve_register_does_not_count(self):
        self.assertEqual(self.status([shift(register=99)], point=outlet(registers=(1,))), "not_open")

    def test_date_exception_overrides_week(self):
        point = outlet(exceptions={"2026-11-18": "off"})
        self.store.set_schedule(point.id, ["wed"], "08:00")
        self.assertEqual(self.status([], point=point), "day_off")
        self.store.set_schedule(point.id, ["2026-11-18"], "10:00")
        self.assertEqual(self.status([], point=point), "waiting")
        self.store.set_schedule(point.id, ["2026-11-18"], "reset")
        self.assertEqual(self.status([], point=point), "day_off")

    def test_invalid_time_is_rejected(self):
        for value in ("9:00", "24:00", "12:99", "oops"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                clock(value)


if __name__ == "__main__":
    unittest.main()
