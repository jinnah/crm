"""communication settings outbound messages response tracking

Revision ID: b38622ca11e1
Revises: 9de32ba7f789
Create Date: 2026-08-09

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b38622ca11e1"
down_revision: str | Sequence[str] | None = "9de32ba7f789"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "communication_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("business_name", sa.String(length=200), nullable=False),
        sa.Column("form_title", sa.String(length=200), nullable=False),
        sa.Column("form_intro", sa.Text(), nullable=False),
        sa.Column("acknowledgment_enabled", sa.Boolean(), nullable=False),
        sa.Column("acknowledgment_template", sa.Text(), nullable=False),
        sa.Column("alert_enabled", sa.Boolean(), nullable=False),
        sa.Column("alert_template", sa.Text(), nullable=False),
        sa.Column("alert_destination_phone", sa.String(length=32), nullable=True),
        sa.Column("response_target_minutes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_communication_settings"),
    )

    op.create_table(
        "outbound_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=24), nullable=False),
        sa.Column("to_phone", sa.String(length=32), nullable=False),
        sa.Column("from_phone", sa.String(length=32), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_sid", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("related_activity_id", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('human_reply', 'auto_acknowledgment', 'staff_alert')",
            name="ck_outbound_messages_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'submitted', 'delivered', 'failed', 'unknown')",
            name="ck_outbound_messages_status",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_outbound_messages_lead_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_outbound_messages_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["related_activity_id"],
            ["lead_activities.id"],
            name="fk_outbound_messages_related_activity_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbound_messages"),
        sa.UniqueConstraint("idempotency_key_digest", name="uq_outbound_messages_key_digest"),
        sa.UniqueConstraint("provider_sid", name="uq_outbound_messages_provider_sid"),
    )
    op.create_index("ix_outbound_messages_lead_id", "outbound_messages", ["lead_id"])

    op.add_column("leads", sa.Column("first_inbound_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("response_due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "leads", sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("leads", sa.Column("first_response_seconds", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("response_target_met", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "response_target_met")
    op.drop_column("leads", "first_response_seconds")
    op.drop_column("leads", "first_response_at")
    op.drop_column("leads", "response_due_at")
    op.drop_column("leads", "first_inbound_at")
    op.drop_index("ix_outbound_messages_lead_id", table_name="outbound_messages")
    op.drop_table("outbound_messages")
    op.drop_table("communication_settings")
