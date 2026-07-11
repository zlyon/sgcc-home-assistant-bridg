import json
from pathlib import Path

from sgcc_ha_bridge.extractor import extract_account_data
from sgcc_ha_bridge.observation import CaptureScope, Observation


FIXTURES = Path(__file__).parent / "fixtures" / "sgcc"
ACCOUNT = "1234567890123"


def observation(scope, source, payload):
    return Observation(
        source=source,
        scope_id=scope.id,
        scope_label=scope.label,
        account_no=scope.account_no,
        payload=payload,
    )


def test_network_payload_parses_issue_12_balance_family():
    payload = json.loads((FIXTURES / "balance" / "mixin_sum_money.json").read_text())
    scope = CaptureScope.create("账户余额", ACCOUNT)
    result = extract_account_data(scope, [observation(scope, "network", payload)])
    assert result.data.balance.balance_cny == 869.18
    assert result.data.balance.prepay_balance_cny == 916.32
    assert result.data.balance.arrears_cny == 0
    assert result.decisions[0].status == "accepted"


def test_network_payload_can_fill_vue_missing_balance():
    network = json.loads((FIXTURES / "balance" / "account_balance.json").read_text())
    vue = {
        "state": {
            "powerData": {
                "consNo": ACCOUNT,
                "dataInfo": {
                    "year": "2026",
                    "totalEleNum": "100",
                    "totalEleCost": "55"
                },
                "mothEleList": []
            }
        }
    }
    scope = CaptureScope.create("账户余额", ACCOUNT)
    result = extract_account_data(scope, [
        observation(scope, "vuex", vue),
        observation(scope, "network", network),
    ])
    assert result.data.balance.balance_cny == 23.46
    assert result.data.yearly.total_usage_kwh == 100


def test_cross_account_network_payload_is_rejected():
    payload = json.loads((FIXTURES / "balance" / "mixin_sum_money.json").read_text())
    scope = CaptureScope.create("账户余额", "1234567899999")
    result = extract_account_data(scope, [observation(scope, "network", payload)])
    assert result.data.balance is None
    assert result.decisions[0].status == "rejected"
    assert "differs" in result.decisions[0].reason


def test_unknown_money_is_not_published():
    payload = json.loads((FIXTURES / "unknown" / "jiangsu_unmapped.json").read_text())
    scope = CaptureScope.create("账户余额", ACCOUNT)
    result = extract_account_data(scope, [observation(scope, "network", payload)])
    assert result.data.balance is None
    assert result.decisions[0].status == "rejected"


def test_explicit_dom_balance_is_last_resort():
    scope = CaptureScope.create("账户余额", ACCOUNT)
    result = extract_account_data(scope, [
        observation(scope, "dom", [{"label": "您的账户余额", "value": "36.27元"}]),
    ])
    assert result.data.balance.balance_cny == 36.27


def test_dom_historical_balance_is_rejected():
    scope = CaptureScope.create("账户余额", ACCOUNT)
    result = extract_account_data(scope, [
        observation(scope, "dom", [{"label": "上月账户余额", "value": "86.44元"}]),
    ])

    assert result.data.balance is None


def test_network_partial_balance_preserves_vue_complementary_fields():
    scope = CaptureScope.create("账户余额", ACCOUNT)
    vue = {
        "state": {
            "balance": {
                "consNo": ACCOUNT,
                "accountBalance": "80",
                "prepayBal": "12",
                "historyOwe": "3",
            }
        }
    }
    network = {
        "result": {
            "userAcc": {
                "accountNo": ACCOUNT,
                "queryTime": "2026-07-11 10:00:00",
                "accountBalance": "88",
            }
        }
    }

    result = extract_account_data(scope, [
        observation(scope, "vuex", vue),
        observation(scope, "network", network),
    ])

    assert result.data.balance.balance_cny == 88
    assert result.data.balance.prepay_balance_cny == 12
    assert result.data.balance.arrears_cny == 3
