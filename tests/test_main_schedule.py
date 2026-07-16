import logging
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

sys.modules.setdefault("schedule", SimpleNamespace())
_data_fetcher_stub = ModuleType("sgcc_ha_bridge.data_fetcher")
_data_fetcher_stub.DataFetcher = object
sys.modules.setdefault("sgcc_ha_bridge.data_fetcher", _data_fetcher_stub)

from sgcc_ha_bridge import main


class DailyScheduleJitterTestCase(unittest.TestCase):
    def test_missing_value_keeps_legacy_ten_minute_window(self):
        with patch.dict(main.os.environ, {}, clear=True):
            self.assertEqual(main._daily_jitter_minutes(), 10)

    def test_zero_disables_random_sampling(self):
        randint = Mock()

        offset, run_times = main._daily_schedule_times("07:00", 0, randint=randint)

        self.assertEqual(offset, 0)
        self.assertEqual(run_times, ["07:00"])
        randint.assert_not_called()

    def test_valid_window_is_passed_to_random_sampler(self):
        randint = Mock(return_value=-37)

        offset, run_times = main._daily_schedule_times("07:00", 60, randint=randint)

        self.assertEqual(offset, -37)
        self.assertEqual(run_times, ["06:23"])
        randint.assert_called_once_with(-60, 60)

    def test_valid_boundaries_are_accepted(self):
        self.assertEqual(main._daily_jitter_minutes("0"), 0)
        self.assertEqual(main._daily_jitter_minutes("180"), 180)

    def test_invalid_values_fall_back_to_legacy_default(self):
        for raw_value in ("", "abc", "-1", "181", None):
            with self.subTest(raw_value=raw_value), self.assertLogs(level=logging.WARNING):
                if raw_value is None:
                    with patch.dict(main.os.environ, {"SGCC_DAILY_JITTER_MINUTES": ""}, clear=True):
                        result = main._daily_jitter_minutes()
                else:
                    result = main._daily_jitter_minutes(raw_value)
                self.assertEqual(result, 10)

    def test_two_daily_runs_share_offset_and_remain_twelve_hours_apart(self):
        randint = Mock(return_value=8)

        offset, run_times = main._daily_schedule_times("07:00", 10, daily_runs=2, randint=randint)

        self.assertEqual(offset, 8)
        self.assertEqual(run_times, ["07:08", "19:08"])
        randint.assert_called_once_with(-10, 10)

    def test_schedule_time_wraps_across_midnight(self):
        randint = Mock(return_value=-10)

        _, run_times = main._daily_schedule_times("00:05", 10, daily_runs=2, randint=randint)

        self.assertEqual(run_times, ["23:55", "11:55"])


if __name__ == "__main__":
    unittest.main()
