import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PasswordResetToken, User, utcnow
from app.security.passwords import hash_password
from app.security.tokens import digest_token, generate_token
from app.services.auth import revoke_all_sessions

RESET_TOKEN_TTL = timedelta(minutes=30)


def _revoke_active_tokens(db: Session, user_id: uuid.UUID, now: datetime) -> None:
    tokens = db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.revoked_at.is_(None),
        )
    )
    for token in tokens:
        token.revoked_at = now


def issue_reset_token(db: Session, user: User, settings: Settings) -> str:
    """Create a single-use reset token, invalidating earlier active ones.
    Returns the raw token; only its keyed digest is stored."""
    now = utcnow()
    _revoke_active_tokens(db, user.id, now)
    raw_token = generate_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_digest=digest_token(raw_token, settings.session_token_pepper),
            created_at=now,
            expires_at=now + RESET_TOKEN_TTL,
        )
    )
    db.flush()
    return raw_token


def find_valid_token(db: Session, raw_token: str, settings: Settings) -> PasswordResetToken | None:
    digest = digest_token(raw_token, settings.session_token_pepper)
    token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_digest == digest))
    if token is None or token.used_at is not None or token.revoked_at is not None:
        return None
    if utcnow() >= token.expires_at:
        return None
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        return None
    return token


def complete_reset(
    db: Session, token: PasswordResetToken, new_password: str, settings: Settings
) -> None:
    """Apply the new password; invalidates the token, all other outstanding
    reset tokens, and every active session for the user."""
    user = db.get(User, token.user_id)
    assert user is not None
    now = utcnow()
    token.used_at = now
    _revoke_active_tokens(db, user.id, now)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = now
    revoke_all_sessions(db, user.id)
    db.flush()
