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
CHANNELS = ("web_form", "phone_call", "sms", "whatsapp", "facebook", "email", "other", "manual")

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
)

# Outbound message purposes and delivery states.
MESSAGE_PURPOSES = ("human_reply", "auto_acknowledgment", "staff_alert", "appointment")
MESSAGE_STATUSES = ("pending", "submitted", "delivered", "failed", "unknown")

# Appointment lifecycle. Appointments are never hard-deleted.
APPOINTMENT_STATUSES = ("scheduled", "completed", "canceled", "no_show")
APPOINTMENT_ORIGINS = ("staff", "customer")

# Appointment notification kinds and their durable delivery state.
NOTIFICATION_TYPES = ("confirmation", "reminder", "rescheduled", "canceled")
NOTIFICATION_STATES = ("pending", "claimed", "sent", "failed", "unknown", "suppressed")


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
        CheckConstraint("origin IN ('staff', 'customer')", name="ck_appointments_origin"),
        CheckConstraint("end_at > start_at", name="ck_appointments_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
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
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(16))
    # Distinguishes reminder offsets and reschedule generations.
    occurrence: Mapped[str] = mapped_column(String(64), default="1")
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
