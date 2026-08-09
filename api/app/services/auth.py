import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuthSession, User, utcnow
from app.security.passwords import burn_verification_time, verify_password
from app.security.tokens import digest_token, generate_token


def normalize_email(email: str) -> str:
    """Consistent normalization applied before every storage and lookup, so
    email comparison is case-insensitive."""
    return email.strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def authenticate(db: Session, email: str, password: str) -> User | None:
    """Return the user only for a correct, active credential pair.

    Unknown email, wrong password, and inactive account are indistinguishable
    to the caller; unknown emails still burn a hash verification so timing is
    comparable.
    """
    user = get_user_by_email(db, email)
    if user is None:
        burn_verification_time(password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if not user.is_active:
        return None
    return user


def create_session(db: Session, user: User, settings: Settings) -> tuple[AuthSession, str]:
    """Create a server-side session; returns it with the raw opaque token,
    which is stored only as a keyed digest."""
    raw_token = generate_token()
    now = utcnow()
    session = AuthSession(
        user_id=user.id,
        token_digest=digest_token(raw_token, settings.session_token_pepper),
        csrf_token=generate_token(),
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(days=settings.session_absolute_days),
    )
    db.add(session)
    db.flush()
    return session, raw_token


def find_active_session(db: Session, raw_token: str, settings: Settings) -> AuthSession | None:
    """Look up a session by token and enforce revocation, absolute expiry and
    the inactivity timeout. Touches last_activity_at on success."""
    digest = digest_token(raw_token, settings.session_token_pepper)
    session = db.scalar(select(AuthSession).where(AuthSession.token_digest == digest))
    if session is None or session.revoked_at is not None:
        return None
    now = utcnow()
    if now >= session.expires_at:
        return None
    if now - session.last_activity_at > timedelta(minutes=settings.session_inactivity_minutes):
        # Terminal state: commit immediately, since the request will abort with 401.
        session.revoked_at = now
        db.commit()
        return None
    session.last_activity_at = now
    db.flush()
    return session


def revoke_session(db: Session, session: AuthSession) -> None:
    if session.revoked_at is None:
        session.revoked_at = utcnow()
        db.flush()


def revoke_all_sessions(
    db: Session, user_id: uuid.UUID, except_id: uuid.UUID | None = None
) -> None:
    sessions = db.scalars(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    )
    now = utcnow()
    for session in sessions:
        if except_id is not None and session.id == except_id:
            continue
        session.revoked_at = now
    db.flush()


def rotate_session(
    db: Session, session: AuthSession, settings: Settings
) -> tuple[AuthSession, str]:
    """Revoke the given session and issue a fresh one for the same user."""
    user = db.get(User, session.user_id)
    assert user is not None
    revoke_session(db, session)
    return create_session(db, user, settings)
