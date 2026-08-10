import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.deps import (
    DbDep,
    FullyAuthedUserDep,
    check_csrf,
    check_origin,
)
from app.api.v1.schemas import (
    AdminResetPasswordRequest,
    CreateUserRequest,
    UpdateUserRequest,
    UserListOut,
    UserOut,
)
from app.models import User
from app.services import messaging
from app.services import users as user_service
from app.services.users import UserManagementError

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(check_origin), Depends(check_csrf)],
)


def _get_target_user(db: DbDep, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


def _handle(error: UserManagementError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


@router.get("", response_model=UserListOut)
def list_users(
    acting_user: FullyAuthedUserDep,
    db: DbDep,
    query: Annotated[str, Query(max_length=200)] = "",
    role: Annotated[str, Query(max_length=20)] = "",
    status: Annotated[str, Query(max_length=10)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> UserListOut:
    """Bounded, filterable listing so administration stays usable with many
    accounts."""
    try:
        users = user_service.list_users(db, acting_user)
    except UserManagementError as error:
        raise _handle(error) from error

    needle = query.strip().lower()
    filtered = [
        user
        for user in users
        if (not needle or needle in user.email.lower() or needle in user.display_name.lower())
        and (not role or user.role == role)
        and (
            not status
            or (status == "active" and user.is_active)
            or (status == "inactive" and not user.is_active)
        )
    ]
    start = (page - 1) * page_size
    return UserListOut(
        items=[UserOut.model_validate(user) for user in filtered[start : start + page_size]],
        total=len(filtered),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: CreateUserRequest, acting_user: FullyAuthedUserDep, db: DbDep) -> UserOut:
    try:
        user = user_service.create_user(
            db, acting_user, body.email, body.role, body.temporary_password
        )
    except UserManagementError as error:
        db.rollback()
        raise _handle(error) from error
    db.commit()
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    acting_user: FullyAuthedUserDep,
    db: DbDep,
) -> UserOut:
    target = _get_target_user(db, user_id)
    try:
        user = user_service.update_user(
            db, acting_user, target, role=body.role, is_active=body.is_active
        )
        # Owner-managed presentation fields, validated separately from the
        # login identity. The notification phone is conservative E.164.
        if body.display_name is not None:
            user.display_name = body.display_name.strip()[:100]
        if body.notification_phone is not None:
            user.notification_phone = messaging.validate_phone(body.notification_phone)
    except (UserManagementError, messaging.MessagingError) as error:
        db.rollback()
        raise _handle(error) from error
    db.commit()
    return UserOut.model_validate(user)


@router.post("/{user_id}/reset-password", status_code=204)
def admin_reset_password(
    user_id: uuid.UUID,
    body: AdminResetPasswordRequest,
    acting_user: FullyAuthedUserDep,
    db: DbDep,
) -> None:
    target = _get_target_user(db, user_id)
    try:
        user_service.admin_reset_password(db, acting_user, target, body.temporary_password)
    except UserManagementError as error:
        db.rollback()
        raise _handle(error) from error
    db.commit()
