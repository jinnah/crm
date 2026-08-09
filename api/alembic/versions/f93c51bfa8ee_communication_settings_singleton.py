"""communication settings singleton

Guarantees exactly one logical communication-settings row.

Deduplication is configuration-aware, never "keep the oldest": identical rows
collapse to one, and when only a single row carries configured (non-default)
values that row is the survivor regardless of its age. If two rows hold
conflicting configuration the migration aborts with an actionable message
rather than silently discarding someone's settings.

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

# Fields that carry an operator's configuration. Compared verbatim to decide
# whether a row is untouched, identical to another, or genuinely conflicting.
CONFIG_COLUMNS = (
    "business_name",
    "form_title",
    "form_intro",
    "acknowledgment_enabled",
    "acknowledgment_template",
    "alert_enabled",
    "alert_template",
    "alert_destination_phone",
    "response_target_minutes",
)

# The model defaults as of b38622ca11e1. A row matching all of these has never
# been configured and is safe to discard in favour of a configured sibling.
DEFAULTS = {
    "business_name": "Our team",
    "form_title": "Request a quote",
    "form_intro": "",
    "acknowledgment_enabled": False,
    "acknowledgment_template": (
        "Hi {{lead_name}}, thanks for contacting {{business_name}}. We'll reply shortly."
    ),
    "alert_enabled": False,
    "alert_template": "New {{source}} lead: {{lead_name}} (CRM ref {{lead_id}}).",
    "alert_destination_phone": None,
    "response_target_minutes": 5,
}


def _config_of(row: sa.Row) -> tuple:
    return tuple(getattr(row, column) for column in CONFIG_COLUMNS)


def _is_default(row: sa.Row) -> bool:
    return all(getattr(row, column) == DEFAULTS[column] for column in CONFIG_COLUMNS)


def _resolve_survivor(rows: list[sa.Row]) -> sa.Row:
    """Pick the row that must survive, or refuse to guess."""
    configured = [row for row in rows if not _is_default(row)]
    if not configured:
        # Every row is untouched: any of them is equivalent.
        return rows[0]

    distinct = {_config_of(row) for row in configured}
    if len(distinct) == 1:
        # One configuration, possibly duplicated verbatim: keep one copy.
        return configured[0]

    raise RuntimeError(
        "Cannot migrate communication_settings to a singleton: "
        f"{len(distinct)} rows hold different configured values "
        f"(ids: {', '.join(str(row.id) for row in configured)}). "
        "No row has been deleted. Review these rows, keep the correct one, "
        "delete the rest, then re-run `alembic upgrade head`."
    )


def upgrade() -> None:
    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                "SELECT id, created_at, " + ", ".join(CONFIG_COLUMNS) + " "
                "FROM communication_settings ORDER BY created_at, id"
            )
        )
    )

    if len(rows) > 1:
        survivor = _resolve_survivor(rows)  # raises before any delete on conflict
        connection.execute(
            sa.text("DELETE FROM communication_settings WHERE id <> :keep"),
            {"keep": survivor.id},
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
