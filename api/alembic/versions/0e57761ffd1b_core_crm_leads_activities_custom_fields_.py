"""core crm leads activities custom fields inbound events

Revision ID: 0e57761ffd1b
Revises: 44b111715606
Create Date: 2026-08-08

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0e57761ffd1b"
down_revision: str | Sequence[str] | None = "44b111715606"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("company", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('new', 'contacted', 'qualified', 'won', 'lost')", name="ck_leads_status"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"], ["users.id"], name="fk_leads_assigned_to", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_leads"),
    )
    op.create_index("ix_leads_email", "leads", ["email"])
    op.create_index("ix_leads_phone", "leads", ["phone"])
    op.create_index("ix_leads_assigned_to", "leads", ["assigned_to"])

    op.create_table(
        "lead_activities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=True),
        sa.Column("direction", sa.String(length=10), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("external_event_id", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_lead_activities_lead_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_lead_activities_created_by", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_lead_activities"),
    )
    op.create_index("ix_lead_activities_lead_id", "lead_activities", ["lead_id"])

    op.create_table(
        "custom_field_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('text', 'number', 'date', 'boolean', 'select')",
            name="ck_custom_field_definitions_type",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_custom_field_definitions"),
        sa.UniqueConstraint("key", name="uq_custom_field_definitions_key"),
    )

    op.create_table(
        "lead_custom_values",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_lead_custom_values_lead_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["custom_field_definitions.id"],
            name="fk_lead_custom_values_field_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_lead_custom_values"),
        sa.UniqueConstraint("lead_id", "field_id", name="uq_lead_custom_values"),
    )
    op.create_index("ix_lead_custom_values_lead_id", "lead_custom_values", ["lead_id"])
    op.create_index("ix_lead_custom_values_field_id", "lead_custom_values", ["field_id"])

    op.create_table(
        "inbound_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("activity_id", sa.Uuid(), nullable=False),
        sa.Column("lead_created", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"], ["leads.id"], name="fk_inbound_events_lead_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["lead_activities.id"],
            name="fk_inbound_events_activity_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inbound_events"),
        sa.UniqueConstraint("idempotency_key_digest", name="uq_inbound_events_key_digest"),
    )


def downgrade() -> None:
    op.drop_table("inbound_events")
    op.drop_index("ix_lead_custom_values_field_id", table_name="lead_custom_values")
    op.drop_index("ix_lead_custom_values_lead_id", table_name="lead_custom_values")
    op.drop_table("lead_custom_values")
    op.drop_table("custom_field_definitions")
    op.drop_index("ix_lead_activities_lead_id", table_name="lead_activities")
    op.drop_table("lead_activities")
    op.drop_index("ix_leads_assigned_to", table_name="leads")
    op.drop_index("ix_leads_phone", table_name="leads")
    op.drop_index("ix_leads_email", table_name="leads")
    op.drop_table("leads")
