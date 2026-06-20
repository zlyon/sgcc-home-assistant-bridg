import json
import tempfile
import unittest
from pathlib import Path

from sensor_updator import SensorUpdator


class CacheOnlySensorUpdator(SensorUpdator):
    def __init__(self, cache_file: Path):
        self.cache_file = cache_file
        self.balance_notify = None
        self.calls = []

    def _get_cache_file(self):
        return str(self.cache_file)

    def should_update(self, sensor_name, new_state, check_attributes=None):
        return True

    def update_balance(self, postfix, sensorState, enhanced_balance=None):
        self.calls.append(("balance", postfix, sensorState, enhanced_balance))

    def update_last_daily_usage(self, postfix, last_daily_date, sensorState):
        self.calls.append(("daily", postfix, last_daily_date, sensorState))

    def update_yearly_data(self, postfix, sensorState, usage=False):
        self.calls.append(("yearly_usage" if usage else "yearly_charge", postfix, sensorState))

    def update_month_data(self, postfix, sensorState, usage=False):
        self.calls.append(("month_usage" if usage else "month_charge", postfix, sensorState))

    def _update_tou_sensors(self, postfix, tou_data):
        self.calls.append(("tou", postfix, tou_data))

    def update_prepay_balance(self, postfix, sensorState):
        self.calls.append(("prepay", postfix, sensorState))


class SensorUpdatorCacheValuesTestCase(unittest.TestCase):
    def test_cache_values_keep_publish_backfill_out_of_legacy_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "sgcc_cache.json"
            updator = CacheOnlySensorUpdator(cache_file)

            updator.update_one_userid(
                user_id="1234567890123",
                balance=None,
                last_daily_date="2026-06-18",
                last_daily_usage=9.5,
                yearly_charge=None,
                yearly_usage=None,
                month_charge=None,
                month_usage=None,
                tou_data={"daily": [{"date": "2026-06-18", "total_usage": 9.5}]},
                enhanced_balance=None,
                cache_values={
                    "user_id": "1234567890123",
                    "balance": None,
                    "last_daily_date": None,
                    "last_daily_usage": None,
                    "yearly_charge": None,
                    "yearly_usage": None,
                    "month_charge": None,
                    "month_usage": None,
                    "tou_data": None,
                    "enhanced_balance": None,
                },
            )

            self.assertFalse(cache_file.exists())
            self.assertIn(("daily", "_0123", "2026-06-18", 9.5), updator.calls)
            self.assertTrue(any(call[0] == "tou" for call in updator.calls))

    def test_default_cache_values_preserve_existing_republish_behavior(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "sgcc_cache.json"
            updator = CacheOnlySensorUpdator(cache_file)

            updator.update_one_userid(
                user_id="1234567890123",
                balance=88.0,
                last_daily_date="2026-06-20",
                last_daily_usage=1.2,
                yearly_charge=None,
                yearly_usage=None,
                month_charge=None,
                month_usage=None,
            )

            data = json.loads(cache_file.read_text())
            entry = data["1234567890123"]
            self.assertEqual(entry["balance"], 88.0)
            self.assertEqual(entry["last_daily_date"], "2026-06-20")
            self.assertEqual(entry["last_daily_usage"], 1.2)


if __name__ == "__main__":
    unittest.main()
