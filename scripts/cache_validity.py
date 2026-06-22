"""Helpers for deciding whether legacy REST cache entries contain real SGCC data."""
from __future__ import annotations

from typing import Any

SCALAR_DATA_KEYS = (
    "balance",
    "last_daily_usage",
    "yearly_charge",
    "yearly_usage",
    "month_charge",
    "month_usage",
    "prepay_balance",
    "arrears",
)

TOU_ROW_DATA_KEYS = (
    "total_usage",
    "total_usage_kwh",
    "usage",
    "charge",
    "total_charge",
    "total_charge_cny",
    "valley_usage",
    "valley_usage_kwh",
    "flat_usage",
    "flat_usage_kwh",
    "peak_usage",
    "peak_usage_kwh",
    "tip_usage",
    "tip_usage_kwh",
)


def _has_value(value: Any) -> bool:
    return value not in (None, "")


def _rows_have_business_value(rows: Any) -> bool:
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if any(_has_value(row.get(key)) for key in TOU_ROW_DATA_KEYS):
            return True
    return False


def has_useful_legacy_cache_entry(values: Any) -> bool:
    """Return True only when a legacy sgcc_cache entry has publishable data."""
    if not isinstance(values, dict):
        return False

    if any(_has_value(values.get(key)) for key in SCALAR_DATA_KEYS):
        return True

    tou_data = values.get("tou_data")
    if isinstance(tou_data, dict):
        if _rows_have_business_value(tou_data.get("months")):
            return True
        if _rows_have_business_value(tou_data.get("daily")):
            return True
        for key in ("yearly_usage", "yearly_charge", "recent_total_usage"):
            if _has_value(tou_data.get(key)):
                return True

    enhanced_balance = values.get("enhanced_balance")
    if isinstance(enhanced_balance, dict):
        if _has_value(enhanced_balance.get("amount_due")):
            return True

    return False


def _object_values(obj: Any, keys: tuple[str, ...]) -> list[Any]:
    return [getattr(obj, key, None) for key in keys]


def has_useful_account_data(account_data: Any) -> bool:
    """Return True when AccountData contains at least one real SGCC business value.

    Account numbers and metadata alone are not enough. This guards successful
    fetch runs and MQTT cache republish from treating an empty parse as valid
    data. Zero is a valid business value.
    """
    if account_data is None:
        return False

    balance = getattr(account_data, "balance", None)
    if balance is not None and any(_has_value(value) for value in _object_values(
        balance,
        ("balance_cny", "prepay_balance_cny", "arrears_cny"),
    )):
        return True

    yearly = getattr(account_data, "yearly", None)
    if yearly is not None and any(_has_value(value) for value in _object_values(
        yearly,
        ("total_usage_kwh", "total_charge_cny"),
    )):
        return True

    for row in getattr(account_data, "monthly", []) or []:
        if any(_has_value(value) for value in _object_values(row, ("total_usage_kwh", "total_charge_cny"))):
            return True

    for row in getattr(account_data, "daily", []) or []:
        if any(_has_value(value) for value in _object_values(
            row,
            (
                "total_usage_kwh",
                "valley_usage_kwh",
                "flat_usage_kwh",
                "peak_usage_kwh",
                "tip_usage_kwh",
            ),
        )):
            return True

    return False


def _parse_date_prefix(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value)[:10]


def account_data_has_recent_cache_value(account_data: Any, *, today: str | None = None, max_age_days: int = 2) -> bool:
    """Return True when cached AccountData is recent enough to skip live SGCC fetch.

    Freshness is intentionally based on balance observed_at or daily reading
    date. Monthly/yearly values are useful to publish, but by themselves are
    too coarse to prove the cache is fresh.
    """
    from datetime import date, datetime

    if not has_useful_account_data(account_data):
        return False
    if today is None:
        today_date = date.today()
    else:
        today_date = date.fromisoformat(today[:10])

    def fresh(date_text: Any) -> bool:
        raw = _parse_date_prefix(date_text)
        if not raw:
            return False
        try:
            value_date = datetime.fromisoformat(raw).date()
        except ValueError:
            return False
        return 0 <= (today_date - value_date).days <= max_age_days

    balance = getattr(account_data, "balance", None)
    if balance is not None and any(_has_value(value) for value in _object_values(
        balance,
        ("balance_cny", "prepay_balance_cny", "arrears_cny"),
    )) and fresh(getattr(balance, "observed_at", None)):
        return True

    for row in getattr(account_data, "daily", []) or []:
        if any(_has_value(value) for value in _object_values(
            row,
            (
                "total_usage_kwh",
                "valley_usage_kwh",
                "flat_usage_kwh",
                "peak_usage_kwh",
                "tip_usage_kwh",
            ),
        )) and fresh(getattr(row, "date", None)):
            return True

    return False
