"""短信中转交互的测试。"""

import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sgcc_ha_bridge.login_interaction import (
    NoopLoginInteraction,
    RacingLoginInteraction,
    SmsRelayLoginInteraction,
    TelegramLoginInteraction,
    build_login_interaction,
)


def response(payload, status_code=200):
    return SimpleNamespace(status_code=status_code, json=lambda: payload)


def code_payload(code=None, now=1_700_000_000_000, seq=1, timeout=False):
    return {
        "ok": True,
        "now": now,
        "code": code,
        "seq": seq,
        "from": "10690300",
        "content": "【网上国网】%s，您申请的验证码" % code if code else "",
        "timeout": timeout,
    }


class SmsRelayFromEnvTestCase(unittest.TestCase):
    def test_builds_from_bare_host_port(self):
        with patch.dict(os.environ, {"SGCC_SMS_RELAY_URL": "192.168.50.150:8088"}, clear=True):
            interaction = SmsRelayLoginInteraction.from_env()

        self.assertIsInstance(interaction, SmsRelayLoginInteraction)
        self.assertEqual(interaction.base_url, "http://192.168.50.150:8088")

    def test_strips_trailing_slash_and_reads_token(self):
        with patch.dict(os.environ, {
            "SGCC_SMS_RELAY_URL": "http://sms-relay-host:8088/",
            "SGCC_SMS_RELAY_TOKEN": "s3cret",
        }, clear=True):
            interaction = SmsRelayLoginInteraction.from_env()

        self.assertEqual(interaction.base_url, "http://sms-relay-host:8088")
        self.assertEqual(interaction.token, "s3cret")

    def test_rejects_credentialed_url(self):
        with patch.dict(os.environ, {
            "SGCC_SMS_RELAY_URL": "http://user:pass@host:8088",
        }, clear=True):
            self.assertIsNone(SmsRelayLoginInteraction.from_env())

    def test_missing_url_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(SmsRelayLoginInteraction.from_env())


class BuildLoginInteractionTestCase(unittest.TestCase):
    def test_smsrelay_provider_returns_relay(self):
        with patch.dict(os.environ, {
            "SGCC_LOGIN_INTERACTION_PROVIDER": "smsrelay",
            "SGCC_SMS_RELAY_URL": "http://127.0.0.1:8088",
        }, clear=True):
            interaction = build_login_interaction()

        self.assertIsInstance(interaction, SmsRelayLoginInteraction)

    def test_smsrelay_provider_without_url_falls_back_to_noop(self):
        with patch.dict(os.environ, {
            "SGCC_LOGIN_INTERACTION_PROVIDER": "smsrelay",
        }, clear=True):
            interaction = build_login_interaction()

        self.assertIsInstance(interaction, NoopLoginInteraction)

    def test_disabled_by_default_even_when_url_is_configured(self):
        """只配 URL 不算打开：必须是显式的 ENABLED=true，别人的部署不会被带上。"""
        with patch.dict(os.environ, {
            "SGCC_LOGIN_INTERACTION_PROVIDER": "auto",
            "SGCC_SMS_RELAY_URL": "http://127.0.0.1:8088",
        }, clear=True):
            self.assertIsInstance(build_login_interaction(), NoopLoginInteraction)

    def test_explicit_false_keeps_it_off(self):
        with patch.dict(os.environ, {
            "SGCC_LOGIN_INTERACTION_PROVIDER": "auto",
            "SGCC_SMS_RELAY_URL": "http://127.0.0.1:8088",
            "SGCC_SMS_RELAY_ENABLED": "false",
        }, clear=True):
            self.assertIsInstance(build_login_interaction(), NoopLoginInteraction)

    def test_auto_with_flag_races_relay_and_telegram(self):
        with patch.dict(os.environ, {
            "SGCC_LOGIN_INTERACTION_PROVIDER": "auto",
            "SGCC_SMS_RELAY_URL": "http://127.0.0.1:8088",
            "SGCC_SMS_RELAY_ENABLED": "true",
            "SGCC_TELEGRAM_BOT_TOKEN": "123456:secret",
            "SGCC_TELEGRAM_CHAT_ID": "42",
        }, clear=True):
            interaction = build_login_interaction()

        self.assertIsInstance(interaction, RacingLoginInteraction)
        self.assertEqual(len(interaction.racers), 2)
        self.assertIsInstance(interaction.racers[0], SmsRelayLoginInteraction)
        self.assertIsInstance(interaction.racers[1], TelegramLoginInteraction)

    def test_relay_only_when_no_telegram(self):
        with patch.dict(os.environ, {
            "SGCC_LOGIN_INTERACTION_PROVIDER": "auto",
            "SGCC_SMS_RELAY_URL": "http://127.0.0.1:8088",
            "SGCC_SMS_RELAY_ENABLED": "1",
        }, clear=True):
            interaction = build_login_interaction()

        self.assertIsInstance(interaction, SmsRelayLoginInteraction)


class FakeChannel:
    """可控的假通道：delay 秒后返回 code，等待期间响应取消。"""

    def __init__(self, code=None, delay=0.0, boom=False):
        self.code = code
        self.delay = delay
        self.boom = boom
        self.cancelled = False
        self.started = False
        self.event = None

    def attach_cancel(self, event):
        self.event = event

    def request_sms_code(self, reason):
        self.started = True
        end = time.monotonic() + self.delay
        while time.monotonic() < end:
            if self.event is not None and self.event.is_set():
                self.cancelled = True
                return None
            time.sleep(0.01)
        if self.boom:
            raise RuntimeError("channel exploded")
        return self.code

    def send_qr_code(self, qrcode, reason):
        return False

    def notify_result(self, method, success, detail=""):
        return False


class RacingLoginInteractionTestCase(unittest.TestCase):
    def test_first_code_wins_and_others_are_cancelled(self):
        fast = FakeChannel("238975", delay=0.05)
        slow = FakeChannel("674151", delay=5.0)
        race = RacingLoginInteraction([fast, slow])

        started = time.monotonic()
        code = race.request_sms_code("登录态失效")
        elapsed = time.monotonic() - started

        self.assertEqual(code, "238975")
        self.assertLess(elapsed, 1.5, "不该等慢通道的超时")
        time.sleep(0.3)  # 给慢通道一点时间响应取消
        self.assertTrue(slow.cancelled, "慢通道应收到取消信号并退出")

    def test_waits_when_slow_channel_is_the_only_winner(self):
        empty = FakeChannel(None, delay=0.05)
        late = FakeChannel("998877", delay=0.4)
        code = RacingLoginInteraction([empty, late]).request_sms_code("登录态失效")

        self.assertEqual(code, "998877")

    def test_returns_none_when_every_channel_fails(self):
        code = RacingLoginInteraction([
            FakeChannel(None, delay=0.05),
            FakeChannel(None, delay=0.1),
        ]).request_sms_code("登录态失效")

        self.assertIsNone(code)

    def test_exception_in_one_channel_does_not_kill_the_race(self):
        broken = FakeChannel(delay=0.02, boom=True)
        good = FakeChannel("445566", delay=0.4)
        code = RacingLoginInteraction([broken, good]).request_sms_code("登录态失效")

        self.assertEqual(code, "445566")

    def test_single_racer_calls_directly(self):
        only = FakeChannel("111222", delay=0.0)
        code = RacingLoginInteraction([only]).request_sms_code("登录态失效")

        self.assertEqual(code, "111222")
        self.assertIsNone(only.event, "单通道不需要取消机制")

    def test_no_racers_returns_none(self):
        self.assertIsNone(RacingLoginInteraction([]).request_sms_code("登录态失效"))

    def test_extras_only_handle_qrcode_and_notify(self):
        class QrOnly(FakeChannel):
            def send_qr_code(self, qrcode, reason):
                self.sent = True
                return True

        extra = QrOnly()
        race = RacingLoginInteraction([], [extra])

        self.assertTrue(race.send_qr_code(b"png", "登录态失效"))
        self.assertTrue(extra.sent)
        self.assertIsNone(race.request_sms_code("登录态失效"))


class SmsRelayRequestCodeTestCase(unittest.TestCase):
    def relay(self, **kwargs):
        params = {"base_url": "http://relay:8088", "token": "t", "wait_seconds": 1}
        params.update(kwargs)
        return SmsRelayLoginInteraction(**params)

    @patch("sgcc_ha_bridge.login_interaction.requests.get")
    def test_returns_code_already_captured(self, get):
        get.return_value = response(code_payload("238975"))
        code = self.relay().request_sms_code("登录态失效")

        self.assertEqual(code, "238975")
        # 已经拿到码就不该再打长轮询
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.kwargs["params"], {"max_age": 90})

    @patch("sgcc_ha_bridge.login_interaction.requests.get")
    def test_sends_token_in_header_not_query(self, get):
        get.return_value = response(code_payload("238975"))
        self.relay().request_sms_code("登录态失效")

        headers = get.call_args.kwargs["headers"]
        self.assertEqual(headers.get("X-Sms-Token"), "t")
        self.assertNotIn("token", get.call_args.kwargs["params"])

    @patch("sgcc_ha_bridge.login_interaction.time.sleep")
    @patch("sgcc_ha_bridge.login_interaction.requests.get")
    def test_waits_for_new_code_via_long_poll(self, get, _sleep):
        get.side_effect = [
            response(code_payload(None, now=1000)),
            response(code_payload(None, now=1100, timeout=True)),
            response(code_payload("674151", now=1200, seq=9)),
        ]
        code = self.relay(wait_seconds=30).request_sms_code("登录态失效")

        self.assertEqual(code, "674151")
        calls = [c.kwargs["params"] for c in get.call_args_list]
        self.assertEqual(calls[0], {"max_age": 90})
        self.assertEqual(calls[1]["since"], 1000)
        self.assertEqual(calls[2]["since"], 1100)

    @patch("sgcc_ha_bridge.login_interaction.requests.get")
    def test_returns_none_on_auth_failure(self, get):
        get.return_value = response({"ok": False, "error": "unauthorized"}, status_code=401)
        self.assertIsNone(self.relay().request_sms_code("登录态失效"))
        # 鉴权失败是确定性错误，不应重试
        self.assertEqual(get.call_count, 1)

    @patch("sgcc_ha_bridge.login_interaction.requests.get")
    def test_returns_immediately_when_relay_unreachable(self, get):
        """中转根本连不上时要立刻返回，好让 Composite 去试 Telegram，不能把超时耗死。"""
        get.side_effect = ConnectionError("boom")

        self.assertIsNone(self.relay(wait_seconds=180).request_sms_code("登录态失效"))
        self.assertEqual(get.call_count, 1)

    @patch("sgcc_ha_bridge.login_interaction.time.sleep")
    @patch("sgcc_ha_bridge.login_interaction.requests.get")
    def test_gives_up_after_three_poll_failures(self, get, _sleep):
        get.side_effect = [
            response(code_payload(None, now=1000)),
            ConnectionError("boom"),
            ConnectionError("boom"),
            ConnectionError("boom"),
        ]
        code = self.relay(wait_seconds=60).request_sms_code("登录态失效")

        self.assertIsNone(code)
        self.assertEqual(get.call_count, 4)

    @patch("sgcc_ha_bridge.login_interaction.time.sleep")
    @patch("sgcc_ha_bridge.login_interaction.requests.get")
    def test_times_out_when_no_code_arrives(self, get, _sleep):
        get.side_effect = [
            response(code_payload(None, now=1000)),
            response(code_payload(None, now=1100, timeout=True)),
        ]
        code = self.relay(wait_seconds=1).request_sms_code("登录态失效")

        self.assertIsNone(code)

    @patch("sgcc_ha_bridge.login_interaction.requests.get")
    def test_rejects_malformed_code_from_relay(self, get):
        get.return_value = response(code_payload("abc123"))
        code = self.relay(wait_seconds=1).request_sms_code("登录态失效")

        self.assertIsNone(code)


class NotifyBroadcastTestCase(unittest.TestCase):
    """不论验证码从哪条通道来，登录结果都要发给所有通道（尤其是 Telegram）。"""

    class Recorder(FakeChannel):
        def __init__(self, accepts=False):
            super().__init__()
            self.accepts = accepts
            self.results = []
            self.qrs = []

        def send_qr_code(self, qrcode, reason):
            self.qrs.append(reason)
            return False

        def notify_result(self, method, success, detail=""):
            self.results.append((method, success, detail))
            return self.accepts

    def test_notify_is_not_short_circuited_by_first_success(self):
        first = self.Recorder(accepts=True)
        second = self.Recorder(accepts=False)

        self.assertTrue(RacingLoginInteraction([], [first, second]).notify_result("phone-code", True, ""))

        self.assertEqual(len(first.results), 1)
        self.assertEqual(len(second.results), 1, "先返回 True 的通道不该吞掉后面的通知")

    def test_telegram_still_gets_result_when_relay_wins_the_race(self):
        relay = self.Recorder(accepts=False)
        relay.code, relay.delay = "238975", 0.05
        telegram = self.Recorder(accepts=False)
        telegram.delay = 5.0

        race = RacingLoginInteraction([relay, telegram])
        self.assertEqual(race.request_sms_code("登录态失效"), "238975")

        race.notify_result("phone-code", True, "登录态已刷新")

        self.assertEqual(
            telegram.results,
            [("phone-code", True, "登录态已刷新")],
            "码是从中转拿的，TG 也必须收到登录结果",
        )

    def test_qrcode_is_sent_to_every_channel(self):
        a, b = self.Recorder(), self.Recorder()
        RacingLoginInteraction([], [a, b]).send_qr_code(b"png", "需要扫码")

        self.assertEqual(a.qrs, ["需要扫码"])
        self.assertEqual(b.qrs, ["需要扫码"])


if __name__ == "__main__":
    unittest.main()
