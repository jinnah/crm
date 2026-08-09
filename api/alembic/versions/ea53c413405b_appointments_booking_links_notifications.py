"""appointments booking links notifications

Adds scheduling: appointments, revocable booking links, durable appointment
notifications, and the owner-level scheduling settings. Existing rows keep
their values — every new settings column has a server default.

Revision ID: ea53c413405b
Revises: f93c51bfa8ee
Create Date: 2026-08-09

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ea53c413405b"
down_revision: str | Sequence[str] | None = "f93c51bfa8ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONFIRMATION_DEFAULT = (
    "Hi {{lead_name}}, your {{appointment_subject}} with {{business_name}} is booked "
    "for {{appointment_date}} at {{appointment_time}}."
)
REMINDER_DEFAULT = (
    "Reminder: {{business_name}} will see you on {{appointment_date}} at {{appointment_time}}."
)
CANCELED_DEFAULT = (
    "Your {{appointment_subject}} with {{business_name}} on {{appointment_date}} has been canceled."
)
RESCHEDULED_DEFAULT = (
    "Your {{appointment_subject}} with {{business_name}} has moved to "
    "{{appointment_date}} at {{appointment_time}}."
)

SETTINGS_COLUMNS = (
    ("business_timezone", sa.String(length=64), "UTC"),
    ("appointment_duration_minutes", sa.Integer(), "60"),
    ("min_booking_notice_minutes", sa.Integer(), "120"),
    ("max_booking_days_ahead", sa.Integer(), "60"),
    ("buffer_before_minutes", sa.Integer(), "0"),
    ("buffer_after_minutes", sa.Integer(), "15"),
    ("self_booking_enabled", sa.Boolean(), "false"),
    ("appointment_confirmation_enabled", sa.Boolean(), "false"),
    ("appointment_reminder_enabled", sa.Boolean(), "false"),
    ("reminder_offset_minutes", sa.Integer(), "1440"),
    ("upcoming_window_hours", sa.Integer(), "24"),
    ("confirmation_template", sa.Text(), CONFIRMATION_DEFAULT),
    ("reminder_template", sa.Text(), REMINDER_DEFAULT),
    ("appointment_canceled_template", sa.Text(), CANCELED_DEFAULT),
    ("appointment_rescheduled_template", sa.Text(), RESCHEDULED_DEFAULT),
)


def _literal(default: str) -> str:
    """SQL literal for a server default: numbers and booleans bare, text quoted."""
    if default in ("true", "false") or default.isdigit():
        return default
    return "'" + default.replace("'", "''") + "'"


def upgrade() -> None:
    # Every new column carries a server default, so existing installations
    # keep working and their configured values are untouched.
    for name, type_, default in SETTINGS_COLUMNS:
        op.add_column(
            "communication_settings",
            sa.Column(name, type_, nullable=False, server_default=sa.text(_literal(default))),
        )
    op.add_column(
        "communication_settings",
        sa.Column("second_reminder_offset_minutes", sa.Integer(), nullable=True),
    )
    op.add_column("communication_settings", sa.Column("business_hours", sa.JSON(), nullable=True))

    # Appointment messages are a new outbound purpose.
    op.drop_constraint("ck_outbound_messages_purpose", "outbound_messages", type_="check")
    op.create_check_constraint(
        "ck_outbound_messages_purpose",
        "outbound_messages",
        "purpose IN ('human_reply', 'auto_acknowledgment', 'staff_alert', 'appointment')",
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("booking_reference", sa.String(length=24), nullable=True),
        sa.Column("manage_token_digest", sa.String(length=64), nullable=True),
        sa.Column("booking_key_digest", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("no_show_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('scheduled', 'completed', 'canceled', 'no_show')",
            name="ck_appointments_status",
        ),
        sa.CheckConstraint("origin IN ('staff', 'customer')", name="ck_appointments_origin"),
        sa.CheckConstraint("end_at > start_at", name="ck_appointments_range"),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_appointments_lead_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"], ["users.id"], name="fk_appointments_assigned_to", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_appointments_created_by", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name="fk_appointments_updated_by", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_appointments"),
        sa.UniqueConstraint("booking_reference", name="uq_appointments_booking_reference"),
        sa.UniqueConstraint("manage_token_digest", name="uq_appointments_manage_token"),
        sa.UniqueConstraint("booking_key_digest", name="uq_appointments_booking_key"),
    )
    op.create_index("ix_appointments_lead_id", "appointments", ["lead_id"])
    op.create_index("ix_appointments_assigned_to", "appointments", ["assigned_to"])
    op.create_index("ix_appointments_start_at", "appointments", ["start_at"])

    op.create_table(
        "booking_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_booking_links_lead_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name="fk_booking_links_assigned_to",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_booking_links_created_by", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_booking_links"),
        sa.UniqueConstraint("token_digest", name="uq_booking_links_token_digest"),
    )
    op.create_index("ix_booking_links_lead_id", "booking_links", ["lead_id"])

    op.create_table(
        "appointment_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("occurrence", sa.String(length=64), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("outbound_message_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('confirmation', 'reminder', 'rescheduled', 'canceled')",
            name="ck_appointment_notifications_type",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'claimed', 'sent', 'failed', 'unknown', 'suppressed')",
            name="ck_appointment_notifications_state",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name="fk_appointment_notifications_appointment_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["outbound_message_id"],
            ["outbound_messages.id"],
            name="fk_appointment_notifications_message_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_appointment_notifications"),
        sa.UniqueConstraint(
            "appointment_id", "type", "occurrence", name="uq_appointment_notifications"
        ),
        sa.UniqueConstraint(
            "idempotency_key_digest", name="uq_appointment_notifications_key_digest"
        ),
    )
    op.create_index(
        "ix_appointment_notifications_appointment_id",
        "appointment_notifications",
        ["appointment_id"],
    )
    op.create_index(
        "ix_appointment_notifications_scheduled_at",
        "appointment_notifications",
        ["scheduled_at"],
    )

    # Hard backstop against double-booking one staff member, independent of
    # any application logic. btree_gist provides equality over uuid inside an
    # exclusion constraint.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        op.execute(
            """
            ALTER TABLE appointments
            ADD CONSTRAINT ex_appointments_no_overlap
            EXCLUDE USING gist (
                assigned_to WITH =,
                tstzrange(start_at, end_at) WITH &&
            ) WHERE (status = 'scheduled' AND assigned_to IS NOT NULL)
            """
        )


def downgrade() -> None:
    op.drop_constraint("ck_outbound_messages_purpose", "outbound_messages", type_="check")
    op.create_check_constraint(
        "ck_outbound_messages_purpose",
        "outbound_messages",
        "purpose IN ('human_reply', 'auto_acknowledgment', 'staff_alert')",
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS ex_appointments_no_overlap")
    op.drop_index(
        "ix_appointment_notifications_scheduled_at", table_name="appointment_notifications"
    )
    op.drop_index(
        "ix_appointment_notifications_appointment_id", table_name="appointment_notifications"
    )
    op.drop_table("appointment_notifications")
    op.drop_index("ix_booking_links_lead_id", table_name="booking_links")
    op.drop_table("booking_links")
    op.drop_index("ix_appointments_start_at", table_name="appointments")
    op.drop_index("ix_appointments_assigned_to", table_name="appointments")
    op.drop_index("ix_appointments_lead_id", table_name="appointments")
    op.drop_table("appointments")
    op.drop_column("communication_settings", "business_hours")
    op.drop_column("communication_settings", "second_reminder_offset_minutes")
    for name, _type, _default in reversed(SETTINGS_COLUMNS):
        op.drop_column("communication_settings", name)
