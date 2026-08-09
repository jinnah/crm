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
    first_inbound_at: datetime | None = None
    response_due_at: datetime | None = None
    first_response_at: datetime | None = None
    first_response_seconds: int | None = None
    response_target_met: bool | None = None
    response_overdue: bool = False
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


class AttentionAppointmentOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    lead_name: str | None
    subject: str
    start_at: datetime
    timezone: str
    status: str
    detail: str | None = None


class AttentionQueueOut(BaseModel):
    overdue: list[LeadOut]
    due_today: list[LeadOut]
    unassigned: list[LeadOut]
    needs_review: list[LeadOut]
    unresponded: list[LeadOut] = []
    appointments_overdue: list[AttentionAppointmentOut] = []
    appointments_upcoming: list[AttentionAppointmentOut] = []
    appointment_messages_failed: list[AttentionAppointmentOut] = []
    appointment_messages_unknown: list[AttentionAppointmentOut] = []


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


class MessageStatusRequest(BaseModel):
    provider_sid: str = Field(min_length=4, max_length=64)
    status: str = Field(max_length=32)
    error_code: str | None = Field(default=None, max_length=32)
    error_message: str | None = Field(default=None, max_length=500)


class MessageStatusResponse(BaseModel):
    matched: bool
    status: str | None


class OutboundMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    purpose: str
    to_phone: str
    body: str
    status: str
    provider_sid: str | None
    error_message: str | None
    created_by_email: str | None = None
    created_at: datetime
    submitted_at: datetime | None
    delivered_at: datetime | None
    failed_at: datetime | None


class SendMessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=1600)


class CommunicationSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_name: str
    form_title: str
    form_intro: str
    acknowledgment_enabled: bool
    acknowledgment_template: str
    alert_enabled: bool
    alert_template: str
    alert_destination_phone: str | None
    response_target_minutes: int


class UpdateCommunicationSettingsRequest(BaseModel):
    business_name: str | None = Field(default=None, max_length=200)
    form_title: str | None = Field(default=None, max_length=200)
    form_intro: str | None = Field(default=None, max_length=2000)
    acknowledgment_enabled: bool | None = None
    acknowledgment_template: str | None = Field(default=None, max_length=1600)
    alert_enabled: bool | None = None
    alert_template: str | None = Field(default=None, max_length=1600)
    alert_destination_phone: str | None = Field(default=None, max_length=50)
    response_target_minutes: int | None = Field(default=None, ge=1, le=10_080)


class BrandingOut(BaseModel):
    """Logo metadata only. The bytes are served from their own cacheable
    route, never embedded in a settings response."""

    business_name: str
    has_logo: bool
    width: int | None = None
    height: int | None = None
    updated_at: datetime | None = None
    initials: str


class PublicFormInfoOut(BaseModel):
    """Safe, public-facing subset of the settings for the request form."""

    form_title: str
    form_intro: str
    business_name: str


# --- Scheduling ----------------------------------------------------------


class AppointmentOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    lead_name: str | None = None
    assigned_to: uuid.UUID | None
    assignee_email: str | None
    subject: str
    notes: str
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str
    origin: str
    booking_reference: str | None
    cancellation_reason: str | None
    created_at: datetime
    updated_at: datetime


class CreateAppointmentRequest(BaseModel):
    start_at: datetime
    duration_minutes: int | None = Field(default=None, ge=1, le=720)
    subject: str = Field(default="Appointment", max_length=200)
    notes: str = Field(default="", max_length=2000)
    assigned_to: uuid.UUID | None = None


class RescheduleAppointmentRequest(BaseModel):
    start_at: datetime
    duration_minutes: int | None = Field(default=None, ge=1, le=720)


class AppointmentDispositionRequest(BaseModel):
    status: str
    reason: str | None = Field(default=None, max_length=500)


class AvailabilityOut(BaseModel):
    date: str
    timezone: str
    duration_minutes: int
    slots: list[datetime]


class BookingLinkOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    assigned_to: uuid.UUID | None
    expires_at: datetime
    revoked_at: datetime | None
    duration_minutes: int | None
    created_at: datetime
    last_used_at: datetime | None
    # Present only in the response that creates or regenerates the link.
    url: str | None = None


class CreateBookingLinkRequest(BaseModel):
    assigned_to: uuid.UUID | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=720)
    ttl_days: int = Field(default=14, ge=1, le=90)


class PublicBookingInfoOut(BaseModel):
    """Everything the public booking page may know. No lead identifiers, no
    contact details, no CRM data."""

    business_name: str
    intro: str
    staff_display_name: str | None
    duration_minutes: int
    timezone: str
    days: list[AvailabilityOut]


class PublicBookingRequest(BaseModel):
    start_at: datetime
    booking_key: str = Field(min_length=8, max_length=200)
    website: str | None = Field(default=None, max_length=100)  # honeypot


class PublicBookingResultOut(BaseModel):
    booking_reference: str
    start_at: datetime
    end_at: datetime
    timezone: str
    manage_token: str | None = None
    duplicate: bool = False


class PublicAppointmentOut(BaseModel):
    """What the holder of an appointment's manage capability may see.

    Deliberately excludes the internal subject, notes, lead identifiers and
    every contact detail — knowing the capability is not the same as being
    given access to the CRM record.
    """

    business_name: str
    staff_display_name: str | None
    booking_reference: str
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str
    can_change: bool
    days: list[AvailabilityOut] = []


class PublicRescheduleRequest(BaseModel):
    start_at: datetime
    website: str | None = Field(default=None, max_length=100)  # honeypot


class AppointmentNotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    appointment_id: uuid.UUID
    type: str
    occurrence: str
    scheduled_at: datetime
    state: str
    failure_message: str | None


class DispatchResultOut(BaseModel):
    claimed: int
    sent: int
    failed: int
    unknown: int
    suppressed: int
    recovered: int


class SchedulingSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_timezone: str
    appointment_duration_minutes: int
    min_booking_notice_minutes: int
    max_booking_days_ahead: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    self_booking_enabled: bool
    appointment_confirmation_enabled: bool
    appointment_reminder_enabled: bool
    reminder_offset_minutes: int
    second_reminder_offset_minutes: int | None
    upcoming_window_hours: int
    confirmation_template: str
    reminder_template: str
    appointment_canceled_template: str
    appointment_rescheduled_template: str
    business_hours: dict[str, Any] | None


class SchedulingBasicsOut(BaseModel):
    """What any staff member needs in order to schedule.

    Deliberately excludes message templates and the notification configuration,
    which stay owner-only.
    """

    model_config = ConfigDict(from_attributes=True)

    business_timezone: str
    appointment_duration_minutes: int
    min_booking_notice_minutes: int
    max_booking_days_ahead: int
    self_booking_enabled: bool
    business_hours: dict[str, Any] | None


class UpdateSchedulingSettingsRequest(BaseModel):
    business_timezone: str | None = Field(default=None, max_length=64)
    appointment_duration_minutes: int | None = Field(default=None, ge=5, le=720)
    min_booking_notice_minutes: int | None = Field(default=None, ge=0, le=43_200)
    max_booking_days_ahead: int | None = Field(default=None, ge=1, le=365)
    buffer_before_minutes: int | None = Field(default=None, ge=0, le=240)
    buffer_after_minutes: int | None = Field(default=None, ge=0, le=240)
    self_booking_enabled: bool | None = None
    appointment_confirmation_enabled: bool | None = None
    appointment_reminder_enabled: bool | None = None
    reminder_offset_minutes: int | None = Field(default=None, ge=5, le=20_160)
    second_reminder_offset_minutes: int | None = Field(default=None, ge=5, le=20_160)
    upcoming_window_hours: int | None = Field(default=None, ge=1, le=336)
    confirmation_template: str | None = Field(default=None, max_length=1600)
    reminder_template: str | None = Field(default=None, max_length=1600)
    appointment_canceled_template: str | None = Field(default=None, max_length=1600)
    appointment_rescheduled_template: str | None = Field(default=None, max_length=1600)
    business_hours: dict[str, Any] | None = None
