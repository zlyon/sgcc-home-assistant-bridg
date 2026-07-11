import json
from pathlib import Path

from sgcc_ha_bridge.error_watcher import ErrorWatcher
from sgcc_ha_bridge.redact import redact_text


class FakeDriver:
    current_url = "https://95598.cn/osgweb/userAcc?token=secret"
    title = "国网 1234567890123"

    def __init__(self):
        self.screenshot_calls = 0

    def get_log(self, name):
        return [{
            "level": "SEVERE",
            "message": "Authorization: Bearer top-secret account 1234567890123",
        }]

    def save_screenshot(self, path):
        self.screenshot_calls += 1
        Path(path).write_bytes(b"png")
        return True


def test_error_watcher_saves_only_redacted_metadata_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SGCC_ERROR_SCREENSHOT", raising=False)
    driver = FakeDriver()
    watcher = ErrorWatcher(root_dir=tmp_path, driver=driver)

    capture_dir = Path(watcher.capture(
        "login_failed",
        "password=plain token=secret account=1234567890123",
    ))

    assert capture_dir.is_dir()
    assert not (capture_dir / "page.html").exists()
    assert not (capture_dir / "screenshot.png").exists()
    assert driver.screenshot_calls == 0
    meta = json.loads((capture_dir / "meta.redacted.json").read_text())
    text = json.dumps(meta, ensure_ascii=False)
    assert "1234567890123" not in text
    assert "plain" not in text
    assert "top-secret" not in text
    assert meta["browser_log_summary"] == {
        "count": 1,
        "levels": {"SEVERE": 1},
    }


def test_error_watcher_prunes_old_capture_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("SGCC_ERROR_MAX_CAPTURES", "2")
    watcher = ErrorWatcher(root_dir=tmp_path, driver=FakeDriver())

    for index in range(3):
        watcher.capture(f"failure-{index}", "failed")

    assert len([path for path in tmp_path.iterdir() if path.is_dir()]) == 2


def test_redact_text_removes_long_numeric_identifiers_with_embedded_account():
    value = "id=prefix250101123456789012301suffix"

    redacted = redact_text(value)

    assert "1234567890123" not in redacted
    assert "250101123456789012301" not in redacted
    assert "<redacted-numeric-id>" in redacted
