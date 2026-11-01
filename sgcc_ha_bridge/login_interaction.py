from __future__ import annotations

import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol
from urllib.parse import urlsplit

import requests

from .notify import UrlLoginQrCodeNotify
from .redact import redact_text


_SMS_CODE_RE = re.compile(r"^\d{4,8}$")
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_SMS_WAIT_SECONDS = 180
_DEFAULT_SEND_TIMEOUT = 30.0
_DEFAULT_PHOTO_TIMEOUT = 60.0
_DEFAULT_POLL_TIMEOUT = 75.0
_DEFAULT_SEND_RETRIES = 3
_POLL_PAYLOAD_TIMEOUT = 10
# 只重试「请求很可能根本没到达服务器」或「网关临时不可用」的情形；
# 4xx/409 这类确定性失败直接返回，重试只会白等。
_RETRYABLE_ERRORS = {"timeout", "connection", "http_502", "http_503", "http_504"}

# 自建反代挂在 Cloudflare 后面时，默认的 python-requests UA 会被 Bot Fight Mode
# 直接判成机器人，边缘返回 403 + "error code: 1010"，请求根本到不了 api.telegram.org。
# 容器里看到的就是一句「HTTP 403」，很容易误判成 Bot Token 失效。
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)


def _telegram_headers() -> dict:
    agent = os.getenv("SGCC_TELEGRAM_USER_AGENT", "").strip() or _DEFAULT_USER_AGENT
    return {
        "User-Agent": agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


class LoginInteraction(Protocol):
    def send_qr_code(self, qrcode: bytes, reason: str) -> bool:
        ...

    def request_sms_code(self, reason: str) -> Optional[str]:
        ...

    def notify_result(self, method: str, success: bool, detail: str = "") -> bool:
        ...


class NoopLoginInteraction:
    def send_qr_code(self, qrcode: bytes, reason: str) -> bool:
        return False

    def request_sms_code(self, reason: str) -> Optional[str]:
        return None

    def notify_result(self, method: str, success: bool, detail: str = "") -> bool:
        return False


class UrlLoginInteraction(NoopLoginInteraction):
    def send_qr_code(self, qrcode: bytes, reason: str) -> bool:
        return UrlLoginQrCodeNotify()(qrcode, _safe_reason(reason))


class CompositeLoginInteraction:
    def __init__(self, interactions: list[LoginInteraction]):
        self.interactions = interactions

    def send_qr_code(self, qrcode: bytes, reason: str) -> bool:
        results = [_safe_call(item.send_qr_code, qrcode, reason) for item in self.interactions]
        return any(results)

    def request_sms_code(self, reason: str) -> Optional[str]:
        for interaction in self.interactions:
            code = _safe_call(interaction.request_sms_code, reason)
            if code:
                return code
        return None

    def notify_result(self, method: str, success: bool, detail: str = "") -> bool:
        results = [_safe_call(item.notify_result, method, success, detail) for item in self.interactions]
        return any(results)


@dataclass
class TelegramLoginInteraction:
    token: str
    chat_id: str
    api_base_url: str = "https://api.telegram.org"
    request_timeout: float = _DEFAULT_TIMEOUT
    sms_wait_seconds: int = _DEFAULT_SMS_WAIT_SECONDS
    # 第三方 Telegram 中转（国内自建或公共反代）单次往返实测 4~20 秒，抖动极大。
    # 统一用 10 秒会把「其实能成功」的请求判成失败，所以按方法分开给超时。
    send_timeout: float = _DEFAULT_SEND_TIMEOUT
    photo_timeout: float = _DEFAULT_PHOTO_TIMEOUT
    poll_timeout: float = _DEFAULT_POLL_TIMEOUT
    send_retries: int = _DEFAULT_SEND_RETRIES
    _next_update_offset: Optional[int] = field(default=None, init=False, repr=False)

    @classmethod
    def from_env(cls) -> Optional["TelegramLoginInteraction"]:
        token = _first_env("SGCC_TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN")
        chat_id = _first_env("SGCC_TELEGRAM_CHAT_ID", "TG_CHAT_ID")
        if not token or not chat_id:
            return None
        api_base_url = _first_env(
            "SGCC_TELEGRAM_API_BASE_URL",
            "TG_API_BASE_URL",
        ) or "https://api.telegram.org"
        if "://" not in api_base_url:
            api_base_url = f"https://{api_base_url}"
        api_base_url = api_base_url.rstrip("/")
        parsed = urlsplit(api_base_url)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            logging.warning(
                "Telegram API Base URL 必须是无凭证、无查询参数的 HTTPS 地址，"
                "登录交互已禁用。"
            )
            return None
        return cls(
            token=token,
            chat_id=chat_id,
            api_base_url=api_base_url,
            request_timeout=_env_float("PUSH_TIMEOUT", _DEFAULT_TIMEOUT, minimum=1.0),
            sms_wait_seconds=_env_int(
                "SGCC_SMS_CODE_TIMEOUT_SECONDS",
                _DEFAULT_SMS_WAIT_SECONDS,
                minimum=30,
                maximum=600,
            ),
            send_timeout=_env_float(
                "SGCC_TELEGRAM_SEND_TIMEOUT", _DEFAULT_SEND_TIMEOUT, minimum=5.0
            ),
            photo_timeout=_env_float(
                "SGCC_TELEGRAM_PHOTO_TIMEOUT", _DEFAULT_PHOTO_TIMEOUT, minimum=5.0
            ),
            poll_timeout=_env_float(
                "SGCC_TELEGRAM_POLL_TIMEOUT", _DEFAULT_POLL_TIMEOUT, minimum=5.0
            ),
            send_retries=_env_int(
                "SGCC_TELEGRAM_SEND_RETRIES",
                _DEFAULT_SEND_RETRIES,
                minimum=1,
                maximum=5,
            ),
        )

    @property
    def bot_url(self) -> str:
        return f"{self.api_base_url}/bot{self.token}"

    def send_qr_code(self, qrcode: bytes, reason: str) -> bool:
        result = self._post(
            "sendPhoto",
            attempts=self.send_retries,
            timeout=self.photo_timeout,
            data={
                "chat_id": self.chat_id,
                "caption": "国家电网登录需要扫码。二维码仅用于本次登录，请尽快使用网上国网 App 扫描。"
                f"\n原因：{_safe_reason(reason)}",
            },
            files={"photo": ("qrcode.png", bytes(qrcode), "image/png")},
        )
        if not result:
            # 二维码是兜底链路的唯一入口，发不出去就等于这条链路直接死掉，
            # 必须留下能一眼看懂的日志，而不是只丢一句泛泛的请求失败。
            logging.error(
                "Telegram 发送登录二维码失败，本次扫码兜底无法进行（请检查 "
                "SGCC_TELEGRAM_API_BASE_URL 可达性与 SGCC_TELEGRAM_PHOTO_TIMEOUT）。"
            )
        return bool(result)

    def request_sms_code(self, reason: str) -> Optional[str]:
        offset_status, base_offset = self._probe_update_offset()
        if offset_status == "conflict":
            return None
        if offset_status != "ok":
            # 预检失败不该连累主流程：照样把「请回传验证码」的提示发出去，
            # 轮询时不带 offset，靠 message_id 递增把旧消息过滤掉。
            logging.warning("Telegram 预检 getUpdates 失败，仍继续发送验证码索取提示。")

        prompt = self._post(
            "sendMessage",
            attempts=self.send_retries,
            timeout=self.send_timeout,
            json={
                "chat_id": self.chat_id,
                "text": "国家电网登录需要短信验证码。请直接回复本消息，发送 4～8 位纯数字验证码。"
                "验证码只用于本次登录，不会写入日志。"
                f"\n原因：{_safe_reason(reason)}",
                "reply_markup": {"force_reply": True, "selective": True},
            },
        )
        if not prompt:
            logging.error(
                "Telegram 发送「请回传短信验证码」提示失败，本次无法等待人工回传"
                "（请检查 SGCC_TELEGRAM_API_BASE_URL 可达性与 SGCC_TELEGRAM_SEND_TIMEOUT）。"
            )
            return None
        prompt_message_id = prompt.get("message_id")
        if prompt_message_id is None:
            logging.error("Telegram 发送验证码索取提示后未返回 message_id。")
            return None
        logging.info("已向 Telegram 推送短信验证码索取提示，等待人工回传。")

        offset = base_offset
        deadline = time.monotonic() + self.sms_wait_seconds
        consecutive_failures = 0
        while time.monotonic() < deadline:
            remaining = max(1, int(deadline - time.monotonic()))
            poll_timeout = min(_POLL_PAYLOAD_TIMEOUT, remaining)
            payload = {"timeout": poll_timeout, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            result = self._post(
                "getUpdates",
                timeout=poll_timeout + self.poll_timeout,
                json=payload,
            )
            if result is None:
                # 慢代理抖一下就整轮作废太亏，连续失败才放弃。
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    logging.error(
                        "Telegram getUpdates 连续 3 次失败，已放弃等待人工回传验证码。"
                    )
                    self._notify("本次国家电网短信验证码未能接收：Telegram 轮询通道连续失败。")
                    return None
                time.sleep(1.5 * consecutive_failures)
                continue
            consecutive_failures = 0
            for update in result:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = max(offset or 0, update_id + 1)
                    self._next_update_offset = offset
                code = self._pick_code_from_update(update, prompt_message_id)
                if code:
                    logging.info("已从 Telegram 收到本次登录的短信验证码回复。")
                    return code

        self._notify("本次国家电网短信验证码等待已超时，登录已安全取消。")
        return None

    def _pick_code_from_update(self, update: dict, prompt_message_id) -> Optional[str]:
        """从一条 update 里取验证码，取不到返回 None。

        只认本 chat；回复优先，但也接受「提示消息之后直接键入的纯数字」——
        强制回复（force_reply）在部分客户端上不会自动展开，用户直接打字是常态。
        """
        message = update.get("message") or {}
        if str((message.get("chat") or {}).get("id")) != str(self.chat_id):
            return None
        reply_to = message.get("reply_to_message") or {}
        reply_id = reply_to.get("message_id")
        message_id = message.get("message_id")
        if reply_id is not None:
            if reply_id != prompt_message_id:
                # 回复的是别的消息，不是本次提示，忽略。
                return None
        elif isinstance(message_id, int) and isinstance(prompt_message_id, int):
            if message_id <= prompt_message_id:
                # 提示发出之前的历史消息（预检失败兜底场景），忽略。
                return None
        code = str(message.get("text") or "").strip()
        if _SMS_CODE_RE.fullmatch(code):
            return code
        self._notify("验证码格式无效，请继续回复原提示消息并发送 4～8 位纯数字。")
        return None

    def notify_result(self, method: str, success: bool, detail: str = "") -> bool:
        method_name = "短信验证码" if method == "phone-code" else "二维码" if method == "qrcode" else method
        status = "成功" if success else "未成功"
        text = f"国家电网{method_name}登录{status}。"
        if detail:
            text += f"\n{_safe_reason(detail)}"
        return self._notify(text)

    def _notify(self, text: str) -> bool:
        result = self._post(
            "sendMessage",
            attempts=self.send_retries,
            timeout=self.send_timeout,
            json={"chat_id": self.chat_id, "text": text},
        )
        return bool(result)

    def _probe_update_offset(self) -> tuple[str, Optional[int]]:
        """返回 (status, offset)。

        status 取值：ok 拿到起点；conflict 有别的消费者在抢 update（继续轮询无意义）；
        unavailable 拿不到起点，但可以退化成「不带 offset + message_id 过滤」继续。
        """
        if self._next_update_offset is not None:
            return "ok", self._next_update_offset
        result, error = self._post_once(
            "getUpdates",
            timeout=self.poll_timeout,
            json={"offset": -1, "timeout": 0, "allowed_updates": ["message"]},
        )
        if error == "conflict":
            logging.warning(
                "Telegram Bot getUpdates 冲突：请关闭该 Bot 的 webhook 或其他长轮询消费者，"
                "本次短信验证码交互已取消。"
            )
            return "conflict", None
        if result is None:
            return "unavailable", None
        latest = max(
            (item.get("update_id", -1) for item in result if isinstance(item.get("update_id"), int)),
            default=-1,
        )
        self._next_update_offset = latest + 1
        return "ok", self._next_update_offset

    def _post(self, method: str, *, timeout: Optional[float] = None, attempts: int = 1, **kwargs):
        for attempt in range(1, max(1, attempts) + 1):
            result, error = self._post_once(method, timeout=timeout, **kwargs)
            if result is not None:
                return result
            if error not in _RETRYABLE_ERRORS or attempt >= attempts:
                return None
            logging.warning(
                "Telegram Bot %s 第 %d 次请求失败（%s），准备重试。",
                method,
                attempt,
                error,
            )
            time.sleep(1.5 * attempt)
        return None

    def _post_once(self, method: str, *, timeout: Optional[float] = None, **kwargs):
        """返回 (result, error)。result 为 None 时 error 说明原因。"""
        headers = dict(_telegram_headers())
        headers.update(kwargs.pop("headers", None) or {})
        try:
            response = requests.post(
                f"{self.bot_url}/{method}",
                timeout=timeout or self.request_timeout,
                headers=headers,
                **kwargs,
            )
            if response.status_code == 409:
                return None, "conflict"
            if response.status_code != 200:
                # Cloudflare 拦下来时正文是纯文本 "error code: 1010"，
                # 只打状态码会把「被边缘拦截」误认成「Bot 凭证失效」。
                snippet = (response.text or "").strip().replace("\n", " ")[:200]
                logging.warning(
                    "Telegram Bot %s 请求失败，HTTP %s，响应=%s",
                    method,
                    response.status_code,
                    redact_text(snippet) or "-",
                )
                return None, f"http_{response.status_code}"
            payload = response.json()
            if not payload.get("ok"):
                logging.warning(f"Telegram Bot {method} 返回失败。")
                return None, "not_ok"
            return payload.get("result"), ""
        except Exception as error:
            safe_error = str(error).replace(self.token, "<redacted>")
            logging.warning(
                f"Telegram Bot {method} 请求异常[{type(error).__name__}]: {redact_text(safe_error)}"
            )
            if isinstance(error, requests.exceptions.Timeout):
                return None, "timeout"
            if isinstance(error, requests.exceptions.ConnectionError):
                return None, "connection"
            return None, "exception"


def build_login_interaction() -> LoginInteraction:
    provider = os.getenv("SGCC_LOGIN_INTERACTION_PROVIDER", "auto").strip().lower()
    if provider in {"", "none", "off", "disabled"}:
        return NoopLoginInteraction()

    interactions: list[LoginInteraction] = []
    telegram = TelegramLoginInteraction.from_env()
    if provider in {"auto", "telegram", "both"}:
        if telegram is not None:
            interactions.append(telegram)
        elif provider in {"telegram", "both"}:
            logging.warning("已启用 Telegram 登录交互，但 Bot Token 或 Chat ID 未完整配置。")
    if provider in {"auto", "url", "both"} and os.getenv("PUSH_QRCODE_URL", "").strip():
        interactions.append(UrlLoginInteraction())

    if not interactions:
        return NoopLoginInteraction()
    if len(interactions) == 1:
        return interactions[0]
    return CompositeLoginInteraction(interactions)


def read_sms_code(interaction: LoginInteraction, reason: str) -> Optional[str]:
    code = interaction.request_sms_code(reason)
    if code:
        return code
    if sys.stdin is not None and sys.stdin.isatty():
        code = input("请输入手机验证码: ").strip()
        if _SMS_CODE_RE.fullmatch(code):
            return code
        logging.warning("输入的手机验证码格式无效。")
    return None


def _safe_call(func, *args):
    try:
        return func(*args)
    except Exception as error:
        logging.warning(f"登录交互通知失败，已隔离: {redact_text(error)}")
        return None


def _safe_reason(reason: str) -> str:
    return redact_text(reason or "登录态失效")[:160]


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _env_float(name: str, default: float, *, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, value))
