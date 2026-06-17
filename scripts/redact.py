import logging
import re
from datetime import datetime

from model import mask_account_no


class AccountNoRedactionFilter(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
            record.msg = re.sub(
                r"(?<!\d)(\d{13})(?!\d)",
                lambda m: mask_account_no(m.group(1)),
                message,
            )
            record.args = ()
        except Exception:
            pass
        return True


_ACCOUNT_REDACTION_FILTER = AccountNoRedactionFilter()


def install_account_log_redaction() -> None:
    root = logging.getLogger()
    if _ACCOUNT_REDACTION_FILTER not in root.filters:
        root.addFilter(_ACCOUNT_REDACTION_FILTER)
    for handler in root.handlers:
        if _ACCOUNT_REDACTION_FILTER not in handler.filters:
            handler.addFilter(_ACCOUNT_REDACTION_FILTER)


def mask_secret(value: str, keep_last: int = 2) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= keep_last:
        return "*" * len(value)
    return "*" * (len(value) - keep_last) + value[-keep_last:]


def redact_text(value) -> str:
    text = str(value)
    return re.sub(r"(?<!\d)(\d{13})(?!\d)", lambda m: mask_account_no(m.group(1)), text)


def redact_url(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"(?<!\d)(\d{13})(?!\d)", lambda m: mask_account_no(m.group(1)), url.split("?", 1)[0])


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()
