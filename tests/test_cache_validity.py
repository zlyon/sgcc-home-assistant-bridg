import os
import sys
import unittest


from sgcc_ha_bridge.cache_validity import (
    account_data_has_recent_cache_value,
    has_useful_account_data,
    has_useful_legacy_cache_entry,
)
from sgcc_ha_bridge.model import Account, AccountData, Balance, DailyReading, MonthlyReading


class CacheValidityTestCase(unittest.TestCase):
    def test_empty_suffix_only_cache_is_not_useful(self):
        self.assertFalse(has_useful_legacy_cache_entry({
            "balance": None,
            "last_daily_date": None,
            "last_daily_usage": None,
            "yearly_charge": None,
            "yearly_usage": None,
            "month_charge": None,
            "month_usage": None,
            "timestamp": "2026-06-18T13:53:31",
            "tou_data": {"months": [], "daily": [], "yearly_usage": None, "yearly_charge": None},
            "enhanced_balance": {"as_of": None, "amount_due": None, "user_id": None},
        }))

    def test_metadata_without_business_value_is_not_useful(self):
        self.assertFalse(has_useful_legacy_cache_entry({
            "last_daily_date": "2026-06-17",
            "enhanced_balance": {"as_of": "2026-06-18T14:00:00", "user_id": "5001657384840"},
        }))
        self.assertFalse(has_useful_legacy_cache_entry({
            "tou_data": {"daily": [{"date": "2026-06-17"}], "months": [{"year_month": "2026-05"}]},
        }))

    def test_zero_business_values_are_useful(self):
        self.assertTrue(has_useful_legacy_cache_entry({"balance": 0}))
        self.assertTrue(has_useful_legacy_cache_entry({"enhanced_balance": {"amount_due": 0}}))
        self.assertTrue(has_useful_legacy_cache_entry({"tou_data": {"daily": [{"date": "2026-06-17", "tip_usage": 0}]}}))

    def test_scalar_or_tou_data_cache_is_useful(self):
        self.assertTrue(has_useful_legacy_cache_entry({"last_daily_usage": 1.23}))
        self.assertTrue(has_useful_legacy_cache_entry({"tou_data": {"daily": [{"date": "2026-06-17", "total_usage": 1.23}]}}))


class AccountDataValidityTestCase(unittest.TestCase):
    def test_account_only_is_not_useful(self):
        data = AccountData(account=Account(account_no="1234567890123"))
        self.assertFalse(has_useful_account_data(data))

    def test_zero_balance_is_useful_business_value(self):
        data = AccountData(
            account=Account(account_no="1234567890123"),
            balance=Balance(
                account_no="1234567890123",
                observed_at="2026-06-21T08:00:00+08:00",
                balance_cny=0,
            ),
        )
        self.assertTrue(has_useful_account_data(data))

    def test_zero_daily_usage_is_useful_business_value(self):
        data = AccountData(
            account=Account(account_no="1234567890123"),
            daily=[DailyReading(account_no="1234567890123", date="2026-06-20", total_usage_kwh=0)],
        )
        self.assertTrue(has_useful_account_data(data))

    def test_recent_daily_cache_can_skip_live_fetch(self):
        data = AccountData(
            account=Account(account_no="1234567890123"),
            daily=[DailyReading(account_no="1234567890123", date="2026-06-20", total_usage_kwh=1.2)],
        )
        self.assertTrue(account_data_has_recent_cache_value(data, today="2026-06-21"))

    def test_stale_daily_cache_cannot_skip_live_fetch(self):
        data = AccountData(
            account=Account(account_no="1234567890123"),
            daily=[DailyReading(account_no="1234567890123", date="2026-06-10", total_usage_kwh=1.2)],
        )
        self.assertFalse(account_data_has_recent_cache_value(data, today="2026-06-21"))

    def test_monthly_only_is_useful_but_not_fresh_enough(self):
        data = AccountData(
            account=Account(account_no="1234567890123"),
            monthly=[MonthlyReading(account_no="1234567890123", year_month="2026-06", total_usage_kwh=12.3)],
        )
        self.assertTrue(has_useful_account_data(data))
        self.assertFalse(account_data_has_recent_cache_value(data, today="2026-06-21"))


if __name__ == "__main__":
    unittest.main()
