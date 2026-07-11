import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sgcc_ha_bridge.notify import UrlLoginQrCodeNotify, UrlPushNotify


class NotifyTestCase(unittest.TestCase):
    def test_url_push_notify_posts_when_balance_is_low(self):
        with patch.dict(os.environ, {"PUSH_URL": "http://notify.local/balance", "BALANCE": "10"}, clear=False):
            with patch("sgcc_ha_bridge.notify.requests.post", return_value=SimpleNamespace(status_code=200)) as post:
                self.assertTrue(UrlPushNotify()("test_user", 5.0))

        post.assert_called_once_with(
            "http://notify.local/balance",
            json={"user_id": "test_user", "balance": 5.0},
            timeout=10.0,
        )

    def test_url_login_qrcode_notify_posts_when_url_is_configured(self):
        with patch.dict(os.environ, {"PUSH_QRCODE_URL": "http://notify.local/qrcode"}, clear=False):
            with patch("sgcc_ha_bridge.notify.requests.post", return_value=SimpleNamespace(status_code=200)) as post:
                self.assertTrue(UrlLoginQrCodeNotify()(b"fake-png", "Test reason"))

        self.assertEqual(post.call_count, 1)
        _, kwargs = post.call_args
        self.assertEqual(kwargs["data"], {"reason": "Test reason"})
        self.assertIn("file", kwargs["files"])


if __name__ == "__main__":
    unittest.main()
