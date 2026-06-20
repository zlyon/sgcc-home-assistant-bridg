import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from ha_mapping import account_data_to_update_args, with_history_daily_if_empty
from model import Account, AccountData, DailyReading


class FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_daily(self, account_no, limit=31):
        self.calls.append((account_no, limit))
        return list(self.rows)


class HistoryDailyBackfillTestCase(unittest.TestCase):
    def test_with_history_daily_if_empty_uses_store_for_publish_copy(self):
        account_data = AccountData(account=Account(account_no="1234567890123"), daily=[])
        history = [
            DailyReading(account_no="1234567890123", date="2026-06-18", total_usage_kwh=8.0),
            DailyReading(account_no="1234567890123", date="2026-06-19", total_usage_kwh=9.5),
        ]
        store = FakeStore(history)

        publish_data = with_history_daily_if_empty(account_data, store, limit=31)
        update_args = account_data_to_update_args(publish_data)

        self.assertIsNot(publish_data, account_data)
        self.assertEqual(account_data.daily, [])
        self.assertEqual(store.calls, [("1234567890123", 31)])
        self.assertEqual(update_args["last_daily_date"], "2026-06-19")
        self.assertEqual(update_args["last_daily_usage"], 9.5)
        self.assertIsNotNone(update_args["tou_data"])
        self.assertEqual(len(update_args["tou_data"]["daily"]), 2)

    def test_with_history_daily_if_empty_does_not_query_when_current_daily_exists(self):
        current = [DailyReading(account_no="1234567890123", date="2026-06-20", total_usage_kwh=1.0)]
        account_data = AccountData(account=Account(account_no="1234567890123"), daily=current)
        store = FakeStore([])

        self.assertIs(with_history_daily_if_empty(account_data, store), account_data)
        self.assertEqual(store.calls, [])


if __name__ == "__main__":
    unittest.main()
