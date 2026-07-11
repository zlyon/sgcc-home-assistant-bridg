import unittest

from sgcc_ha_bridge.money_candidates import collect_money_candidates


class MoneyCandidatesTestCase(unittest.TestCase):
    def test_collects_structured_money_candidates_by_category(self):
        store = {
            "state": {
                "userAcc": {
                    "accountNo": "1234567890016",
                    "queryTime": "2026-07-07 00:00:00",
                    "accountBalance": "86.44元",
                    "address": "secret address",
                },
                "mixinGetYuEdata": {
                    "consNo": "1234567890016",
                    "historyOwe": "0.00",
                },
                "powerData": {
                    "mothEleList": [
                        {
                            "month": "202606",
                            "monthEleCost": "212.33",
                            "monthEleNum": "423.0",
                        }
                    ]
                },
            }
        }
        components = [
            {"data": {"consNo": "1234567890016", "prepayBalance": "217.7"}}
        ]

        candidates = collect_money_candidates(store=store, components=components)
        by_source = {item.source: item for item in candidates}

        self.assertEqual(by_source["store.state.userAcc.accountBalance"].category, "account_balance")
        self.assertEqual(by_source["store.state.mixinGetYuEdata.historyOwe"].category, "arrears_due")
        self.assertEqual(by_source["store.state.powerData.mothEleList[0].monthEleCost"].category, "bill_charge")
        self.assertEqual(by_source["component[0].data.prepayBalance"].category, "prepay_balance")
        self.assertNotIn("store.state.powerData.mothEleList[0].monthEleNum", by_source)
        self.assertEqual(by_source["store.state.userAcc.accountBalance"].account, "*********0016")
        self.assertEqual(by_source["store.state.userAcc.accountBalance"].time, "2026-07-07 00:00:00")

    def test_sum_money_candidate_is_classified_as_account_balance_in_yue_context(self):
        components = [
            {
                "data": {
                    "mixinGetYuEdata": {
                        "consNo": "1234567897516",
                        "amtTime": "2026-07-07 03:14:50",
                        "historyOwe": "0",
                        "prepayBal": "0",
                        "sumMoney": "169.77",
                    }
                }
            }
        ]

        candidates = collect_money_candidates(components=components)
        by_source = {item.source: item for item in candidates}

        self.assertEqual(
            by_source["component[0].data.mixinGetYuEdata.sumMoney"].category,
            "account_balance",
        )
        self.assertEqual(
            by_source["component[0].data.mixinGetYuEdata.sumMoney"].time,
            "2026-07-07 03:14:50",
        )

    def test_limit_is_applied(self):
        store = {"state": {f"accountBalance{i}": str(i) for i in range(10)}}
        candidates = collect_money_candidates(store=store, components=[], limit=3)
        self.assertEqual(len(candidates), 3)

    def test_long_service_identifier_is_not_a_money_candidate(self):
        components = [{
            "data": {
                "onlineShop": {
                    "label": "电费缴纳",
                    "SERVICEID": "D250101123456789012301",
                }
            }
        }]

        candidates = collect_money_candidates(components=components)

        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
