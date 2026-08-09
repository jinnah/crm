"""communication settings singleton

Guarantees exactly one logical communication-settings row. Existing
installations keep their configured settings: the oldest row is retained and
any accidental extras are removed before the unique key is applied.

Revision ID: f93c51bfa8ee
Revises: b38622ca11e1
Create Date: 2026-08-09

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f93c51bfa8ee"
down_revision: str | Sequence[str] | None = "b38622ca11e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep the earliest row (the one an installation has been configuring)
    # and discard any duplicates a pre-constraint race may have created.
    op.execute(
        sa.text(
            """
            DELETE FROM communication_settings
            WHERE id NOT IN (
                SELECT id FROM communication_settings ORDER BY created_at, id LIMIT 1
            )
            """
        )
    )
    op.add_column(
        "communication_settings",
        sa.Column("singleton", sa.String(length=1), nullable=False, server_default="X"),
    )
    op.create_unique_constraint(
        "uq_communication_settings_singleton", "communication_settings", ["singleton"]
    )
    op.create_check_constraint(
        "ck_communication_settings_singleton", "communication_settings", "singleton = 'X'"
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_communication_settings_singleton", "communication_settings", type_="check"
    )
    op.drop_constraint(
        "uq_communication_settings_singleton", "communication_settings", type_="unique"
    )
    op.drop_column("communication_settings", "singleton")
