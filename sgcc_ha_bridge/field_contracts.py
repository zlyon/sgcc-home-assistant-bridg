"""Evidence-backed SGCC field contracts shared by capture and parsing.

New parser aliases belong here only after a redacted Debug sample is turned
into a fixture and a positive/negative parser test.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldContract:
    category: str
    canonical_key: str
    aliases: tuple[str, ...]
    status: str
    evidence: str


FIELD_CONTRACTS = (
    FieldContract(
        category="account_balance",
        canonical_key="accountBalance",
        aliases=("accountBalance",),
        status="confirmed",
        evidence="tests/fixtures/sgcc/balance/account_balance.json",
    ),
    FieldContract(
        category="prepay_balance",
        canonical_key="prepayBal",
        aliases=("prepayBal", "prepayBalance"),
        status="confirmed",
        evidence="tests/fixtures/sgcc/balance/mixin_sum_money.json",
    ),
    FieldContract(
        category="arrears",
        canonical_key="historyOwe",
        aliases=("historyOwe",),
        status="confirmed",
        evidence="tests/fixtures/sgcc/balance/mixin_sum_money.json",
    ),
    FieldContract(
        category="account_balance",
        canonical_key="accountBalance",
        aliases=(
            "accountBal",
            "accountBalanceAmt",
            "acctBal",
            "acctBalance",
            "acctBalanceAmt",
            "balanceAmt",
            "availableBalance",
            "availableBal",
            "currentBalance",
            "curBalance",
            "remainBalance",
            "remainingBalance",
            "surplusBalance",
            "surplusAmt",
            "userBalance",
            "账户余额",
            "电费余额",
            "当前余额",
            "账户结余",
            "结余金额",
        ),
        status="legacy",
        evidence="pre-dynamic-parser compatibility; strict same-object context required",
    ),
    FieldContract(
        category="prepay_balance",
        canonical_key="prepayBal",
        aliases=(
            "prepay_balance",
            "prepayAmt",
            "prepaidBalance",
            "prepaidBal",
            "prepaidAmt",
            "prepaymentBalance",
            "advanceBalance",
            "advanceAmt",
            "预付费余额",
            "预存余额",
            "预存电费",
        ),
        status="legacy",
        evidence="pre-dynamic-parser compatibility; current-balance context required",
    ),
    FieldContract(
        category="arrears",
        canonical_key="historyOwe",
        aliases=(
            "arrears",
            "amountDue",
            "oweAmt",
            "oweAmount",
            "oweFee",
            "oweBalance",
            "payableAmt",
            "needPayAmt",
            "totalOwe",
            "欠费",
            "欠费金额",
            "应交金额",
            "待缴金额",
        ),
        status="legacy",
        evidence="pre-dynamic-parser compatibility; current-balance context required",
    ),
)

EXPLICIT_BALANCE_LABELS = ("账户余额", "您的账户余额", "电费余额")
EXPLICIT_PREPAY_LABELS = ("预付费余额",)
EXPLICIT_ARREARS_LABELS = ("应交金额",)
HISTORICAL_BALANCE_LABEL_TERMS = (
    "上月",
    "上期",
    "上次",
    "期初",
    "上年",
    "去年",
    "previous",
    "prev",
    "last",
)

# Structured shape confirmed by the mixin_sum_money fixture. ``sumMoney`` is
# not a free-standing amount alias and remains guarded by sibling context.
STRUCTURED_BALANCE_CAPTURE_KEYS = ("sumMoney", "estiAmt")
BALANCE_CONTEXT_CAPTURE_KEYS = (
    "queryTime",
    "amtTime",
    "accountNo",
    "acctNo",
    "address",
)


def field_keys(category: str, status: str) -> tuple[str, ...]:
    return tuple(
        key
        for contract in FIELD_CONTRACTS
        if contract.category == category and contract.status == status
        for key in contract.aliases
    )


def parser_capture_keys() -> tuple[str, ...]:
    values = [
        key
        for contract in FIELD_CONTRACTS
        for key in contract.aliases
    ]
    values.extend(STRUCTURED_BALANCE_CAPTURE_KEYS)
    values.extend(BALANCE_CONTEXT_CAPTURE_KEYS)
    return tuple(dict.fromkeys(values))
