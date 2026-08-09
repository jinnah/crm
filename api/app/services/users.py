from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ROLES, User, utcnow
from app.security.passwords import hash_password, validate_password
from app.services.auth import get_user_by_email, normalize_email, revoke_all_sessions


class UserManagementError(Exception):
    """Rejected user-management action; message is safe to return to the client."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _require_owner(acting_user: User) -> None:
    # Authorization is asserted here in the service, not only in route dependencies.
    if acting_user.role != "owner" or not acting_user.is_active:
        raise UserManagementError("You are not allowed to manage users.", status_code=403)


def count_active_owners(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(User.role == "owner", User.is_active)
    )


def list_users(db: Session, acting_user: User) -> list[User]:
    _require_owner(acting_user)
    return list(db.scalars(select(User).order_by(User.created_at, User.email)))


def create_user(
    db: Session, acting_user: User, email: str, role: str, temporary_password: str
) -> User:
    _require_owner(acting_user)
    if role not in ROLES:
        raise UserManagementError("Invalid role.")
    policy_error = validate_password(temporary_password)
    if policy_error is not None:
        raise UserManagementError(policy_error)
    normalized = normalize_email(email)
    if get_user_by_email(db, normalized) is not None:
        raise UserManagementError("An account with this email already exists.", status_code=409)
    user = User(
        email=normalized,
        password_hash=hash_password(temporary_password),
        role=role,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    return user


def update_user(
    db: Session,
    acting_user: User,
    user: User,
    role: str | None = None,
    is_active: bool | None = None,
) -> User:
    _require_owner(acting_user)
    if role is not None and role not in ROLES:
        raise UserManagementError("Invalid role.")

    removes_owner = user.role == "owner" and user.is_active
    still_owner = (role or user.role) == "owner" and (
        is_active if is_active is not None else user.is_active
    )
    if removes_owner and not still_owner and count_active_owners(db) <= 1:
        raise UserManagementError(
            "Cannot demote or deactivate the last active owner.", status_code=409
        )

    if role is not None:
        user.role = role
    if is_active is not None:
        if user.is_active and not is_active:
            revoke_all_sessions(db, user.id)
        user.is_active = is_active
    db.flush()
    return user


def admin_reset_password(
    db: Session, acting_user: User, user: User, temporary_password: str
) -> None:
    _require_owner(acting_user)
    policy_error = validate_password(temporary_password)
    if policy_error is not None:
        raise UserManagementError(policy_error)
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.password_changed_at = utcnow()
    revoke_all_sessions(db, user.id)
    db.flush()
