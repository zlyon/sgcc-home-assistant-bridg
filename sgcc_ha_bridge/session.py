from .model import SessionCheck
from .redact import now_iso, redact_url


def check_session(driver, check_method: str) -> SessionCheck:
    current_url = ""
    try:
        current_url = driver.current_url or ""
    except Exception:
        current_url = ""
    redirected_to_login = "/osgweb/login" in current_url
    auth_by = "none"
    try:
        from .login import SgccLogin
        authenticated, auth_by = SgccLogin.auth_evidence(driver)
    except Exception:
        authenticated = False
        auth_by = "error"
    if authenticated:
        status = "authenticated"
    elif redirected_to_login:
        status = "expired"
    else:
        status = "unknown"
    safe_url = redact_url(current_url)
    return SessionCheck(
        checked_at=now_iso(),
        status=status,
        current_url=safe_url,
        check_method=check_method,
        redirected_to_login=redirected_to_login,
        evidence_redacted=f"url={safe_url};auth_by={auth_by}",
    )
