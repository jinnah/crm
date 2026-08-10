"""Jobs, secure documents, commercial records, payments and document email.

Revision ID: a7c9e2d41f68
Revises: d94f7ae1c2b5
Create Date: 2026-08-09

Downgrading would destroy jobs, uploaded-document metadata, issued commercial
documents, payments, receipts and email history — irreplaceable records. The
downgrade therefore refuses BEFORE any mutation whenever populated rows
exist, with operator instructions. Object-store binaries are additionally
covered by the coordinated backup procedure documented in the README.
"""

import sqlalchemy as sa

from alembic import op

revision = "a7c9e2d41f68"
down_revision = "d94f7ae1c2b5"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "number_sequences",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "year", name="uq_number_sequences"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("job_number", sa.String(24), nullable=False, unique=True),
        sa.Column(
            "lead_id",
            _uuid(),
            sa.ForeignKey("leads.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("service_type", sa.String(200), nullable=False, server_default=""),
        sa.Column("service_address", sa.String(300), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column(
            "assigned_to",
            _uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("internal_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('new', 'quoted', 'approved', 'scheduled', 'in_progress', "
            "'completed', 'canceled')",
            name="ck_jobs_status",
        ),
    )

    # Existing appointments stay unlinked until a staff member assigns them.
    op.add_column(
        "appointments",
        sa.Column("job_id", _uuid(), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True),
    )
    op.create_index("ix_appointments_job_id", "appointments", ["job_id"])

    op.create_table(
        "job_documents",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "job_id",
            _uuid(),
            sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("category", sa.String(16), nullable=False, server_default="other"),
        sa.Column("description", sa.String(1000), nullable=False, server_default=""),
        sa.Column("original_filename", sa.String(200), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(300), nullable=True),
        sa.Column("quarantine_key", sa.String(300), nullable=True),
        sa.Column("preview_storage_key", sa.String(300), nullable=True),
        sa.Column("scan_state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("scan_detail", sa.String(300), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "uploaded_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "deleted_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("delete_reason", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('receipt', 'quote', 'invoice', 'contract', 'permit', "
            "'warranty', 'photo', 'other')",
            name="ck_job_documents_category",
        ),
        sa.CheckConstraint(
            "scan_state IN ('pending', 'clean', 'infected', 'failed')",
            name="ck_job_documents_scan_state",
        ),
    )

    op.create_table(
        "commercial_documents",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column(
            "job_id",
            _uuid(),
            sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("number", sa.String(24), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("discount_bp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subtotal_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discount_total_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tax_total_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("customer_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("terms", sa.Text(), nullable=False, server_default=""),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_name", sa.String(200), nullable=True),
        sa.Column("response_snapshot_sha256", sa.String(64), nullable=True),
        sa.Column(
            "source_quote_id",
            _uuid(),
            sa.ForeignKey("commercial_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_quote_version", sa.Integer(), nullable=True),
        sa.Column(
            "converted_invoice_id",
            _uuid(),
            sa.ForeignKey("commercial_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payment_id", _uuid(), nullable=True),
        sa.Column("amount_paid_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "voided_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("void_reason", sa.String(300), nullable=True),
        sa.Column(
            "created_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "number", name="uq_commercial_documents_number"),
        sa.CheckConstraint(
            "kind IN ('quote', 'invoice', 'receipt')", name="ck_commercial_documents_kind"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'sent', 'viewed', 'accepted', 'declined', 'expired', "
            "'voided', 'partially_paid', 'paid', 'overdue', 'issued')",
            name="ck_commercial_documents_status",
        ),
        sa.CheckConstraint("subtotal_minor >= 0", name="ck_commercial_documents_subtotal"),
        sa.CheckConstraint("total_minor >= 0", name="ck_commercial_documents_total"),
    )

    op.create_table(
        "payments",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "invoice_id",
            _uuid(),
            sa.ForeignKey("commercial_documents.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("paid_on", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False, server_default=""),
        sa.Column("internal_note", sa.String(500), nullable=False, server_default=""),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "recorded_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "receipt_document_id",
            _uuid(),
            sa.ForeignKey("commercial_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "voided_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("void_reason", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_payments_amount"),
        sa.CheckConstraint(
            "method IN ('cash', 'check', 'bank_transfer', 'card_external', 'other')",
            name="ck_payments_method",
        ),
    )

    # The receipts → payments reference completes the cycle, added after both
    # tables exist.
    op.create_foreign_key(
        "fk_commercial_documents_payment_id",
        "commercial_documents",
        "payments",
        ["payment_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "commercial_line_items",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "document_id",
            _uuid(),
            sa.ForeignKey("commercial_documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("quantity_milli", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("unit", sa.String(20), nullable=False, server_default=""),
        sa.Column("unit_price_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discount_bp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tax_rate_bp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("line_total_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity_milli > 0", name="ck_commercial_line_items_quantity"),
        sa.CheckConstraint("unit_price_minor >= 0", name="ck_commercial_line_items_unit_price"),
        sa.CheckConstraint(
            "discount_bp >= 0 AND discount_bp <= 10000", name="ck_commercial_line_items_discount"
        ),
        sa.CheckConstraint(
            "tax_rate_bp >= 0 AND tax_rate_bp <= 5000", name="ck_commercial_line_items_tax"
        ),
    )

    op.create_table(
        "commercial_document_versions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "document_id",
            _uuid(),
            sa.ForeignKey("commercial_documents.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("pdf_storage_key", sa.String(300), nullable=False),
        sa.Column("pdf_sha256", sa.String(64), nullable=False),
        sa.Column("pdf_byte_size", sa.Integer(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "version", name="uq_commercial_document_versions"),
    )

    op.create_table(
        "document_capabilities",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "version_id",
            _uuid(),
            sa.ForeignKey("commercial_document_versions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("purpose", sa.String(16), nullable=False, server_default="view"),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("first_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('view', 'quote_response')", name="ck_document_capabilities_purpose"
        ),
    )

    op.create_table(
        "email_deliveries",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "job_id",
            _uuid(),
            sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column(
            "version_id",
            _uuid(),
            sa.ForeignKey("commercial_document_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "job_document_id",
            _uuid(),
            sa.ForeignKey("job_documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("from_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("from_address", sa.String(320), nullable=False),
        sa.Column("reply_to", sa.String(320), nullable=False, server_default=""),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("attach_pdf", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "capability_id",
            _uuid(),
            sa.ForeignKey("document_capabilities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("template_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("provider_message_id", sa.String(200), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_class", sa.String(32), nullable=True),
        sa.Column("failure_message", sa.String(500), nullable=True),
        sa.Column(
            "created_by", _uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('quote', 'invoice', 'receipt', 'job_document')",
            name="ck_email_deliveries_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'submitted', 'delivered', 'failed', "
            "'unknown', 'suppressed')",
            name="ck_email_deliveries_status",
        ),
    )

    # --- Documents & email settings columns -----------------------------
    _add_settings_columns()


def _add_settings_columns() -> None:
    defaults: list[tuple[str, sa.types.TypeEngine, str | None]] = [
        ("default_currency", sa.String(3), "USD"),
        ("quote_number_prefix", sa.String(8), "Q"),
        ("invoice_number_prefix", sa.String(8), "INV"),
        ("receipt_number_prefix", sa.String(8), "R"),
        ("business_email", sa.String(320), ""),
        ("business_phone", sa.String(32), ""),
        ("business_address", sa.String(500), ""),
        ("business_registration_id", sa.String(100), ""),
        ("email_from_display_name", sa.String(200), ""),
        ("email_reply_to", sa.String(320), ""),
    ]
    for name, type_, default in defaults:
        op.add_column(
            "communication_settings",
            sa.Column(name, type_, nullable=False, server_default=default),
        )
    for name, default in (
        ("default_quote_valid_days", "30"),
        ("default_invoice_due_days", "14"),
        ("default_tax_rate_bp", "0"),
        ("secure_link_expiry_days", "30"),
    ):
        op.add_column(
            "communication_settings",
            sa.Column(name, sa.Integer(), nullable=False, server_default=default),
        )
    op.add_column(
        "communication_settings",
        sa.Column(
            "email_attach_pdf_default", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    templates = {
        "quote_email_subject": "Your quote {{document_number}} from {{business_name}}",
        "quote_email_body": (
            "Hi {{customer_name}},\n\nYour quote {{document_number}} for job {{job_number}} "
            "is ready: {{document_total}}.\n\nView and respond here: {{secure_document_link}}"
            "\n\n{{business_name}}"
        ),
        "invoice_email_subject": "Invoice {{document_number}} from {{business_name}}",
        "invoice_email_body": (
            "Hi {{customer_name}},\n\nInvoice {{document_number}} for job {{job_number}} "
            "is due {{due_date}}: {{document_total}}.\n\nView it here: "
            "{{secure_document_link}}\n\n{{business_name}}"
        ),
        "receipt_email_subject": "Receipt {{document_number}} from {{business_name}}",
        "receipt_email_body": (
            "Hi {{customer_name}},\n\nThank you. Your receipt {{document_number}} for "
            "{{document_total}} is here: {{secure_document_link}}\n\n{{business_name}}"
        ),
    }
    for name, default in templates.items():
        column_type = sa.String(300) if name.endswith("subject") else sa.Text()
        op.add_column(
            "communication_settings",
            sa.Column(name, column_type, nullable=False, server_default=default),
        )


_SETTINGS_COLUMNS = [
    "default_currency",
    "quote_number_prefix",
    "invoice_number_prefix",
    "receipt_number_prefix",
    "default_quote_valid_days",
    "default_invoice_due_days",
    "default_tax_rate_bp",
    "business_email",
    "business_phone",
    "business_address",
    "business_registration_id",
    "email_from_display_name",
    "email_reply_to",
    "quote_email_subject",
    "quote_email_body",
    "invoice_email_subject",
    "invoice_email_body",
    "receipt_email_subject",
    "receipt_email_body",
    "secure_link_expiry_days",
    "email_attach_pdf_default",
]


def downgrade() -> None:
    """Refuse to destroy irreplaceable records; stop BEFORE mutating."""
    connection = op.get_bind()

    checks = [
        ("jobs", "job record(s)"),
        ("job_documents", "uploaded-document record(s)"),
        ("commercial_documents", "commercial document(s)"),
        ("payments", "payment record(s)"),
        ("email_deliveries", "email-delivery record(s)"),
    ]
    for table, label in checks:
        count = connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                f"{count} {label} exist and downgrading would delete this history "
                "permanently. Export or archive the data (database AND the document "
                "object store together), remove the rows deliberately, then run the "
                "downgrade again."
            )
    linked = connection.execute(
        sa.text("SELECT count(*) FROM appointments WHERE job_id IS NOT NULL")
    ).scalar_one()
    if linked:
        raise RuntimeError(
            f"{linked} appointment(s) are linked to jobs. Unlink them deliberately "
            "before downgrading."
        )

    op.drop_table("email_deliveries")
    op.drop_table("document_capabilities")
    op.drop_table("commercial_document_versions")
    op.drop_table("commercial_line_items")
    op.drop_constraint(
        "fk_commercial_documents_payment_id", "commercial_documents", type_="foreignkey"
    )
    op.drop_table("payments")
    op.drop_table("commercial_documents")
    op.drop_table("job_documents")
    op.drop_index("ix_appointments_job_id", table_name="appointments")
    op.drop_column("appointments", "job_id")
    op.drop_table("jobs")
    op.drop_table("number_sequences")
    for name in _SETTINGS_COLUMNS:
        op.drop_column("communication_settings", name)
