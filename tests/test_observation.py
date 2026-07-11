import json
from pathlib import Path

from sgcc_ha_bridge.observation import collect_generic_candidates, shape_fingerprint


FIXTURES = Path(__file__).parent / "fixtures" / "sgcc"


def test_shape_fingerprint_is_value_agnostic():
    first = {"data": {"amount": 12.3, "rows": [{"date": "2026-01-01", "value": 1}]}}
    second = {"data": {"amount": 99.9, "rows": [{"date": "2026-02-02", "value": 8}]}}
    assert shape_fingerprint(first)[0] == shape_fingerprint(second)[0]


def test_unknown_payload_is_visible_to_debug_candidates():
    payload = json.loads((FIXTURES / "unknown" / "jiangsu_unmapped.json").read_text())
    candidates = collect_generic_candidates(payload)
    paths = {item["path"] for item in candidates}
    assert "$.data.realAccountValue" in paths
    assert "$.data.accountNo" in paths
    assert "$.data.queryTime" in paths
