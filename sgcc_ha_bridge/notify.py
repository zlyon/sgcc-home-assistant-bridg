import typing as typ
import os
import logging
import requests
import io

_DEFAULT_TIMEOUT = 10


def _timeout() -> float:
    try:
        return max(1.0, float(os.getenv("PUSH_TIMEOUT", str(_DEFAULT_TIMEOUT))))
    except (TypeError, ValueError):
        return float(_DEFAULT_TIMEOUT)


class PushplusNotify(typ.NamedTuple):

    def __call__(self, user_id, balance):
        BALANCE = float(os.getenv("BALANCE", 10.0))
        logging.info(f"检查电费余额。当余额低于 {BALANCE} 元时，将发送通知")
        if balance < BALANCE :
            token_value = os.getenv("PUSHPLUS_TOKEN", "").strip()
            if not token_value:
                logging.warning("PUSHPLUS_TOKEN 未配置，跳过余额通知。")
                return False
            for token in (item.strip() for item in token_value.split(",") if item.strip()):
                title = "电费余额不足提醒"
                content = (f"您用户号{user_id}的当前电费余额为：{balance}元，请及时充值。" )
                resp = requests.get(
                    "https://www.pushplus.plus/send",
                    params={"token": token, "title": title, "content": content},
                    timeout=_timeout(),
                )
                logging.info(
                    f"用户 {user_id} 当前余额 {balance} 元低于 {BALANCE} 元，已发送通知，请注意查收并及时充值。"
                )
                return resp.status_code == 200
        return False

class UrlPushNotify(typ.NamedTuple):

    def __call__(self, user_id, balance):
        BALANCE = float(os.getenv("BALANCE", 10.0))
        logging.info(f"检查电费余额。当余额低于 {BALANCE} 元时，将发送通知")
        if balance < BALANCE :
            url = os.getenv("PUSH_URL", "").strip()
            if not url:
                logging.warning("PUSH_URL 未配置，跳过余额通知。")
                return False
            resp = requests.post(
                url,
                json={"user_id": user_id, "balance": balance},
                timeout=_timeout(),
            )
            logging.info(
                f"用户 {user_id} 当前余额 {balance} 元低于 {BALANCE} 元，已发送通知，请注意查收并及时充值。"
            )
            return resp.status_code == 200
        return False

class UrlLoginQrCodeNotify(typ.NamedTuple):

    def __call__(self, qrcode, reason: str) -> bool:
        url = os.getenv("PUSH_QRCODE_URL")

        if url:
            files = {
                'file': ("qrcode.png", io.BytesIO(qrcode), 'image/png')
            }
            resp = requests.post(
                url,
                files=files,
                data={"reason": reason},
                timeout=_timeout(),
            )
            logging.info("推送二维码到URL")
            return resp.status_code == 200
        return False
