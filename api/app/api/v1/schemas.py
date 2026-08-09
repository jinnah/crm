import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

# Only safe fields appear here; password hashes, token digests and other
# internal security fields are never exposed through API schemas.


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime


class SessionOut(BaseModel):
    user: UserOut
    csrf_token: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class CreateUserRequest(BaseModel):
    email: EmailStr
    role: str
    temporary_password: str


class UpdateUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class AdminResetPasswordRequest(BaseModel):
    temporary_password: str


class MessageResponse(BaseModel):
    detail: str
