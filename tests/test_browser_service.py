import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


class BrowserServiceConfigTestCase(unittest.TestCase):
    def _load_browser_service(self, **env):
        old_env = os.environ.copy()
        os.environ.update(env)
        sys.modules.pop("browser_service", None)
        try:
            return importlib.import_module("browser_service")
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_cdp_forward_disabled_keeps_existing_port_and_host(self):
        browser_service = self._load_browser_service(
            SGCC_BROWSER_CDP_HOST="0.0.0.0",
            SGCC_BROWSER_CDP_PORT="19222",
            SGCC_BROWSER_CDP_FORWARD_ENABLED="false",
        )

        self.assertFalse(browser_service.CDP_FORWARD_ENABLED)
        self.assertEqual(browser_service._chrome_cdp_host(), "0.0.0.0")
        self.assertEqual(browser_service._chrome_cdp_port(), 19222)
        self.assertEqual(browser_service._cdp_probe_host(), "127.0.0.1")

    def test_cdp_forward_enabled_uses_internal_loopback_port(self):
        browser_service = self._load_browser_service(
            SGCC_BROWSER_CDP_HOST="0.0.0.0",
            SGCC_BROWSER_CDP_PORT="19222",
            SGCC_BROWSER_CDP_INTERNAL_PORT="19223",
            SGCC_BROWSER_CDP_FORWARD_ENABLED="true",
        )

        self.assertTrue(browser_service.CDP_FORWARD_ENABLED)
        self.assertEqual(browser_service._chrome_cdp_host(), "127.0.0.1")
        self.assertEqual(browser_service._chrome_cdp_port(), 19223)
        self.assertEqual(browser_service._cdp_forward_bind_host(), "0.0.0.0")

    def test_cdp_payload_rewrites_websocket_debugger_url_to_public_host(self):
        browser_service = self._load_browser_service(
            SGCC_BROWSER_CDP_PORT="19222",
            SGCC_BROWSER_CDP_INTERNAL_PORT="19223",
            SGCC_BROWSER_CDP_FORWARD_ENABLED="true",
        )

        body = b'{"webSocketDebuggerUrl":"ws://127.0.0.1:19223/devtools/browser/abc"}'
        rewritten = browser_service._rewrite_cdp_payload(body, "sgcc_browser:19222")

        self.assertIn(b"ws://sgcc_browser:19222/devtools/browser/abc", rewritten)
        self.assertNotIn(b"127.0.0.1:19223", rewritten)

    def test_cdp_forward_rejects_same_public_and_internal_port(self):
        browser_service = self._load_browser_service(
            SGCC_BROWSER_CDP_PORT="19222",
            SGCC_BROWSER_CDP_INTERNAL_PORT="19222",
            SGCC_BROWSER_CDP_FORWARD_ENABLED="true",
        )

        with self.assertRaisesRegex(RuntimeError, "requires"):
            browser_service._start_cdp_forwarder()

    def test_cdp_forward_starts_threaded_proxy_when_enabled(self):
        browser_service = self._load_browser_service(
            SGCC_BROWSER_CDP_HOST="127.0.0.1",
            SGCC_BROWSER_CDP_PORT="29222",
            SGCC_BROWSER_CDP_INTERNAL_PORT="29223",
            SGCC_BROWSER_CDP_FORWARD_ENABLED="true",
        )

        try:
            browser_service._start_cdp_forwarder()
            self.assertTrue(browser_service._cdp_forward_running())
            server, thread = browser_service._cdp_forward_proc
            self.assertEqual(server.server_address[1], 29222)
            self.assertTrue(thread.is_alive())
        finally:
            browser_service._stop_cdp_forwarder()


if __name__ == "__main__":
    unittest.main()
