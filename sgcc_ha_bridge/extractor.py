"""Source-aware AccountData extraction with account-scope isolation."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

from .cache_validity import has_useful_account_data
from .model import Account, AccountData, Balance
from .observation import CaptureScope, Observation, ParserDecision
from .parser import (
    balance_values_from_explicit_label_row,
    merge_account_data,
    parse_account_data,
)


_AMOUNT_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")


@dataclass
class ExtractionResult:
    data: AccountData
    decisions: list[ParserDecision]


def extract_account_data(scope: CaptureScope, observations: Iterable[Observation]) -> ExtractionResult:
    values = list(observations)
    decisions: list[ParserDecision] = []
    vuex_payloads = [item.payload for item in values if item.source == "vuex"]
    component_payloads = [item.payload for item in values if item.source == "component"]
    dom_payloads = [item.payload for item in values if item.source == "dom"]
    network_items = [item for item in values if item.source == "network"]

    candidates: list[tuple[int, AccountData]] = []

    if vuex_payloads or component_payloads:
        store = _combine_store_payloads(vuex_payloads)
        components = _combine_component_payloads(component_payloads)
        parsed = parse_account_data(store=store, components=components)
        accepted, reason, normalized = _validate_scope(parsed, scope)
        decisions.append(ParserDecision(
            source="vue",
            scope_id=scope.id,
            scope_label=scope.label,
            account_no=scope.account_no,
            status="accepted" if accepted else "rejected",
            reason=reason,
            metadata=_summary(parsed),
        ))
        if accepted and has_useful_account_data(normalized):
            candidates.append((20, normalized))

    for item in network_items:
        parsed = parse_account_data(store=item.payload)
        accepted, reason, normalized = _validate_scope(parsed, scope)
        useful = has_useful_account_data(normalized)
        if accepted and useful:
            decision_reason = "known structured payload"
        elif accepted:
            decision_reason = "known parser found no useful business data"
        else:
            decision_reason = reason
        decisions.append(ParserDecision(
            source="network",
            scope_id=scope.id,
            scope_label=scope.label,
            account_no=scope.account_no,
            status="accepted" if accepted and useful else "rejected",
            reason=decision_reason,
            metadata={**item.metadata, **_summary(parsed)},
        ))
        if accepted and useful:
            candidates.append((30, normalized))

    dom_data = _parse_dom_balance(scope, dom_payloads)
    if dom_data is not None:
        decisions.append(ParserDecision(
            source="dom",
            scope_id=scope.id,
            scope_label=scope.label,
            account_no=scope.account_no,
            status="accepted",
            reason="explicit label/value fallback",
            metadata=_summary(dom_data),
        ))
        candidates.append((10, dom_data))

    if not candidates:
        account = Account(account_no=scope.account_no)
        return ExtractionResult(AccountData(account=account), decisions)

    # Lower-priority sources merge first; later values win on duplicate fields.
    ordered = [item for _, item in sorted(candidates, key=lambda pair: pair[0])]
    try:
        merged = merge_account_data(*ordered)
    except ValueError:
        merged = next(
            (item for priority, item in sorted(candidates, key=lambda pair: pair[0], reverse=True)),
            AccountData(account=Account(account_no=scope.account_no)),
        )
    return ExtractionResult(_fill_scope_account(merged, scope.account_no), decisions)


def _combine_store_payloads(payloads: list[Any]) -> Any:
    if not payloads:
        return {}
    if len(payloads) == 1:
        return payloads[0]
    snapshots = []
    for payload in payloads:
        if isinstance(payload, dict) and any(key in payload for key in ("state", "getters", "snapshots")):
            snapshots.append(payload)
        else:
            snapshots.append({"state": payload})
    return {"snapshots": snapshots}


def _combine_component_payloads(payloads: list[Any]) -> list[Any]:
    result: list[Any] = []
    for payload in payloads:
        if isinstance(payload, list):
            result.extend(payload)
        elif payload:
            result.append(payload)
    return result


def _validate_scope(data: AccountData, scope: CaptureScope) -> tuple[bool, str, AccountData]:
    parsed_account = data.account.account_no if data and data.account else ""
    target = scope.account_no
    if target and parsed_account and "*" not in parsed_account and parsed_account != target:
        return False, "payload account differs from capture scope", data
    return True, "account matches scope or payload has no full identity", _fill_scope_account(data, target)


def _fill_scope_account(data: AccountData, account_no: str) -> AccountData:
    if not account_no:
        return data
    account = data.account
    if not account.account_no or "*" in account.account_no:
        account = replace(account, account_no=account_no)
    balance = data.balance
    if balance and (not balance.account_no or "*" in balance.account_no):
        balance = replace(balance, account_no=account_no)
    yearly = data.yearly
    if yearly and (not yearly.account_no or "*" in yearly.account_no):
        yearly = replace(yearly, account_no=account_no)
    monthly = [
        replace(row, account_no=account_no) if not row.account_no or "*" in row.account_no else row
        for row in data.monthly
    ]
    daily = [
        replace(row, account_no=account_no) if not row.account_no or "*" in row.account_no else row
        for row in data.daily
    ]
    return AccountData(account=account, balance=balance, yearly=yearly, monthly=monthly, daily=daily)


def _parse_dom_balance(scope: CaptureScope, payloads: list[Any]) -> AccountData | None:
    if scope.label != "账户余额":
        return None
    for payload in payloads:
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            values = balance_values_from_explicit_label_row(item)
            value = values.get("accountBalance")
            if value is None:
                continue
            match = _AMOUNT_RE.search(str(value))
            if not match:
                continue
            amount = float(match.group(0))
            return AccountData(
                account=Account(account_no=scope.account_no),
                balance=Balance(
                    account_no=scope.account_no,
                    observed_at=scope.started_at,
                    balance_cny=amount,
                ),
            )
    return None


def _summary(data: AccountData) -> dict[str, Any]:
    return {
        "account_present": bool(data.account.account_no),
        "balance_present": data.balance is not None,
        "monthly_count": len(data.monthly),
        "daily_count": len(data.daily),
        "yearly_present": data.yearly is not None,
    }
