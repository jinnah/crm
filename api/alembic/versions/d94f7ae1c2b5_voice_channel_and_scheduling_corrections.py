"""voice channel and scheduling corrections

Revision ID: d94f7ae1c2b5
Revises: c7a41d92e6b3
Create Date: 2026-08-09

Adds the appointment schedule revision, notification schedule revisions,
historical-retention foreign keys (leads no longer cascade-delete appointment
history; appointments no longer cascade-delete notification history), the
voice-call channel (voice_calls table, voice settings, user notification
fields) and the widened appointment-origin constraint.

Every change preserves existing rows. The downgrade refuses to run while it
would destroy irreplaceable populated data (voice calls, voice-origin
appointments) and stops BEFORE any mutation with an actionable error.
"""

import sqlalchemy as sa

from alembic import op

revision = "d94f7ae1c2b5"
down_revision = "c7a41d92e6b3"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # --- Appointment schedule revisions ---------------------------------
    op.add_column(
        "appointments",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "appointment_notifications",
        sa.Column("schedule_revision", sa.Integer(), nullable=False, server_default="1"),
    )

    # --- Historical retention: no cascading deletes ----------------------
    if _is_postgres():
        op.drop_constraint("fk_appointments_lead_id", "appointments", type_="foreignkey")
        op.create_foreign_key(
            "fk_appointments_lead_id",
            "appointments",
            "leads",
            ["lead_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.drop_constraint(
            "fk_appointment_notifications_appointment_id",
            "appointment_notifications",
            type_="foreignkey",
        )
        op.create_foreign_key(
            "fk_appointment_notifications_appointment_id",
            "appointment_notifications",
            "appointments",
            ["appointment_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    # --- Voice appointment origin ----------------------------------------
    if _is_postgres():
        op.drop_constraint("ck_appointments_origin", "appointments", type_="check")
        op.create_check_constraint(
            "ck_appointments_origin",
            "appointments",
            "origin IN ('staff', 'customer', 'voice')",
        )

    # --- Owner-managed user presentation fields ---------------------------
    op.add_column(
        "users",
        sa.Column("display_name", sa.String(length=100), nullable=False, server_default=""),
    )
    op.add_column("users", sa.Column("notification_phone", sa.String(length=32), nullable=True))

    # --- Voice settings ----------------------------------------------------
    op.add_column(
        "communication_settings",
        sa.Column("voice_ack_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "communication_settings",
        sa.Column(
            "voice_ack_template",
            sa.Text(),
            nullable=False,
            server_default=(
                "Hi {{lead_name}}, thanks for calling {{business_name}}. "
                "We have your request and will be in touch shortly."
            ),
        ),
    )
    op.add_column(
        "communication_settings",
        sa.Column("voice_alert_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "communication_settings",
        sa.Column(
            "voice_alert_template",
            sa.Text(),
            nullable=False,
            server_default=(
                "New call: {{lead_name}} — {{service_requested}}. "
                "{{call_summary}} (CRM ref {{lead_id}})"
            ),
        ),
    )
    op.add_column(
        "communication_settings",
        sa.Column(
            "voice_alert_recipients",
            sa.String(length=16),
            nullable=False,
            server_default="business",
        ),
    )
    op.add_column(
        "communication_settings",
        sa.Column("voice_default_staff_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_communication_settings_voice_default_staff_id",
        "communication_settings",
        "users",
        ["voice_default_staff_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "communication_settings",
        sa.Column(
            "voice_transcript_retention_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "communication_settings",
        sa.Column(
            "voice_transcript_retention_days",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )
    if _is_postgres():
        op.create_check_constraint(
            "ck_communication_settings_voice_recipients",
            "communication_settings",
            "voice_alert_recipients IN ('business', 'assigned', 'both')",
        )

    # --- Voice calls -------------------------------------------------------
    op.create_table(
        "voice_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="twilio"),
        sa.Column("call_sid", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("activity_id", sa.Uuid(), nullable=True),
        sa.Column("appointment_id", sa.Uuid(), nullable=True),
        sa.Column("caller_phone", sa.String(length=32), nullable=True),
        sa.Column("business_phone", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("call_status", sa.String(length=16), nullable=False, server_default="completed"),
        sa.Column("disposition", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("caller_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("service_requested", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("service_address", sa.String(length=300), nullable=False, server_default=""),
        sa.Column(
            "preferred_callback_window",
            sa.String(length=200),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "appointment_preference", sa.String(length=200), nullable=False, server_default=""
        ),
        sa.Column("summary", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("urgency", sa.String(length=8), nullable=False, server_default="normal"),
        sa.Column(
            "requires_human_follow_up",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("transfer_outcome", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("disclosure_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "consent_result", sa.String(length=16), nullable=False, server_default="not_asked"
        ),
        sa.Column("completion_conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ack_state", sa.String(length=16), nullable=False, server_default="skipped"),
        sa.Column("alert_state", sa.String(length=16), nullable=False, server_default="skipped"),
        sa.Column("recording_sid", sa.String(length=64), nullable=True),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_voice_calls_lead_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["lead_activities.id"],
            name="fk_voice_calls_activity_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name="fk_voice_calls_appointment_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("call_sid", name="uq_voice_calls_call_sid"),
        sa.CheckConstraint(
            "call_status IN ('completed', 'no_answer', 'abandoned', 'transferred', 'failed')",
            name="ck_voice_calls_status",
        ),
        sa.CheckConstraint("urgency IN ('normal', 'urgent')", name="ck_voice_calls_urgency"),
        sa.CheckConstraint(
            "transfer_outcome IN ('none', 'completed', 'failed')",
            name="ck_voice_calls_transfer",
        ),
        sa.CheckConstraint(
            "consent_result IN ('granted', 'declined', 'not_asked')",
            name="ck_voice_calls_consent",
        ),
    )
    op.create_index("ix_voice_calls_lead_id", "voice_calls", ["lead_id"])
    op.create_index("ix_voice_calls_caller_phone", "voice_calls", ["caller_phone"])


def downgrade() -> None:
    """Refuse to destroy irreplaceable populated data; stop BEFORE mutating."""
    connection = op.get_bind()

    calls = connection.execute(sa.text("SELECT count(*) FROM voice_calls")).scalar_one()
    if calls:
        raise RuntimeError(
            f"{calls} voice call record(s) exist and downgrading would delete this call "
            "history permanently. Export or archive the voice_calls table first, remove "
            "the rows deliberately, then run the downgrade again."
        )
    voice_appts = connection.execute(
        sa.text("SELECT count(*) FROM appointments WHERE origin = 'voice'")
    ).scalar_one()
    if voice_appts:
        raise RuntimeError(
            f"{voice_appts} appointment(s) have origin 'voice', which the downgraded "
            "constraint cannot represent. Resolve or re-attribute them before downgrading."
        )

    op.drop_index("ix_voice_calls_caller_phone", table_name="voice_calls")
    op.drop_index("ix_voice_calls_lead_id", table_name="voice_calls")
    op.drop_table("voice_calls")

    if _is_postgres():
        op.drop_constraint(
            "ck_communication_settings_voice_recipients",
            "communication_settings",
            type_="check",
        )
    op.drop_constraint(
        "fk_communication_settings_voice_default_staff_id",
        "communication_settings",
        type_="foreignkey",
    )
    op.drop_column("communication_settings", "voice_transcript_retention_days")
    op.drop_column("communication_settings", "voice_transcript_retention_enabled")
    op.drop_column("communication_settings", "voice_default_staff_id")
    op.drop_column("communication_settings", "voice_alert_recipients")
    op.drop_column("communication_settings", "voice_alert_template")
    op.drop_column("communication_settings", "voice_alert_enabled")
    op.drop_column("communication_settings", "voice_ack_template")
    op.drop_column("communication_settings", "voice_ack_enabled")

    op.drop_column("users", "notification_phone")
    op.drop_column("users", "display_name")

    if _is_postgres():
        op.drop_constraint("ck_appointments_origin", "appointments", type_="check")
        op.create_check_constraint(
            "ck_appointments_origin", "appointments", "origin IN ('staff', 'customer')"
        )
        op.drop_constraint(
            "fk_appointment_notifications_appointment_id",
            "appointment_notifications",
            type_="foreignkey",
        )
        op.create_foreign_key(
            "fk_appointment_notifications_appointment_id",
            "appointment_notifications",
            "appointments",
            ["appointment_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.drop_constraint("fk_appointments_lead_id", "appointments", type_="foreignkey")
        op.create_foreign_key(
            "fk_appointments_lead_id",
            "appointments",
            "leads",
            ["lead_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.drop_column("appointment_notifications", "schedule_revision")
    op.drop_column("appointments", "revision")
