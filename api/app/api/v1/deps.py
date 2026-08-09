from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuthSession, User
from app.security.tokens import constant_time_equals
from app.services.auth import find_active_session, revoke_session
from app.services.rate_limit import RateLimiter

CSRF_HEADER = "X-CSRF-Token"

DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def check_origin(request: Request, settings: SettingsDep) -> None:
    """Reject browser requests whose Origin is not the configured frontend.

    Non-browser clients omit Origin; authenticated unsafe methods are still
    covered by the CSRF synchronizer token.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return
    allowed = {settings.frontend_url, *settings.cors_origins}
    if origin.rstrip("/") not in {a.rstrip("/") for a in allowed}:
        raise HTTPException(status_code=403, detail="Origin not allowed.")


def get_current_session(
    request: Request, db: DbDep, settings: SettingsDep
) -> tuple[AuthSession, User]:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    session = find_active_session(db, raw_token, settings)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        revoke_session(db, session)
        db.commit()
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return session, user


SessionDep = Annotated[tuple[AuthSession, User], Depends(get_current_session)]


def get_current_user(session_and_user: SessionDep) -> User:
    return session_and_user[1]


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def check_csrf(request: Request, session_and_user: SessionDep) -> None:
    """Synchronizer-token check for authenticated state-changing requests."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    session, _ = session_and_user
    header = request.headers.get(CSRF_HEADER)
    if not header or not constant_time_equals(header, session.csrf_token):
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")


def require_password_changed(user: CurrentUserDep) -> User:
    """Blocks every protected action except the password-change flow while a
    password change is required. Enforced here on the API, not just in the UI."""
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required.")
    return user


FullyAuthedUserDep = Annotated[User, Depends(require_password_changed)]


def get_login_limiter(request: Request) -> RateLimiter:
    return request.app.state.login_limiter


def get_recovery_limiter(request: Request) -> RateLimiter:
    return request.app.state.recovery_limiter


def get_mailer(request: Request):
    return request.app.state.mailer


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
