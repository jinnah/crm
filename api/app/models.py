import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ROLES = ("owner", "manager", "team_member")

LEAD_STATUSES = ("new", "contacted", "qualified", "won", "lost")

# Normalized source/channel values; "manual" marks leads created in the UI.
CHANNELS = (
    "web_form",
    "phone_call",
    "voice_call",
    "sms",
    "whatsapp",
    "facebook",
    "email",
    "other",
    "manual",
)

CUSTOM_FIELD_TYPES = ("text", "number", "date", "boolean", "select")

ACTIVITY_TYPES = (
    "created",
    "inbound_request",
    "note",
    "status_change",
    "assignment_change",
    "follow_up_scheduled",
    "follow_up_changed",
    "follow_up_completed",
    "follow_up_cleared",
    "archived",
    "restored",
    "outbound_message",
    "message_status",
    "contacted_outside_crm",
    "appointment_scheduled",
    "appointment_rescheduled",
    "appointment_canceled",
    "appointment_completed",
    "appointment_no_show",
    "booking_link_created",
    "booking_link_revoked",
    "voice_call",
    "job_created",
    "job_status_change",
    "job_archived",
    "job_restored",
    "document_uploaded",
    "document_moved",
    "document_deleted",
    "commercial_issued",
    "commercial_voided",
    "quote_response",
    "payment_recorded",
    "payment_reversed",
    "document_email",
)

# Outbound message purposes and delivery states.
MESSAGE_PURPOSES = ("human_reply", "auto_acknowledgment", "staff_alert", "appointment")
MESSAGE_STATUSES = ("pending", "submitted", "delivered", "failed", "unknown")

# Appointment lifecycle. Appointments are never hard-deleted.
APPOINTMENT_STATUSES = ("scheduled", "completed", "canceled", "no_show")
APPOINTMENT_ORIGINS = ("staff", "customer", "voice")

# Voice-call enumerations: everything the AI agent reports is normalized to
# these bounded values before storage.
VOICE_CALL_STATUSES = ("completed", "no_answer", "abandoned", "transferred", "failed")
VOICE_URGENCIES = ("normal", "urgent")
VOICE_TRANSFER_OUTCOMES = ("none", "completed", "failed")
VOICE_CONSENT_RESULTS = ("granted", "declined", "not_asked")
VOICE_MESSAGE_STATES = ("skipped", "no_destination", "sent", "failed", "unknown")
VOICE_ALERT_RECIPIENTS = ("business", "assigned", "both")

# Appointment notification kinds and their durable delivery state.
NOTIFICATION_TYPES = ("confirmation", "reminder", "rescheduled", "canceled")
NOTIFICATION_STATES = ("pending", "claimed", "sent", "failed", "unknown", "suppressed")

# Job lifecycle. Transitions are enforced centrally in services/jobs.py; the
# UI never invents its own rules. Jobs are archived, never hard-deleted.
JOB_STATUSES = ("new", "quoted", "approved", "scheduled", "in_progress", "completed", "canceled")

# Uploaded-paperwork categories (bounded; "other" is the catch-all).
JOB_DOCUMENT_CATEGORIES = (
    "receipt",
    "quote",
    "invoice",
    "contract",
    "permit",
    "warranty",
    "photo",
    "other",
)

# Upload pipeline state. A file is inaccessible until scanning succeeds.
SCAN_STATES = ("pending", "clean", "infected", "failed")

# Commercial documents and their centrally enforced lifecycles.
COMMERCIAL_KINDS = ("quote", "invoice", "receipt")
QUOTE_STATUSES = ("draft", "sent", "viewed", "accepted", "declined", "expired", "voided")
INVOICE_STATUSES = ("draft", "sent", "viewed", "partially_paid", "paid", "overdue", "voided")
RECEIPT_STATUSES = ("issued", "voided")

PAYMENT_METHODS = ("cash", "check", "bank_transfer", "card_external", "other")

# Customer capability purposes for document access.
CAPABILITY_PURPOSES = ("view", "quote_response")

# Transactional document email: purposes and durable delivery states.
# "submitted" means the provider accepted the message; "delivered" is only
# ever set from a trusted provider callback, never from submission alone.
EMAIL_PURPOSES = ("quote", "invoice", "receipt", "job_document")
EMAIL_STATES = ("pending", "claimed", "submitted", "delivered", "failed", "unknown", "suppressed")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes that survive backends returning naive values."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed")
        return value

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'manager', 'team_member')", name="ck_users_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    # Optional, owner-managed presentation fields. The display name stands in
    # for the email everywhere a customer might see it; the notification phone
    # is a separate E.164 destination for staff alerts, never a login identity.
    display_name: Mapped[str] = mapped_column(String(100), default="")
    notification_phone: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(default=True)
    must_change_password: Mapped[bool] = mapped_column(default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # HMAC-SHA-256 digest of the opaque token; the raw token exists only in the cookie.
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    user: Mapped[User] = relationship()


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    user: Mapped[User] = relationship()


class Lead(Base):
    """Combined prospect/contact record. Leads are archived, never hard-deleted."""

    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'contacted', 'qualified', 'won', 'lost')", name="ck_leads_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str | None] = mapped_column(String(320), index=True)  # normalized lowercase
    phone: Mapped[str | None] = mapped_column(String(32), index=True)  # E.164 when available
    company: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    next_follow_up_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_contacted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    needs_review: Mapped[bool] = mapped_column(default=False)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # First-response tracking. The clock starts at the first inbound request;
    # only human actions stop it.
    first_inbound_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    response_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_response_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_response_seconds: Mapped[int | None] = mapped_column(Integer)
    response_target_met: Mapped[bool | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    assignee: Mapped[User | None] = relationship()


class LeadActivity(Base):
    """Chronological timeline entry; inbound requests are immutable history."""

    __tablename__ = "lead_activities"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str | None] = mapped_column(String(20))
    direction: Mapped[str | None] = mapped_column(String(10))  # inbound | outbound
    content: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    provider: Mapped[str | None] = mapped_column(String(100))
    external_event_id: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    author: Mapped[User | None] = relationship()


class CustomFieldDefinition(Base):
    """Owner-managed lead field definitions; keys are immutable, definitions
    are deactivated rather than deleted so stored values survive."""

    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('text', 'number', 'date', 'boolean', 'select')",
            name="ck_custom_field_definitions_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    label: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20))
    options: Mapped[list[str] | None] = mapped_column(JSON)  # select choices
    required: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class LeadCustomValue(Base):
    __tablename__ = "lead_custom_values"
    __table_args__ = (UniqueConstraint("lead_id", "field_id", name="uq_lead_custom_values"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    field_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("custom_field_definitions.id", ondelete="RESTRICT"), index=True
    )
    value: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    field: Mapped[CustomFieldDefinition] = relationship()


class LeadExternalIdentity(Base):
    """Stable provider sender IDs (Facebook PSID, WhatsApp ID, …) mapped to a
    lead, so provider-only channels reuse one lead instead of creating a new
    one per message."""

    __tablename__ = "lead_external_identities"
    __table_args__ = (
        UniqueConstraint(
            "channel", "provider", "external_sender_id", name="uq_lead_external_identities"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(100))
    external_sender_id: Mapped[str] = mapped_column(String(255))
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    lead: Mapped[Lead] = relationship()


class CommunicationSettings(Base):
    """Single-row operational messaging configuration (single-tenant install).

    Twilio credentials never live here — they stay in environment variables /
    n8n credential storage.
    """

    __tablename__ = "communication_settings"
    __table_args__ = (
        CheckConstraint("singleton = 'X'", name="ck_communication_settings_singleton"),
        CheckConstraint(
            "voice_alert_recipients IN ('business', 'assigned', 'both')",
            name="ck_communication_settings_voice_recipients",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Guarantees exactly one logical row: the value is fixed and unique, so a
    # concurrent second insert loses on the unique constraint instead of
    # creating a rival settings row.
    singleton: Mapped[str] = mapped_column(String(1), default="X", unique=True)
    business_name: Mapped[str] = mapped_column(String(200), default="Our team")
    form_title: Mapped[str] = mapped_column(String(200), default="Request a quote")
    form_intro: Mapped[str] = mapped_column(Text, default="")
    acknowledgment_enabled: Mapped[bool] = mapped_column(default=False)
    acknowledgment_template: Mapped[str] = mapped_column(
        Text,
        default="Hi {{lead_name}}, thanks for contacting {{business_name}}. We'll reply shortly.",
    )
    alert_enabled: Mapped[bool] = mapped_column(default=False)
    alert_template: Mapped[str] = mapped_column(
        Text, default="New {{source}} lead: {{lead_name}} (CRM ref {{lead_id}})."
    )
    alert_destination_phone: Mapped[str | None] = mapped_column(String(32))
    response_target_minutes: Mapped[int] = mapped_column(Integer, default=5)

    # --- Scheduling -----------------------------------------------------
    # IANA name; every stored timestamp is UTC, this is how they are shown.
    business_timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    appointment_duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    min_booking_notice_minutes: Mapped[int] = mapped_column(Integer, default=120)
    max_booking_days_ahead: Mapped[int] = mapped_column(Integer, default=60)
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, default=0)
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, default=15)
    self_booking_enabled: Mapped[bool] = mapped_column(default=False)
    appointment_confirmation_enabled: Mapped[bool] = mapped_column(default=False)
    appointment_reminder_enabled: Mapped[bool] = mapped_column(default=False)
    # Up to two reminder offsets, in minutes before the appointment start.
    reminder_offset_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    second_reminder_offset_minutes: Mapped[int | None] = mapped_column(Integer)
    upcoming_window_hours: Mapped[int] = mapped_column(Integer, default=24)
    confirmation_template: Mapped[str] = mapped_column(
        Text,
        default=(
            "Hi {{lead_name}}, your {{appointment_subject}} with {{business_name}} is booked "
            "for {{appointment_date}} at {{appointment_time}}."
        ),
    )
    reminder_template: Mapped[str] = mapped_column(
        Text,
        default=(
            "Reminder: {{business_name}} will see you on {{appointment_date}} at "
            "{{appointment_time}}."
        ),
    )
    appointment_canceled_template: Mapped[str] = mapped_column(
        Text,
        default=(
            "Your {{appointment_subject}} with {{business_name}} on {{appointment_date}} "
            "has been canceled."
        ),
    )
    appointment_rescheduled_template: Mapped[str] = mapped_column(
        Text,
        default=(
            "Your {{appointment_subject}} with {{business_name}} has moved to "
            "{{appointment_date}} at {{appointment_time}}."
        ),
    )
    # Weekday availability as {"mon": [["09:00", "17:00"]], ...} in business time.
    business_hours: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # --- Voice calls -----------------------------------------------------
    # Messaging and booking behaviour for the AI voice-call channel. Provider
    # and AI credentials never live here.
    voice_ack_enabled: Mapped[bool] = mapped_column(default=False)
    voice_ack_template: Mapped[str] = mapped_column(
        Text,
        default=(
            "Hi {{lead_name}}, thanks for calling {{business_name}}. "
            "We have your request and will be in touch shortly."
        ),
    )
    voice_alert_enabled: Mapped[bool] = mapped_column(default=False)
    voice_alert_template: Mapped[str] = mapped_column(
        Text,
        default=(
            "New call: {{lead_name}} — {{service_requested}}. "
            "{{call_summary}} (CRM ref {{lead_id}})"
        ),
    )
    # Who receives the alert: the business number, the assigned user's
    # notification phone, or both.
    voice_alert_recipients: Mapped[str] = mapped_column(String(16), default="business")
    # Staff member offered to callers when the lead has no active assignee.
    voice_default_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Full-transcript retention is off by default and always consent-gated.
    voice_transcript_retention_enabled: Mapped[bool] = mapped_column(default=False)
    voice_transcript_retention_days: Mapped[int] = mapped_column(Integer, default=30)

    # --- Documents & email ----------------------------------------------
    # Customer-facing document configuration. The verified From ADDRESS is
    # deployment configuration (environment), shown read-only — it is not a
    # row here so no user or API caller can change it.
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    quote_number_prefix: Mapped[str] = mapped_column(String(8), default="Q")
    invoice_number_prefix: Mapped[str] = mapped_column(String(8), default="INV")
    receipt_number_prefix: Mapped[str] = mapped_column(String(8), default="R")
    default_quote_valid_days: Mapped[int] = mapped_column(Integer, default=30)
    default_invoice_due_days: Mapped[int] = mapped_column(Integer, default=14)
    default_tax_rate_bp: Mapped[int] = mapped_column(Integer, default=0)
    business_email: Mapped[str] = mapped_column(String(320), default="")
    business_phone: Mapped[str] = mapped_column(String(32), default="")
    business_address: Mapped[str] = mapped_column(String(500), default="")
    business_registration_id: Mapped[str] = mapped_column(String(100), default="")
    email_from_display_name: Mapped[str] = mapped_column(String(200), default="")
    email_reply_to: Mapped[str] = mapped_column(String(320), default="")
    quote_email_subject: Mapped[str] = mapped_column(
        String(300), default="Your quote {{document_number}} from {{business_name}}"
    )
    quote_email_body: Mapped[str] = mapped_column(
        Text,
        default=(
            "Hi {{customer_name}},\n\nYour quote {{document_number}} for job {{job_number}} "
            "is ready: {{document_total}}.\n\nView and respond here: {{secure_document_link}}\n\n"
            "{{business_name}}"
        ),
    )
    invoice_email_subject: Mapped[str] = mapped_column(
        String(300), default="Invoice {{document_number}} from {{business_name}}"
    )
    invoice_email_body: Mapped[str] = mapped_column(
        Text,
        default=(
            "Hi {{customer_name}},\n\nInvoice {{document_number}} for job {{job_number}} "
            "is due {{due_date}}: {{document_total}}.\n\nView it here: "
            "{{secure_document_link}}\n\n{{business_name}}"
        ),
    )
    receipt_email_subject: Mapped[str] = mapped_column(
        String(300), default="Receipt {{document_number}} from {{business_name}}"
    )
    receipt_email_body: Mapped[str] = mapped_column(
        Text,
        default=(
            "Hi {{customer_name}},\n\nThank you. Your receipt {{document_number}} for "
            "{{document_total}} is here: {{secure_document_link}}\n\n{{business_name}}"
        ),
    )
    secure_link_expiry_days: Mapped[int] = mapped_column(Integer, default=30)
    email_attach_pdf_default: Mapped[bool] = mapped_column(default=True)

    # --- Branding -------------------------------------------------------
    # The logo is small, single-tenant and changes rarely, so it lives in the
    # database rather than pulling in an object store. What is stored is the
    # re-encoded image the server produced, never the bytes that were
    # uploaded; the digest doubles as the ETag.
    logo_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    logo_mime: Mapped[str | None] = mapped_column(String(32))
    logo_digest: Mapped[str | None] = mapped_column(String(64))
    logo_width: Mapped[int | None] = mapped_column(Integer)
    logo_height: Mapped[int | None] = mapped_column(Integer)
    logo_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class OutboundMessage(Base):
    """Durable record of every outbound SMS: created before the provider is
    contacted, never hard-deleted, deduplicated by idempotency key digest and
    by provider message SID."""

    __tablename__ = "outbound_messages"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('human_reply', 'auto_acknowledgment', 'staff_alert', 'appointment')",
            name="ck_outbound_messages_purpose",
        ),
        CheckConstraint(
            "status IN ('pending', 'submitted', 'delivered', 'failed', 'unknown')",
            name="ck_outbound_messages_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(24))
    to_phone: Mapped[str] = mapped_column(String(32))
    from_phone: Mapped[str | None] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    provider: Mapped[str] = mapped_column(String(32), default="twilio")
    provider_sid: Mapped[str | None] = mapped_column(String(64), unique=True)
    idempotency_key_digest: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    related_activity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_activities.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    author: Mapped[User | None] = relationship()


class Appointment(Base):
    """A scheduled visit. Never hard-deleted: cancellation is a status."""

    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'completed', 'canceled', 'no_show')",
            name="ck_appointments_status",
        ),
        CheckConstraint("origin IN ('staff', 'customer', 'voice')", name="ck_appointments_origin"),
        CheckConstraint("end_at > start_at", name="ck_appointments_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # RESTRICT: appointment history must survive; leads are archived, never
    # hard-deleted, so a lead deletion that would erase history is refused.
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="RESTRICT"), index=True
    )
    # Optional job association. Existing appointments stay unlinked until a
    # staff member assigns them deliberately; RESTRICT keeps job history safe.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), index=True
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    subject: Mapped[str] = mapped_column(String(200), default="Appointment")
    notes: Mapped[str] = mapped_column(Text, default="")
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # The business time zone in force when this appointment was scheduled.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    origin: Mapped[str] = mapped_column(String(16), default="staff")
    # Monotonic schedule revision: every reschedule or disposition increments
    # it, mutations carry the revision they saw, and stale writes get 409.
    revision: Mapped[int] = mapped_column(Integer, default=1)
    booking_reference: Mapped[str | None] = mapped_column(String(24), unique=True)
    # Opaque capability for customer-initiated reschedule/cancel; only its
    # digest is stored, so knowing the appointment UUID grants nothing.
    manage_token_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Digest of the customer's booking idempotency key, so a repeated
    # submission returns the original appointment instead of creating one.
    booking_key_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    canceled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    no_show_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    lead: Mapped["Lead"] = relationship()
    assignee: Mapped["User | None"] = relationship(foreign_keys=[assigned_to])


class BookingLink(Base):
    """Revocable, expiring capability that lets one customer self-book."""

    __tablename__ = "booking_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Only the keyed digest is stored; the raw token exists in the link alone.
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    lead: Mapped["Lead"] = relationship()


class AppointmentNotification(Base):
    """Durable record of one appointment message occurrence.

    (appointment, type, occurrence) is unique, so a confirmation or a given
    reminder offset can only ever exist once per appointment.
    """

    __tablename__ = "appointment_notifications"
    __table_args__ = (
        UniqueConstraint(
            "appointment_id", "type", "occurrence", name="uq_appointment_notifications"
        ),
        CheckConstraint(
            "type IN ('confirmation', 'reminder', 'rescheduled', 'canceled')",
            name="ck_appointment_notifications_type",
        ),
        CheckConstraint(
            "state IN ('pending', 'claimed', 'sent', 'failed', 'unknown', 'suppressed')",
            name="ck_appointment_notifications_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # RESTRICT: notification history must survive its appointment.
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("appointments.id", ondelete="RESTRICT"), index=True
    )
    type: Mapped[str] = mapped_column(String(16))
    # Distinguishes reminder offsets and reschedule generations.
    occurrence: Mapped[str] = mapped_column(String(64), default="1")
    # The appointment revision this message describes. Delivery re-checks it
    # immediately before the provider is contacted, so a reminder for an old
    # time can never go out after a reschedule.
    schedule_revision: Mapped[int] = mapped_column(Integer, default=1)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    outbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outbound_messages.id", ondelete="SET NULL")
    )
    idempotency_key_digest: Mapped[str] = mapped_column(String(64), unique=True)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attempted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failure_code: Mapped[str | None] = mapped_column(String(32))
    failure_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    appointment: Mapped["Appointment"] = relationship()


class VoiceCall(Base):
    """Durable record of one AI-answered phone call.

    `call_sid` is the provider-level idempotency identity — an identifier,
    never authority. The row keeps the structured facts the agent collected;
    raw provider payloads are not stored, and the optional transcript is
    consent-gated, bounded and purgeable while the summary survives.
    """

    __tablename__ = "voice_calls"
    __table_args__ = (
        CheckConstraint(
            "call_status IN ('completed', 'no_answer', 'abandoned', 'transferred', 'failed')",
            name="ck_voice_calls_status",
        ),
        CheckConstraint("urgency IN ('normal', 'urgent')", name="ck_voice_calls_urgency"),
        CheckConstraint(
            "transfer_outcome IN ('none', 'completed', 'failed')",
            name="ck_voice_calls_transfer",
        ),
        CheckConstraint(
            "consent_result IN ('granted', 'declined', 'not_asked')",
            name="ck_voice_calls_consent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), default="twilio")
    call_sid: Mapped[str] = mapped_column(String(64), unique=True)
    # RESTRICT: call history must survive; leads are never hard-deleted.
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="RESTRICT"), index=True
    )
    activity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_activities.id", ondelete="SET NULL")
    )
    # An appointment booked during this call, when there is one.
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL")
    )
    caller_phone: Mapped[str | None] = mapped_column(String(32), index=True)
    business_phone: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    call_status: Mapped[str] = mapped_column(String(16), default="completed")
    disposition: Mapped[str] = mapped_column(String(64), default="")
    caller_name: Mapped[str] = mapped_column(String(200), default="")
    service_requested: Mapped[str] = mapped_column(String(300), default="")
    service_address: Mapped[str] = mapped_column(String(300), default="")
    preferred_callback_window: Mapped[str] = mapped_column(String(200), default="")
    # A time the caller mentioned, recorded verbatim as a PREFERENCE — this is
    # never a booked appointment; a booking sets appointment_id instead.
    appointment_preference: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(String(2000), default="")
    urgency: Mapped[str] = mapped_column(String(8), default="normal")
    requires_human_follow_up: Mapped[bool] = mapped_column(default=False)
    transfer_outcome: Mapped[str] = mapped_column(String(16), default="none")
    disclosure_version: Mapped[str] = mapped_column(String(64), default="")
    consent_result: Mapped[str] = mapped_column(String(16), default="not_asked")
    # A retry with the same CallSid but a different immutable identity is
    # refused and flagged here rather than rewriting history.
    completion_conflict: Mapped[bool] = mapped_column(default=False)
    # Outcome of the automated messages for this call, so missing
    # destinations and ambiguous sends surface as controlled states.
    ack_state: Mapped[str] = mapped_column(String(16), default="skipped")
    alert_state: Mapped[str] = mapped_column(String(16), default="skipped")
    # Bounded provider reference only — never audio, credentials or URLs.
    recording_sid: Mapped[str | None] = mapped_column(String(64))
    # Consent-gated, bounded plain text; purged after the retention period.
    transcript_text: Mapped[str | None] = mapped_column(Text)
    retention_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    purged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    lead: Mapped["Lead"] = relationship()


class InboundEvent(Base):
    """Idempotency ledger for the n8n inbound endpoint: one row per processed
    Idempotency-Key, storing only a keyed digest and the replayable result."""

    __tablename__ = "inbound_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    idempotency_key_digest: Mapped[str] = mapped_column(String(64), unique=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    activity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_activities.id", ondelete="CASCADE")
    )
    lead_created: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class NumberSequence(Base):
    """Concurrency-safe yearly counters for human-readable numbers.

    Allocation takes a row lock (FOR UPDATE) on the (kind, year) row, so two
    concurrent issuances can never mint the same number. Gaps caused by a
    rolled-back transaction are acceptable and documented; reuse is not.
    """

    __tablename__ = "number_sequences"
    __table_args__ = (UniqueConstraint("kind", "year", name="uq_number_sequences"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))  # job | quote | invoice | receipt
    year: Mapped[int] = mapped_column(Integer)
    last_value: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Job(Base):
    """One piece of work for one customer. A customer (lead) may have many
    jobs; every document and commercial record belongs to exactly one job, so
    the customer relationship is always derived job → lead and can never
    disagree with a separately stored customer id.

    Jobs are archived, never hard-deleted; jobs with documents, issued
    commercial records, payments or appointments are protected by RESTRICT
    foreign keys on those tables.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'quoted', 'approved', 'scheduled', 'in_progress', "
            "'completed', 'canceled')",
            name="ck_jobs_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Human-readable, immutable, concurrency-safe (allocated via NumberSequence).
    job_number: Mapped[str] = mapped_column(String(24), unique=True)
    # RESTRICT: a customer with jobs is archived, never erased.
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    service_type: Mapped[str] = mapped_column(String(200), default="")
    service_address: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(16), default="new")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    internal_notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    lead: Mapped["Lead"] = relationship()
    assignee: Mapped["User | None"] = relationship(foreign_keys=[assigned_to])


class JobDocument(Base):
    """One uploaded piece of paperwork, always belonging to a job.

    The binary lives in object storage under `storage_key`; PostgreSQL holds
    metadata, relationships, hashes and audit state only. A file stays in
    quarantine (inaccessible) until content validation and malware scanning
    both succeed. Deleted uploads keep this row as an audit tombstone with the
    stored object removed.
    """

    __tablename__ = "job_documents"
    __table_args__ = (
        CheckConstraint(
            "category IN ('receipt', 'quote', 'invoice', 'contract', 'permit', "
            "'warranty', 'photo', 'other')",
            name="ck_job_documents_category",
        ),
        CheckConstraint(
            "scan_state IN ('pending', 'clean', 'infected', 'failed')",
            name="ck_job_documents_scan_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # RESTRICT: document history must survive; jobs are archived, not deleted.
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(16), default="other")
    description: Mapped[str] = mapped_column(String(1000), default="")
    # Sanitized display filename — never a path, never trusted content.
    original_filename: Mapped[str] = mapped_column(String(200), default="")
    content_type: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # Object-storage keys. The quarantine key is set on upload; the permanent
    # key only after validation and scanning succeed. Keys grant no authority.
    storage_key: Mapped[str | None] = mapped_column(String(300))
    quarantine_key: Mapped[str | None] = mapped_column(String(300))
    # Server-generated normalized preview image (images only), safe to inline.
    preview_storage_key: Mapped[str | None] = mapped_column(String(300))
    scan_state: Mapped[str] = mapped_column(String(16), default="pending")
    scan_detail: Mapped[str | None] = mapped_column(String(300))
    scanned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Audit tombstone for deleted uploads: the row survives, the object is gone.
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    delete_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    job: Mapped["Job"] = relationship()


class CommercialDocument(Base):
    """A quote, invoice or receipt for a job.

    Money is integer minor units in `currency` — never floating point. Drafts
    are editable; issuing snapshots the document into an immutable
    CommercialDocumentVersion (with the exact PDF), assigns the final number,
    and later corrections create new versions or void-and-reissue — history is
    never rewritten. Never hard-deleted.
    """

    __tablename__ = "commercial_documents"
    __table_args__ = (
        UniqueConstraint("kind", "number", name="uq_commercial_documents_number"),
        CheckConstraint(
            "kind IN ('quote', 'invoice', 'receipt')", name="ck_commercial_documents_kind"
        ),
        CheckConstraint(
            "status IN ('draft', 'sent', 'viewed', 'accepted', 'declined', 'expired', "
            "'voided', 'partially_paid', 'paid', 'overdue', 'issued')",
            name="ck_commercial_documents_status",
        ),
        CheckConstraint("subtotal_minor >= 0", name="ck_commercial_documents_subtotal"),
        CheckConstraint("total_minor >= 0", name="ck_commercial_documents_total"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(8))
    # RESTRICT: commercial history must survive its job.
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="draft")
    # NULL until issuance; then immutable and unique per kind.
    number: Mapped[str | None] = mapped_column(String(24))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    # Document-level discount in basis points (0–10000), applied to the
    # subtotal; see services/commercial.py for the documented rounding rules.
    discount_bp: Mapped[int] = mapped_column(Integer, default=0)
    subtotal_minor: Mapped[int] = mapped_column(Integer, default=0)
    discount_total_minor: Mapped[int] = mapped_column(Integer, default=0)
    tax_total_minor: Mapped[int] = mapped_column(Integer, default=0)
    total_minor: Mapped[int] = mapped_column(Integer, default=0)
    customer_notes: Mapped[str] = mapped_column(Text, default="")
    terms: Mapped[str] = mapped_column(Text, default="")
    # Quote validity / invoice dates (business-timezone calendar dates).
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    issued_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Current active issued version number (versions are immutable rows).
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    # Quote response — recorded once, idempotently, with the snapshot hash the
    # customer actually saw.
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    response_name: Mapped[str | None] = mapped_column(String(200))
    response_snapshot_sha256: Mapped[str | None] = mapped_column(String(64))
    # Idempotent quote→invoice conversion: set once under the document lock.
    source_quote_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("commercial_documents.id", ondelete="SET NULL")
    )
    source_quote_version: Mapped[int | None] = mapped_column(Integer)
    converted_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("commercial_documents.id", ondelete="SET NULL")
    )
    # Receipts: the payment they certify. use_alter breaks the FK cycle with
    # payments (which reference commercial_documents) at create_all time; the
    # explicit name lets ALTER emit and drop it deterministically.
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "payments.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_commercial_documents_payment_id",
        )
    )
    amount_paid_minor: Mapped[int] = mapped_column(Integer, default=0)
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    voided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    void_reason: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    job: Mapped["Job"] = relationship()


class CommercialLineItem(Base):
    """One draft line. Issued snapshots copy lines into the immutable version
    payload; these rows always describe the current draft state only."""

    __tablename__ = "commercial_line_items"
    __table_args__ = (
        CheckConstraint("quantity_milli > 0", name="ck_commercial_line_items_quantity"),
        CheckConstraint("unit_price_minor >= 0", name="ck_commercial_line_items_unit_price"),
        CheckConstraint(
            "discount_bp >= 0 AND discount_bp <= 10000", name="ck_commercial_line_items_discount"
        ),
        CheckConstraint(
            "tax_rate_bp >= 0 AND tax_rate_bp <= 5000", name="ck_commercial_line_items_tax"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commercial_documents.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(500))
    # Quantity in thousandths (2.5 hours = 2500); money in integer minor units.
    quantity_milli: Mapped[int] = mapped_column(Integer, default=1000)
    unit: Mapped[str] = mapped_column(String(20), default="")
    unit_price_minor: Mapped[int] = mapped_column(Integer, default=0)
    discount_bp: Mapped[int] = mapped_column(Integer, default=0)
    tax_rate_bp: Mapped[int] = mapped_column(Integer, default=0)
    line_total_minor: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class CommercialDocumentVersion(Base):
    """The immutable snapshot of one issued document version.

    `payload` holds everything needed to reproduce the document (parties,
    lines, totals, dates, notes); `pdf_storage_key`/`pdf_sha256` reference the
    exact bytes the customer received. Never edited, never deleted.
    """

    __tablename__ = "commercial_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_commercial_document_versions"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commercial_documents.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    number: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    pdf_storage_key: Mapped[str] = mapped_column(String(300))
    pdf_sha256: Mapped[str] = mapped_column(String(64))
    pdf_byte_size: Mapped[int] = mapped_column(Integer)
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    document: Mapped["CommercialDocument"] = relationship()


class Payment(Base):
    """A manually recorded, externally completed payment against an invoice.

    Posted payments are never edited in place: corrections are audited
    reversals. No field ever stores card numbers, CVV or bank credentials —
    reference/note inputs are validated against likely PAN/CVV content.
    """

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_payments_amount"),
        CheckConstraint(
            "method IN ('cash', 'check', 'bank_transfer', 'card_external', 'other')",
            name="ck_payments_method",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # RESTRICT: payment history must survive its invoice.
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commercial_documents.id", ondelete="RESTRICT"), index=True
    )
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    method: Mapped[str] = mapped_column(String(16))
    paid_on: Mapped[datetime] = mapped_column(UTCDateTime)
    reference: Mapped[str] = mapped_column(String(100), default="")
    internal_note: Mapped[str] = mapped_column(String(500), default="")
    idempotency_key_digest: Mapped[str] = mapped_column(String(64), unique=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # The receipt issued for this payment (a CommercialDocument of kind receipt).
    receipt_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("commercial_documents.id", ondelete="SET NULL")
    )
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    voided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    void_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class DocumentCapability(Base):
    """Expiring, revocable customer access to ONE immutable document version.

    Only the keyed digest is stored; the raw capability exists in the link
    alone and travels in request bodies, never URLs the API logs. A capability
    grants exactly the referenced version plus, for quotes, the response
    action — nothing else.
    """

    __tablename__ = "document_capabilities"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('view', 'quote_response')", name="ck_document_capabilities_purpose"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commercial_document_versions.id", ondelete="RESTRICT"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(16), default="view")
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    first_viewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    version: Mapped["CommercialDocumentVersion"] = relationship()


class EmailDelivery(Base):
    """Durable record of one intended document email.

    Created and committed BEFORE n8n or any provider is contacted. n8n claims
    work with a lease, sends via the installation's verified sender, and
    reports the outcome; an ambiguous outcome after submission may have begun
    becomes `unknown` and is never retried automatically.
    """

    __tablename__ = "email_deliveries"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('quote', 'invoice', 'receipt', 'job_document')",
            name="ck_email_deliveries_purpose",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'submitted', 'delivered', 'failed', "
            "'unknown', 'suppressed')",
            name="ck_email_deliveries_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # RESTRICT: delivery history must survive.
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(16))
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("commercial_document_versions.id", ondelete="RESTRICT")
    )
    job_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_documents.id", ondelete="RESTRICT")
    )
    recipient: Mapped[str] = mapped_column(String(320))
    from_name: Mapped[str] = mapped_column(String(200), default="")
    from_address: Mapped[str] = mapped_column(String(320))
    reply_to: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(String(500))
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text, default="")
    # Whether the PDF travels as an attachment or as a secure link only.
    attach_pdf: Mapped[bool] = mapped_column(default=True)
    capability_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_capabilities.id", ondelete="SET NULL")
    )
    template_generation: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key_digest: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failure_class: Mapped[str | None] = mapped_column(String(32))
    failure_message: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    job: Mapped["Job"] = relationship()
