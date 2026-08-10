import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

# Only safe fields appear here; password hashes, token digests and other
# internal security fields are never exposed through API schemas.


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    display_name: str = ""
    notification_phone: str | None = None
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
    # Owner-managed presentation fields; empty string clears them.
    display_name: str | None = Field(default=None, max_length=100)
    notification_phone: str | None = Field(default=None, max_length=50)


class UserListOut(BaseModel):
    items: list["UserOut"]
    total: int
    page: int
    page_size: int


class AdminResetPasswordRequest(BaseModel):
    temporary_password: str


class MessageResponse(BaseModel):
    detail: str


class AssignableUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    display_name: str = ""


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
    voice_calls: list["AttentionVoiceCallOut"] = []


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


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("must include a UTC offset, for example 2026-08-20T14:00:00Z")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_require_aware)]


class AppointmentOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    lead_name: str | None = None
    assigned_to: uuid.UUID | None
    assignee_email: str | None
    # Presentation name for calendars and public surfaces; the email stays
    # out of narrow UI blocks.
    assignee_name: str | None = None
    subject: str
    notes: str
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str
    origin: str
    revision: int
    booking_reference: str | None
    cancellation_reason: str | None
    created_at: datetime
    updated_at: datetime


class CreateAppointmentRequest(BaseModel):
    start_at: AwareDatetime
    duration_minutes: int | None = Field(default=None, ge=1, le=720)
    subject: str = Field(default="Appointment", max_length=200)
    notes: str = Field(default="", max_length=2000)
    assigned_to: uuid.UUID | None = None


class RescheduleAppointmentRequest(BaseModel):
    start_at: AwareDatetime
    duration_minutes: int | None = Field(default=None, ge=1, le=720)
    # The revision the caller saw; a stale value gets 409, never a lost update.
    expected_revision: int = Field(ge=1)


class AppointmentDispositionRequest(BaseModel):
    status: str
    reason: str | None = Field(default=None, max_length=500)
    expected_revision: int = Field(ge=1)


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


class InternalBookingInfoRequest(BaseModel):
    """Body of the fixed-path internal lookup: the capability travels in the
    body so URLs and access logs never carry it."""

    token: str = Field(min_length=16, max_length=200)
    # Bounded paging over the configured booking window.
    start_day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    days: int = Field(default=14, ge=1, le=31)


class PublicBookingInfoOut(BaseModel):
    """Everything the public booking page may know. No lead identifiers, no
    contact details, no CRM data."""

    business_name: str
    intro: str
    staff_display_name: str | None
    duration_minutes: int
    timezone: str
    days: list[AvailabilityOut]
    # The configured window in days, and where the next availability page
    # starts (None when the window is exhausted).
    window_days: int = 0
    next_start_day: str | None = None


class InternalBookingConfirmRequest(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    start_at: AwareDatetime
    booking_key: str = Field(min_length=8, max_length=200)
    website: str | None = Field(default=None, max_length=100)  # honeypot


class PublicBookingResultOut(BaseModel):
    booking_reference: str
    start_at: datetime
    end_at: datetime
    timezone: str
    manage_token: str | None = None
    duplicate: bool = False


class InternalManageRequest(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    start_day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    days: int = Field(default=14, ge=1, le=31)


class InternalManageRescheduleRequest(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    start_at: AwareDatetime
    expected_revision: int = Field(ge=1)
    website: str | None = Field(default=None, max_length=100)  # honeypot


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
    revision: int = 1
    days: list[AvailabilityOut] = []
    next_start_day: str | None = None


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


class VoiceSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    voice_ack_enabled: bool
    voice_ack_template: str
    voice_alert_enabled: bool
    voice_alert_template: str
    voice_alert_recipients: str
    voice_default_staff_id: uuid.UUID | None
    voice_transcript_retention_enabled: bool
    voice_transcript_retention_days: int


class UpdateVoiceSettingsRequest(BaseModel):
    voice_ack_enabled: bool | None = None
    voice_ack_template: str | None = Field(default=None, max_length=1600)
    voice_alert_enabled: bool | None = None
    voice_alert_template: str | None = Field(default=None, max_length=1600)
    voice_alert_recipients: str | None = None
    voice_default_staff_id: uuid.UUID | None = None
    # Explicit sentinel-free clearing: send clear_default_staff=true.
    clear_default_staff: bool = False
    voice_transcript_retention_enabled: bool | None = None
    voice_transcript_retention_days: int | None = Field(default=None, ge=1, le=365)


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


# --- Voice calls ---------------------------------------------------------


class VoiceCallCompletedRequest(BaseModel):
    """Structured completion from the AI voice workflow.

    Strict on purpose: unknown fields are rejected, every value is bounded
    and enum-checked, and nothing here is ever treated as authority — the
    CallSid identifies the call, it does not authorize anything.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="twilio", max_length=32)
    call_sid: str = Field(min_length=8, max_length=64)
    caller_phone: str | None = Field(default=None, max_length=32)
    business_phone: str | None = Field(default=None, max_length=32)
    started_at: AwareDatetime | None = None
    answered_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=6 * 60 * 60)
    call_status: str = Field(default="completed")
    disposition: str = Field(default="", max_length=64)
    caller_name: str = Field(default="", max_length=200)
    caller_email: str | None = Field(default=None, max_length=320)
    service_requested: str = Field(default="", max_length=300)
    service_address: str = Field(default="", max_length=300)
    preferred_callback_window: str = Field(default="", max_length=200)
    appointment_preference: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=2000)
    urgency: str = Field(default="normal")
    requires_human_follow_up: bool = False
    transfer_outcome: str = Field(default="none")
    disclosure_version: str = Field(default="", max_length=64)
    consent_result: str = Field(default="not_asked")
    recording_sid: str | None = Field(default=None, max_length=64)
    transcript_text: str | None = Field(default=None, max_length=20_000)
    metadata: dict[str, str] | None = None


class VoiceCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    call_sid: str
    lead_id: uuid.UUID
    appointment_id: uuid.UUID | None
    caller_phone: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    call_status: str
    disposition: str
    caller_name: str
    service_requested: str
    service_address: str
    preferred_callback_window: str
    appointment_preference: str
    summary: str
    urgency: str
    requires_human_follow_up: bool
    transfer_outcome: str
    disclosure_version: str
    consent_result: str
    ack_state: str
    alert_state: str
    recording_sid: str | None
    purged_at: datetime | None
    created_at: datetime


class VoiceCallCompletedOut(BaseModel):
    call_id: uuid.UUID
    lead_id: uuid.UUID
    lead_created: bool
    needs_review: bool
    replayed: bool
    ack_state: str
    alert_state: str


class VoiceAvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_sid: str = Field(min_length=8, max_length=64)
    start_day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    days: int = Field(default=7, ge=1, le=31)


class VoiceAvailabilityOut(BaseModel):
    result: str  # "ok" | "requires_human_follow_up"
    reason: str | None = None
    timezone: str | None = None
    duration_minutes: int | None = None
    staff_display_name: str | None = None
    days: list[AvailabilityOut] = []
    next_start_day: str | None = None


class VoiceBookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_sid: str = Field(min_length=8, max_length=64)
    start_at: AwareDatetime


class VoiceBookOut(BaseModel):
    result: str  # "booked" | "slot_unavailable" | "requires_human_follow_up"
    reason: str | None = None
    booking_reference: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = None
    replayed: bool = False


class VoiceCleanupOut(BaseModel):
    purged_transcripts: int
    purged_recordings: int


class AttentionVoiceCallOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    lead_name: str | None
    reason: str
    summary: str
    occurred_at: datetime | None


class UpdateUserAdminFieldsRequest(BaseModel):
    """Owner-managed presentation fields, separate from login identity."""

    display_name: str | None = Field(default=None, max_length=100)
    notification_phone: str | None = Field(default=None, max_length=50)


# --- Jobs ----------------------------------------------------------------


class JobOut(BaseModel):
    id: uuid.UUID
    job_number: str
    lead_id: uuid.UUID
    lead_name: str | None = None
    title: str
    service_type: str
    service_address: str
    status: str
    assigned_to: uuid.UUID | None
    assignee_name: str | None = None
    scheduled_for: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    internal_notes: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int
    page: int
    page_size: int


class CreateJobRequest(BaseModel):
    lead_id: uuid.UUID
    title: str = Field(default="", max_length=200)
    service_type: str = Field(default="", max_length=200)
    service_address: str = Field(default="", max_length=300)
    assigned_to: uuid.UUID | None = None
    scheduled_for: datetime | None = None
    internal_notes: str = Field(default="", max_length=5000)


class UpdateJobRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    service_type: str | None = Field(default=None, max_length=200)
    service_address: str | None = Field(default=None, max_length=300)
    assigned_to: uuid.UUID | None = None
    clear_assignee: bool = False
    scheduled_for: datetime | None = None
    clear_scheduled_for: bool = False
    internal_notes: str | None = Field(default=None, max_length=5000)


class JobStatusRequest(BaseModel):
    status: str
    note: str = Field(default="", max_length=300)


class LinkAppointmentRequest(BaseModel):
    appointment_id: uuid.UUID


# --- Job documents -------------------------------------------------------


class JobDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    title: str
    category: str
    description: str
    original_filename: str
    content_type: str
    byte_size: int
    sha256: str
    scan_state: str
    scan_detail: str | None
    has_preview: bool = False
    archived_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime


class UpdateJobDocumentRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=16)
    description: str | None = Field(default=None, max_length=1000)


class MoveJobDocumentRequest(BaseModel):
    target_job_id: uuid.UUID


class DeleteJobDocumentRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


# --- Commercial documents ------------------------------------------------


class LineItemIn(BaseModel):
    description: str = Field(max_length=500)
    quantity_milli: int = Field(gt=0)
    unit: str = Field(default="", max_length=20)
    unit_price_minor: int = Field(ge=0)
    discount_bp: int = Field(default=0, ge=0, le=10000)
    tax_rate_bp: int = Field(default=0, ge=0, le=5000)


class LineItemOut(LineItemIn):
    position: int
    line_total_minor: int


class CommercialDocumentOut(BaseModel):
    id: uuid.UUID
    kind: str
    job_id: uuid.UUID
    status: str
    number: str | None
    currency: str
    discount_bp: int
    subtotal_minor: int
    discount_total_minor: int
    tax_total_minor: int
    total_minor: int
    amount_paid_minor: int
    customer_notes: str
    terms: str
    valid_until: datetime | None
    issued_at: datetime | None
    due_at: datetime | None
    current_version: int
    responded_at: datetime | None
    response_name: str | None
    source_quote_id: uuid.UUID | None
    converted_invoice_id: uuid.UUID | None
    payment_id: uuid.UUID | None
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime
    lines: list[LineItemOut] = []


class DraftRequest(BaseModel):
    kind: Literal["quote", "invoice"]


class UpdateDraftRequest(BaseModel):
    lines: list[LineItemIn] = Field(max_length=100)
    discount_bp: int = Field(default=0, ge=0, le=10000)
    customer_notes: str = Field(default="", max_length=5000)
    terms: str = Field(default="", max_length=5000)
    valid_until: datetime | None = None
    due_at: datetime | None = None


class VoidRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


class RecordPaymentRequest(BaseModel):
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    method: Literal["cash", "check", "bank_transfer", "card_external", "other"]
    paid_on: datetime
    reference: str = Field(default="", max_length=100)
    internal_note: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=100)


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    amount_minor: int
    currency: str
    method: str
    paid_on: datetime
    reference: str
    internal_note: str
    receipt_document_id: uuid.UUID | None
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime


class ReversePaymentRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    version: int
    number: str
    pdf_sha256: str
    pdf_byte_size: int
    superseded_at: datetime | None
    created_at: datetime


# --- Email deliveries ----------------------------------------------------


class SendDocumentEmailRequest(BaseModel):
    # A plain bounded string: the service validates the shape, and reserved
    # test domains (.test) must remain usable in automated validation.
    recipient: str = Field(min_length=3, max_length=320)
    attach_pdf: bool | None = None
    send_key: str = Field(min_length=8, max_length=100)


class EmailDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    purpose: str
    version_id: uuid.UUID | None
    recipient: str
    from_name: str
    from_address: str
    reply_to: str
    subject: str
    attach_pdf: bool
    status: str
    attempts: int
    provider_message_id: str | None
    failure_class: str | None
    failure_message: str | None
    created_at: datetime
    submitted_at: datetime | None
    delivered_at: datetime | None


# --- Documents & email settings -----------------------------------------


class DocumentSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    default_currency: str
    quote_number_prefix: str
    invoice_number_prefix: str
    receipt_number_prefix: str
    default_quote_valid_days: int
    default_invoice_due_days: int
    default_tax_rate_bp: int
    business_email: str
    business_phone: str
    business_address: str
    business_registration_id: str
    email_from_display_name: str
    email_reply_to: str
    quote_email_subject: str
    quote_email_body: str
    invoice_email_subject: str
    invoice_email_body: str
    receipt_email_subject: str
    receipt_email_body: str
    secure_link_expiry_days: int
    email_attach_pdf_default: bool
    # Read-only deployment configuration; never accepted on update.
    effective_from_address: str = ""
    sender_configured: bool = False


class UpdateDocumentSettingsRequest(BaseModel):
    default_currency: str | None = Field(default=None, min_length=3, max_length=3)
    quote_number_prefix: str | None = Field(default=None, max_length=8)
    invoice_number_prefix: str | None = Field(default=None, max_length=8)
    receipt_number_prefix: str | None = Field(default=None, max_length=8)
    default_quote_valid_days: int | None = Field(default=None, ge=1, le=365)
    default_invoice_due_days: int | None = Field(default=None, ge=1, le=365)
    default_tax_rate_bp: int | None = Field(default=None, ge=0, le=5000)
    business_email: str | None = Field(default=None, max_length=320)
    business_phone: str | None = Field(default=None, max_length=32)
    business_address: str | None = Field(default=None, max_length=500)
    business_registration_id: str | None = Field(default=None, max_length=100)
    email_from_display_name: str | None = Field(default=None, max_length=200)
    email_reply_to: str | None = Field(default=None, max_length=320)
    quote_email_subject: str | None = Field(default=None, max_length=300)
    quote_email_body: str | None = Field(default=None, max_length=5000)
    invoice_email_subject: str | None = Field(default=None, max_length=300)
    invoice_email_body: str | None = Field(default=None, max_length=5000)
    receipt_email_subject: str | None = Field(default=None, max_length=300)
    receipt_email_body: str | None = Field(default=None, max_length=5000)
    secure_link_expiry_days: int | None = Field(default=None, ge=1, le=365)
    email_attach_pdf_default: bool | None = None


class StorageHealthOut(BaseModel):
    storage: dict[str, str]
    scanner: dict[str, str]
    sender_configured: bool


# --- Customer document access (internal BFF contract) -------------------


class DocumentAccessRequest(BaseModel):
    token: str = Field(min_length=16, max_length=200)


class QuoteResponseRequest(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    accept: bool
    typed_name: str = Field(min_length=1, max_length=200)
    website: str = Field(default="", max_length=100)  # honeypot


# --- n8n document-email contract ----------------------------------------


class ClaimEmailWorkRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


class ClaimedEmailOut(BaseModel):
    id: uuid.UUID
    recipient: str
    from_name: str
    from_address: str
    reply_to: str
    subject: str
    body_text: str
    body_html: str
    attach_pdf: bool
    purpose: str
    version_id: uuid.UUID | None
    pdf_filename: str | None = None


class ReportEmailResultRequest(BaseModel):
    delivery_id: uuid.UUID
    outcome: Literal["submitted", "delivered", "failed", "unknown"]
    provider_message_id: str | None = Field(default=None, max_length=200)
    failure_class: str | None = Field(default=None, max_length=32)
    failure_message: str | None = Field(default=None, max_length=500)
