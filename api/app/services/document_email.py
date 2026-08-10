"""Transactional document email: durable intent, n8n transport.

The CRM never contacts an email provider. It commits an EmailDelivery row
(the full snapshot of what should be sent), n8n claims work with a lease,
sends through the installation's one verified sender, and reports back.

State rules:
- `submitted` = the provider accepted the message. NOT delivery.
- `delivered` only ever comes from a trusted provider callback.
- Once submission may have begun, an ambiguous outcome is `unknown` and is
  never retried automatically.
- Failed/unknown deliveries surface as attention items.

The From ADDRESS is deployment configuration (DOCUMENT_EMAIL_FROM_ADDRESS).
Neither the API nor the workflow accepts an override; the owner configures
only the display name, Reply-To and templates.
"""

import re
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    CommercialDocument,
    CommercialDocumentVersion,
    CommunicationSettings,
    EmailDelivery,
    Job,
    Lead,
    User,
    utcnow,
)
from app.security.tokens import digest_token
from app.services.commercial import format_minor
from app.services.leads import add_activity

# Bounded attachment policy: one PDF, and only when it fits.
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024

# A claim older than this was abandoned by a crash before submission began
# and may be recovered to pending.
CLAIM_LEASE_MINUTES = 10

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TEMPLATE_VARIABLES = (
    "customer_name",
    "business_name",
    "job_number",
    "document_type",
    "document_number",
    "document_total",
    "due_date",
    "secure_document_link",
    "reply_to",
)
_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class EmailError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def validate_template(template: str) -> str:
    unknown = [
        name for name in _VARIABLE_PATTERN.findall(template) if name not in TEMPLATE_VARIABLES
    ]
    if unknown:
        raise EmailError(f"Unknown template variables: {', '.join(sorted(set(unknown)))}")
    return template


def render_template(template: str, values: dict[str, str]) -> str:
    """Plain substitution from the allowlist. No expression evaluation, and
    customer values are inserted as text — never interpreted."""
    validate_template(template)

    def replace(match: re.Match[str]) -> str:
        return values.get(match.group(1), "")

    return _VARIABLE_PATTERN.sub(replace, template)


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def sender_configured(settings: Settings) -> bool:
    return bool(settings.document_email_from_address)


def create_delivery(
    db: Session,
    acting_user: User,
    settings: Settings,
    settings_row: CommunicationSettings,
    *,
    version: CommercialDocumentVersion,
    recipient: str,
    secure_link: str,
    capability_id: uuid.UUID | None,
    attach_pdf: bool | None = None,
    send_key: str,
) -> EmailDelivery:
    """Create the durable record for one intended send. Committed by the
    caller BEFORE n8n is told anything. Deduplicated by (version, recipient,
    purpose, explicit send key): a browser retry returns the existing row."""
    if not sender_configured(settings):
        raise EmailError(
            "Email sending is not configured: no verified sender address is set for this "
            "installation. Drafts and PDFs still work.",
            status_code=409,
        )
    recipient = recipient.strip().lower()
    if not _EMAIL_PATTERN.match(recipient) or len(recipient) > 320:
        raise EmailError("Enter a valid recipient email address.")

    document = db.get(CommercialDocument, version.document_id)
    assert document is not None
    if document.status == "voided":
        raise EmailError("A voided document cannot be emailed.", status_code=409)
    active = db.scalar(
        select(CommercialDocumentVersion).where(
            CommercialDocumentVersion.document_id == document.id,
            CommercialDocumentVersion.superseded_at.is_(None),
        )
    )
    if active is None or active.id != version.id:
        raise EmailError("Only the current version can be emailed.", status_code=409)

    job = db.get(Job, document.job_id)
    assert job is not None
    if job.archived_at is not None:
        raise EmailError("Restore this job before emailing its documents.", status_code=409)
    lead = db.get(Lead, job.lead_id)
    assert lead is not None

    purpose = document.kind
    digest = digest_token(
        f"document-email:{version.id}:{recipient}:{purpose}:{send_key}",
        settings.session_token_pepper,
    )
    existing = db.scalar(
        select(EmailDelivery).where(EmailDelivery.idempotency_key_digest == digest)
    )
    if existing is not None:
        return existing

    subject_template, body_template = {
        "quote": (settings_row.quote_email_subject, settings_row.quote_email_body),
        "invoice": (settings_row.invoice_email_subject, settings_row.invoice_email_body),
        "receipt": (settings_row.receipt_email_subject, settings_row.receipt_email_body),
    }[purpose]
    values = {
        "customer_name": lead.name or "there",
        "business_name": settings_row.business_name,
        "job_number": job.job_number,
        "document_type": purpose,
        "document_number": version.number,
        "document_total": format_minor(version.payload["totals"]["total_minor"], document.currency),
        "due_date": (document.due_at.strftime("%B %d, %Y") if document.due_at is not None else ""),
        "secure_document_link": secure_link,
        "reply_to": settings_row.email_reply_to,
    }
    subject = render_template(subject_template, values).strip()[:500]
    body_text = render_template(body_template, values)
    paragraphs = "".join(
        f"<p>{_html_escape(line)}</p>" for line in body_text.split("\n\n") if line.strip()
    )
    body_html = f"<!doctype html><html><body>{paragraphs}</body></html>"

    if attach_pdf is None:
        attach_pdf = settings_row.email_attach_pdf_default
    # Prefer the secure link when the PDF exceeds the bounded attachment size.
    if version.pdf_byte_size > MAX_ATTACHMENT_BYTES:
        attach_pdf = False

    delivery = EmailDelivery(
        job_id=job.id,
        purpose=purpose,
        version_id=version.id,
        recipient=recipient,
        from_name=settings_row.email_from_display_name or settings_row.business_name,
        from_address=settings.document_email_from_address,
        reply_to=settings_row.email_reply_to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attach_pdf=attach_pdf,
        capability_id=capability_id,
        idempotency_key_digest=digest,
        created_by=acting_user.id,
    )
    db.add(delivery)
    db.flush()
    add_activity(
        db,
        lead,
        "document_email",
        f"{purpose.capitalize()} {version.number} queued for email delivery.",
        acting_user=acting_user,
        meta={"delivery_id": str(delivery.id), "job_id": str(job.id)},
    )
    return delivery


def recover_stale_claims(db: Session) -> int:
    """Claims whose lease expired before submission began return to pending.
    Anything that may have reached a provider is left for report_result."""
    cutoff = utcnow() - timedelta(minutes=CLAIM_LEASE_MINUTES)
    stale = db.scalars(
        select(EmailDelivery)
        .where(EmailDelivery.status == "claimed", EmailDelivery.claimed_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    count = 0
    for delivery in stale:
        delivery.status = "pending"
        delivery.claimed_at = None
        count += 1
    if count:
        db.flush()
    return count


def claim_pending(db: Session, limit: int = 10) -> list[EmailDelivery]:
    """FOR UPDATE SKIP LOCKED claim: overlapping n8n runs divide the work."""
    rows = list(
        db.scalars(
            select(EmailDelivery)
            .where(EmailDelivery.status == "pending")
            .order_by(EmailDelivery.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    now = utcnow()
    for delivery in rows:
        delivery.status = "claimed"
        delivery.claimed_at = now
        delivery.attempts += 1
    if rows:
        db.flush()
    return rows


def report_result(
    db: Session,
    delivery: EmailDelivery,
    *,
    outcome: str,
    provider_message_id: str | None = None,
    failure_class: str | None = None,
    failure_message: str | None = None,
) -> EmailDelivery:
    """Record what n8n observed. Terminal states never regress; `delivered`
    is only accepted on top of submitted (trusted callback path)."""
    now = utcnow()
    if outcome == "submitted":
        if delivery.status not in ("claimed", "submitted"):
            raise EmailError("This delivery is not awaiting submission.", status_code=409)
        delivery.status = "submitted"
        delivery.submitted_at = delivery.submitted_at or now
        delivery.provider_message_id = (provider_message_id or "")[:200] or None
    elif outcome == "delivered":
        if delivery.status not in ("submitted", "delivered"):
            raise EmailError("Delivery confirmations require a prior submission.", status_code=409)
        delivery.status = "delivered"
        delivery.delivered_at = delivery.delivered_at or now
        if provider_message_id:
            delivery.provider_message_id = provider_message_id[:200]
    elif outcome == "failed":
        if delivery.status in ("delivered",):
            raise EmailError("A delivered message cannot fail.", status_code=409)
        delivery.status = "failed"
        delivery.failed_at = now
        delivery.failure_class = (failure_class or "provider_error")[:32]
        delivery.failure_message = (failure_message or "")[:500] or None
    elif outcome == "unknown":
        if delivery.status in ("delivered",):
            raise EmailError("A delivered message cannot become unknown.", status_code=409)
        delivery.status = "unknown"
        delivery.failure_class = (failure_class or "ambiguous")[:32]
        delivery.failure_message = (failure_message or "")[:500] or None
    else:
        raise EmailError("Unknown outcome.")
    db.flush()

    if delivery.status in ("failed", "unknown"):
        job = db.get(Job, delivery.job_id)
        if job is not None:
            lead = db.get(Lead, job.lead_id)
            if lead is not None:
                add_activity(
                    db,
                    lead,
                    "document_email",
                    f"Email for {delivery.purpose} on job {job.job_number} is "
                    f"{delivery.status}: {delivery.failure_message or 'no detail'}.",
                    meta={"delivery_id": str(delivery.id), "job_id": str(job.id)},
                )
    return delivery


def attention_deliveries(db: Session, limit: int = 100) -> list[EmailDelivery]:
    """Failed/unknown sends needing a person, newest first."""
    return list(
        db.scalars(
            select(EmailDelivery)
            .where(EmailDelivery.status.in_(("failed", "unknown")))
            .order_by(EmailDelivery.updated_at.desc())
            .limit(limit)
        )
    )
