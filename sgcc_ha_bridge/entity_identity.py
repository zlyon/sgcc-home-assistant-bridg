"""Stable, privacy-preserving Home Assistant identity helpers."""
from __future__ import annotations

import hashlib


_ENTITY_NAMESPACE = "sgcc-home-assistant-bridge:entity:v2:"


def account_entity_key(account_no: str) -> str:
    """Return a stable unique key without exposing the full SGCC account number."""
    value = str(account_no or "").strip()
    if len(value) != 13 or not value.isdigit():
        raise ValueError("a full 13-digit SGCC account number is required for entity identity")
    digest = hashlib.sha256(f"{_ENTITY_NAMESPACE}{value}".encode("utf-8")).hexdigest()[:10]
    return f"{value[-4:]}_{digest}"


def account_entity_postfix(account_no: str) -> str:
    return f"_{account_entity_key(account_no)}"


def legacy_account_postfix(account_no: str) -> str:
    value = str(account_no or "").strip()
    return f"_{value[-4:]}" if value else ""
