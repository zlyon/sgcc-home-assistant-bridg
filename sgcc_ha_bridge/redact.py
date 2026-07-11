import logging
import re
from datetime import datetime

from .model import mask_account_no


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|cookie|authorization|api[_-]?key)"
    r"(\s*[:=]\s*)([^,\r\n]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_ACCOUNT_RE = re.compile(r"(?<!\d)(\d{13})(?!\d)")
_LONG_NUMERIC_ID_RE = re.compile(r"\d{13,}")


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
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        text,
    )
    text = _ACCOUNT_RE.sub(lambda match: mask_account_no(match.group(1)), text)
    return _LONG_NUMERIC_ID_RE.sub("<redacted-numeric-id>", text)


def redact_url(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"(?<!\d)(\d{13})(?!\d)", lambda m: mask_account_no(m.group(1)), url.split("?", 1)[0])


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()
