import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

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


class AssignableUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str


class LeadOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None
    phone: str | None
    company: str
    status: str
    source: str
    assigned_to: uuid.UUID | None
    assignee_email: str | None
    next_follow_up_at: datetime | None
    last_contacted_at: datetime | None
    needs_review: bool
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    custom_values: dict[str, Any] = {}


class LeadListOut(BaseModel):
    items: list[LeadOut]
    total: int
    page: int
    page_size: int


class CreateLeadRequest(BaseModel):
    name: str = Field(default="", max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    company: str = Field(default="", max_length=200)
    status: str = "new"
    source: str = "manual"
    assigned_to: uuid.UUID | None = None
    next_follow_up_at: datetime | None = None
    custom_values: dict[str, Any] = {}


class UpdateLeadRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    company: str | None = Field(default=None, max_length=200)
    status: str | None = None
    source: str | None = None
    next_follow_up_at: datetime | None = None
    needs_review: bool | None = None
    custom_values: dict[str, Any] | None = None


class AssignLeadRequest(BaseModel):
    user_id: uuid.UUID | None


class NoteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)


class ActivityOut(BaseModel):
    id: uuid.UUID
    type: str
    channel: str | None
    direction: str | None
    content: str
    created_by_email: str | None
    provider: str | None
    external_event_id: str | None
    occurred_at: datetime | None
    meta: dict[str, Any] | None
    created_at: datetime


class AttentionQueueOut(BaseModel):
    overdue: list[LeadOut]
    due_today: list[LeadOut]
    unassigned: list[LeadOut]
    needs_review: list[LeadOut]


class CustomFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    label: str
    type: str
    options: list[str] | None
    required: bool
    is_active: bool
    display_order: int


class CreateCustomFieldRequest(BaseModel):
    key: str = Field(max_length=50)
    label: str = Field(max_length=100)
    type: str
    options: list[str] | None = None
    required: bool = False
    display_order: int = 0


class UpdateCustomFieldRequest(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    options: list[str] | None = None
    required: bool | None = None
    is_active: bool | None = None
    display_order: int | None = None


INBOUND_CHANNELS = ("web_form", "phone_call", "sms", "whatsapp", "facebook", "email", "other")


class InboundEventRequest(BaseModel):
    channel: Literal["web_form", "phone_call", "sms", "whatsapp", "facebook", "email", "other"]
    provider: str | None = Field(default=None, max_length=100)
    external_event_id: str | None = Field(default=None, max_length=255)
    event_type: str | None = Field(default=None, max_length=100)
    sender_name: str | None = Field(default=None, max_length=200)
    sender_email: str | None = Field(default=None, max_length=320)
    sender_phone: str | None = Field(default=None, max_length=50)
    external_sender_id: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=500)
    content: str | None = Field(default=None, max_length=20_000)
    received_at: datetime | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value, default=str)) > 8_192:
            raise ValueError("metadata is too large (8KB limit)")
        return value


class InboundEventResponse(BaseModel):
    lead_id: uuid.UUID
    activity_id: uuid.UUID
    lead_created: bool
    replayed: bool
