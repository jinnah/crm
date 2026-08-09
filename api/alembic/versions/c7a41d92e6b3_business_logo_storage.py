"""business logo storage

Revision ID: c7a41d92e6b3
Revises: ea53c413405b
Create Date: 2026-08-09

Adds durable storage for the business logo on the single settings row. The
bytes are the server's own re-encoded image, never what was uploaded.
"""

import sqlalchemy as sa

from alembic import op

revision = "c7a41d92e6b3"
down_revision = "ea53c413405b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "communication_settings", sa.Column("logo_bytes", sa.LargeBinary(), nullable=True)
    )
    op.add_column(
        "communication_settings", sa.Column("logo_mime", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "communication_settings", sa.Column("logo_digest", sa.String(length=64), nullable=True)
    )
    op.add_column("communication_settings", sa.Column("logo_width", sa.Integer(), nullable=True))
    op.add_column("communication_settings", sa.Column("logo_height", sa.Integer(), nullable=True))
    op.add_column(
        "communication_settings",
        sa.Column("logo_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Refuse to drop a logo that exists.

    Dropping these columns destroys the only copy of the image — it cannot be
    reconstructed from anything else in the database. Following the same
    policy the scheduling and settings migrations use, this stops before the
    destructive change and tells the operator what to do, rather than
    silently discarding the business's branding.
    """
    connection = op.get_bind()
    stored = connection.execute(
        sa.text("SELECT count(*) FROM communication_settings WHERE logo_bytes IS NOT NULL")
    ).scalar_one()
    if stored:
        raise RuntimeError(
            "A business logo is stored in communication_settings and downgrading would "
            "delete it permanently. Remove the logo first (Settings -> Branding -> Remove, "
            "or UPDATE communication_settings SET logo_bytes = NULL), keeping a copy of the "
            "image if you still want it, then run the downgrade again."
        )

    op.drop_column("communication_settings", "logo_updated_at")
    op.drop_column("communication_settings", "logo_height")
    op.drop_column("communication_settings", "logo_width")
    op.drop_column("communication_settings", "logo_digest")
    op.drop_column("communication_settings", "logo_mime")
    op.drop_column("communication_settings", "logo_bytes")
