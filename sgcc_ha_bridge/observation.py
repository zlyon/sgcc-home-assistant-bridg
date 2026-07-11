"""Source-neutral observations used by SGCC extraction and debug capture."""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any, Iterable


_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*(?:元|度|kwh|cny))?$", re.IGNORECASE)
_ACCOUNT_RE = re.compile(r"^\d{13}$")
_DATE_RE = re.compile(r"^(?:20\d{2})(?:[-/]?\d{2})(?:[-/]?\d{2})?$")
_MONEY_TERMS = (
    "balance", "money", "amount", "charge", "fee", "owe", "arrears", "prepay", "payable",
    "amt", "cost", "price", "余额", "金额", "电费", "欠费", "应交", "预付", "预存", "缴费",
)
_USAGE_TERMS = (
    "usage", "electric", "power", "energy", "quantity", "kwh", "ele", "用电", "电量",
    "峰", "平", "谷", "尖",
)
_DATE_TERMS = ("date", "time", "month", "year", "period", "日期", "时间", "月份", "年度", "账期")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass(frozen=True)
class CaptureScope:
    id: str
    label: str
    account_no: str = ""
    url: str = ""
    started_at: str = field(default_factory=now_iso)

    @classmethod
    def create(cls, label: str, account_no: str = "", url: str = "") -> "CaptureScope":
        return cls(id=uuid.uuid4().hex[:16], label=label, account_no=account_no, url=url)


@dataclass
class Observation:
    source: str
    scope_id: str
    scope_label: str
    account_no: str = ""
    payload: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    observed_at: str = field(default_factory=now_iso)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParserDecision:
    source: str
    scope_id: str
    scope_label: str
    status: str
    reason: str
    account_no: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def iter_values(value: Any, path: str = "$", depth: int = 0, max_depth: int = 12) -> Iterable[tuple[str, Any]]:
    """Yield JSON-like values with stable paths while bounding hostile inputs."""
    yield path, value
    if depth >= max_depth:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_values(child, f"{path}.{_path_key(key)}", depth + 1, max_depth)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from iter_values(child, f"{path}[{index}]", depth + 1, max_depth)


def structure_shape(value: Any, max_depth: int = 10, max_items: int = 80) -> Any:
    """Return a value-free structural shape suitable for family clustering."""
    if is_dataclass(value):
        value = asdict(value)
    if max_depth <= 0:
        return {"type": _type_name(value), "truncated": "depth"}
    if isinstance(value, dict):
        items = list(itertools.islice(value.items(), max_items))
        return {
            "type": "object",
            "keys": {
                str(key): structure_shape(child, max_depth - 1, max_items)
                for key, child in items
            },
            "truncated": len(value) > max_items,
        }
    if isinstance(value, (list, tuple)):
        sample = value[: min(len(value), 5)]
        return {
            "type": "array",
            "length": len(value),
            "items": [structure_shape(item, max_depth - 1, max_items) for item in sample],
            "truncated": len(value) > len(sample),
        }
    return {"type": _type_name(value)}


def shape_fingerprint(value: Any) -> tuple[str, Any]:
    shape = structure_shape(value)
    encoded = json.dumps(shape, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16], shape


def collect_generic_candidates(value: Any, limit: int = 240) -> list[dict[str, Any]]:
    """Collect possible business leaves for debug; never publishes them."""
    candidates: list[dict[str, Any]] = []
    for path, leaf in iter_values(value):
        if isinstance(leaf, (dict, list, tuple)) or leaf in (None, ""):
            continue
        key = _path_leaf(path)
        category = _candidate_category(key, leaf)
        if category is None:
            continue
        candidates.append({
            "path": path,
            "key": key,
            "category": category,
            "value": leaf,
            "value_type": _type_name(leaf),
        })
        if len(candidates) >= limit:
            break
    return candidates


def _candidate_category(key: str, value: Any) -> str | None:
    key_lower = key.lower()
    text = str(value).strip()
    if _ACCOUNT_RE.fullmatch(text):
        return "account"
    if any(term in key_lower for term in _DATE_TERMS) or _DATE_RE.fullmatch(text):
        return "date_or_period"
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    numeric = numeric or bool(_NUMBER_RE.fullmatch(text))
    if not numeric:
        return None
    if any(term in key_lower for term in _MONEY_TERMS):
        return "money"
    if any(term in key_lower for term in _USAGE_TERMS):
        return "energy"
    return "generic_number"


def _path_key(value: Any) -> str:
    text = str(value)
    return text if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$-]*", text) else json.dumps(text, ensure_ascii=False)


def _path_leaf(path: str) -> str:
    if "." in path:
        return path.rsplit(".", 1)[-1].strip('"')
    return path.rsplit("[", 1)[-1].rstrip("]").strip('"')


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return type(value).__name__
