import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.v1.deps import (
    DbDep,
    SessionDep,
    SettingsDep,
    check_csrf,
    check_origin,
    client_ip,
    get_login_limiter,
    get_mailer,
    get_recovery_limiter,
)
from app.api.v1.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SessionOut,
    UserOut,
)
from app.config import Settings
from app.models import utcnow
from app.security.passwords import hash_password, validate_password, verify_password
from app.services import password_reset
from app.services.auth import (
    authenticate,
    create_session,
    get_user_by_email,
    normalize_email,
    revoke_all_sessions,
    revoke_session,
)
from app.services.mailer import Mailer
from app.services.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(check_origin)])

GENERIC_LOGIN_ERROR = "Invalid email or password."
GENERIC_RECOVERY_MESSAGE = (
    "If an account exists for that email, a password reset link has been sent."
)
GENERIC_RESET_ERROR = "Invalid or expired reset link."
RATE_LIMIT_ERROR = "Too many attempts. Please try again later."


def _set_session_cookie(response: Response, raw_token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_absolute_days * 24 * 60 * 60,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


@router.post("/login", response_model=SessionOut)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    limiter: Annotated[RateLimiter, Depends(get_login_limiter)],
) -> SessionOut:
    ip_key = f"ip:{client_ip(request)}"
    account_key = f"acct:{normalize_email(body.email)}"
    if not limiter.allowed(ip_key) or not limiter.allowed(account_key):
        raise HTTPException(status_code=429, detail=RATE_LIMIT_ERROR)

    user = authenticate(db, body.email, body.password)
    if user is None:
        limiter.record(ip_key)
        limiter.record(account_key)
        db.commit()
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)

    limiter.reset(account_key)
    user.last_login_at = utcnow()
    session, raw_token = create_session(db, user, settings)
    db.commit()
    _set_session_cookie(response, raw_token, settings)
    return SessionOut(user=UserOut.model_validate(user), csrf_token=session.csrf_token)


@router.post("/logout", status_code=204, dependencies=[Depends(check_csrf)])
def logout(
    session_and_user: SessionDep, response: Response, db: DbDep, settings: SettingsDep
) -> None:
    session, _ = session_and_user
    revoke_session(db, session)
    db.commit()
    _clear_session_cookie(response, settings)


@router.get("/session", response_model=SessionOut)
def current_session(session_and_user: SessionDep, db: DbDep) -> SessionOut:
    session, user = session_and_user
    db.commit()  # persist the last-activity touch
    return SessionOut(user=UserOut.model_validate(user), csrf_token=session.csrf_token)


@router.post("/change-password", status_code=204, dependencies=[Depends(check_csrf)])
def change_password(
    body: ChangePasswordRequest,
    session_and_user: SessionDep,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> None:
    session, user = session_and_user
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    policy_error = validate_password(body.new_password)
    if policy_error is not None:
        raise HTTPException(status_code=400, detail=policy_error)
    if verify_password(user.password_hash, body.new_password):
        raise HTTPException(
            status_code=400, detail="New password must be different from the current password."
        )

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.password_changed_at = utcnow()
    revoke_all_sessions(db, user.id)
    _, raw_token = create_session(db, user, settings)
    db.commit()
    _set_session_cookie(response, raw_token, settings)


@router.post("/forgot-password", status_code=202, response_model=MessageResponse)
def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    limiter: Annotated[RateLimiter, Depends(get_recovery_limiter)],
    mailer: Annotated[Mailer, Depends(get_mailer)],
) -> MessageResponse:
    response.headers["Referrer-Policy"] = "no-referrer"
    ip_key = f"ip:{client_ip(request)}"
    account_key = f"acct:{normalize_email(body.email)}"
    if not limiter.allowed(ip_key) or not limiter.allowed(account_key):
        raise HTTPException(status_code=429, detail=RATE_LIMIT_ERROR)
    limiter.record(ip_key)
    limiter.record(account_key)

    user = get_user_by_email(db, body.email)
    if user is not None and user.is_active:
        raw_token = password_reset.issue_reset_token(db, user, settings)
        db.commit()
        reset_url = f"{settings.frontend_url.rstrip('/')}/reset-password?token={raw_token}"
        try:
            mailer.send_password_reset(user.email, reset_url)
        except Exception as exc:  # never leak tokens or credentials into logs
            logger.warning("Password reset email delivery failed: %s", type(exc).__name__)
    return MessageResponse(detail=GENERIC_RECOVERY_MESSAGE)


@router.post("/reset-password", status_code=204)
def reset_password(
    body: ResetPasswordRequest, response: Response, db: DbDep, settings: SettingsDep
) -> None:
    response.headers["Referrer-Policy"] = "no-referrer"
    policy_error = validate_password(body.new_password)
    if policy_error is not None:
        raise HTTPException(status_code=400, detail=policy_error)
    token = password_reset.find_valid_token(db, body.token, settings)
    if token is None:
        raise HTTPException(status_code=400, detail=GENERIC_RESET_ERROR)
    password_reset.complete_reset(db, token, body.new_password, settings)
    db.commit()
