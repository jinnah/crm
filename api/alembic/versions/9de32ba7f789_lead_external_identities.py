"""lead external identities

Revision ID: 9de32ba7f789
Revises: 0e57761ffd1b
Create Date: 2026-08-08

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9de32ba7f789"
down_revision: str | Sequence[str] | None = "0e57761ffd1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lead_external_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("external_sender_id", sa.String(length=255), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            name="fk_lead_external_identities_lead_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_lead_external_identities"),
        sa.UniqueConstraint(
            "channel", "provider", "external_sender_id", name="uq_lead_external_identities"
        ),
    )
    op.create_index("ix_lead_external_identities_lead_id", "lead_external_identities", ["lead_id"])


def downgrade() -> None:
    op.drop_index("ix_lead_external_identities_lead_id", table_name="lead_external_identities")
    op.drop_table("lead_external_identities")
