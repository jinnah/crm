"""Quotes, invoices, manual payments and receipts.

Money rules (documented, tested):

- All amounts are integer minor units (cents) in the document currency —
  binary floating point never touches money.
- Quantities are integer thousandths (2.5 hours = 2500).
- Rounding is ROUND_HALF_UP, applied at each step below:
    line_net   = round(quantity × unit_price × (1 − line_discount))
    subtotal   = Σ line_net
    discount   = round(subtotal × document_discount)
    tax        = Σ round(line_net × (1 − document_discount) × line_tax_rate)
    total      = subtotal − discount + tax
- Discounts are basis points 0–10000; tax rates 0–5000 (50%).
- The server recalculates every total; browser-supplied totals are ignored.

Issuing snapshots a draft into an immutable CommercialDocumentVersion with
the exact generated PDF and assigns the final number under the concurrency-
safe sequence. Sent versions are never edited: corrections create a new
version (quotes) or void-and-reissue (invoices). Nothing here is deleted.

Payments are recorded against invoices under the invoice row lock, so
concurrent payments cannot exceed the remaining balance; posted payments are
corrected only by audited reversal. Receipts are generated documents issued
from durably recorded payments, and replays return the same payment+receipt.
"""

import hashlib
import json
import re
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    INVOICE_STATUSES,
    QUOTE_STATUSES,
    CommercialDocument,
    CommercialDocumentVersion,
    CommercialLineItem,
    CommunicationSettings,
    Job,
    Lead,
    Payment,
    User,
    utcnow,
)
from app.security.tokens import digest_token
from app.services import jobs as job_service
from app.services.leads import add_activity
from app.services.numbering import allocate_number
from app.services.storage import COMMERCIAL_PREFIX, new_object_key

MAX_LINE_ITEMS = 100
MAX_QUANTITY_MILLI = 1_000_000_000  # one million units
MAX_UNIT_PRICE_MINOR = 1_000_000_000  # ten million in major units

_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")

# Likely card-number (PAN) and CVV content is refused in every free-text
# payment field. 13-19 digits, spaces/dashes allowed, Luhn-checked.
_PAN_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_CVV_HINT = re.compile(r"\b(?:cvv|cvc|cvv2|security\s*code)\b", re.IGNORECASE)


class CommercialError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --- money ---------------------------------------------------------------


def _round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def line_net_minor(quantity_milli: int, unit_price_minor: int, discount_bp: int) -> int:
    gross = (
        Decimal(quantity_milli)
        * Decimal(unit_price_minor)
        * (Decimal(10000 - discount_bp))
        / (Decimal(1000) * Decimal(10000))
    )
    return _round_half_up(gross)


def compute_totals(
    lines: list[CommercialLineItem], document_discount_bp: int
) -> tuple[int, int, int, int]:
    """Returns (subtotal, discount_total, tax_total, total) in minor units."""
    subtotal = 0
    tax_total = Decimal(0)
    doc_keep = Decimal(10000 - document_discount_bp) / Decimal(10000)
    for line in lines:
        net = line_net_minor(line.quantity_milli, line.unit_price_minor, line.discount_bp)
        line.line_total_minor = net
        subtotal += net
        if line.tax_rate_bp:
            tax_total += Decimal(
                _round_half_up(Decimal(net) * doc_keep * Decimal(line.tax_rate_bp) / Decimal(10000))
            )
    discount_total = _round_half_up(
        Decimal(subtotal) * Decimal(document_discount_bp) / Decimal(10000)
    )
    total = subtotal - discount_total + int(tax_total)
    if total < 0:  # pragma: no cover - excluded by field bounds, kept as a guard
        raise CommercialError("Totals cannot be negative.")
    return subtotal, discount_total, int(tax_total), total


def format_minor(amount_minor: int, currency: str) -> str:
    major, minor = divmod(abs(amount_minor), 100)
    sign = "-" if amount_minor < 0 else ""
    return f"{sign}{major}.{minor:02d} {currency}"


def reject_payment_credentials(value: str, field_name: str) -> str:
    """Refuse likely PAN/CVV/banking-credential content in free text."""
    if _CVV_HINT.search(value):
        raise CommercialError(
            f"The {field_name} may not contain card security codes. "
            "Never record card or banking credentials in the CRM."
        )
    for match in _PAN_CANDIDATE.finditer(value):
        digits = re.sub(r"[ -]", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            raise CommercialError(
                f"The {field_name} appears to contain a card number. "
                "Never record card or banking credentials in the CRM."
            )
    return value


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


# --- draft management ----------------------------------------------------


def lock_document(db: Session, document_id: uuid.UUID) -> CommercialDocument:
    # populate_existing: a session that read this row BEFORE locking must see
    # the state committed by the lock's previous holder, not its own cache —
    # otherwise an idempotency check under the lock reads stale attributes.
    document = db.scalar(
        select(CommercialDocument)
        .where(CommercialDocument.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if document is None:
        raise CommercialError("Document not found.", status_code=404)
    return document


def create_draft(
    db: Session,
    acting_user: User,
    job: Job,
    settings_row: CommunicationSettings,
    *,
    kind: str,
) -> CommercialDocument:
    if kind not in ("quote", "invoice"):
        raise CommercialError("Only quotes and invoices are drafted directly.")
    if job.archived_at is not None:
        raise CommercialError("Restore this job first.", status_code=409)
    currency = settings_row.default_currency
    if not _CURRENCY_PATTERN.match(currency):
        raise CommercialError("The default currency is not configured correctly.")
    document = CommercialDocument(
        kind=kind,
        job_id=job.id,
        currency=currency,
        terms=settings_row.business_name and "" or "",
        created_by=acting_user.id,
    )
    if kind == "quote":
        document.valid_until = utcnow() + timedelta(days=settings_row.default_quote_valid_days)
    db.add(document)
    db.flush()
    return document


def replace_lines(
    db: Session,
    document: CommercialDocument,
    lines: list[dict],
    *,
    discount_bp: int,
    customer_notes: str,
    terms: str,
    valid_until=None,
    due_at=None,
) -> CommercialDocument:
    """Replace the working draft's lines and metadata wholesale (stable order
    = list order).

    Issued snapshots are immutable version rows; these columns are the
    WORKING copy. A sent quote may keep being edited — re-issuing then
    creates a new version and supersedes the old one without touching what
    the customer received. Issued invoices are corrected by void-and-reissue
    only, and responded/voided quotes are closed.
    """
    editable = ("draft", "sent", "viewed") if document.kind == "quote" else ("draft",)
    if document.status not in editable:
        raise CommercialError(
            "This document can no longer be edited; correct it with a new document "
            "or void-and-reissue.",
            status_code=409,
        )
    if not 0 <= discount_bp <= 10000:
        raise CommercialError("The document discount must be between 0% and 100%.")
    if len(lines) > MAX_LINE_ITEMS:
        raise CommercialError(f"Documents are limited to {MAX_LINE_ITEMS} lines.")
    for line in db.scalars(
        select(CommercialLineItem).where(CommercialLineItem.document_id == document.id)
    ):
        db.delete(line)
    db.flush()
    new_lines: list[CommercialLineItem] = []
    for position, data in enumerate(lines):
        quantity_milli = int(data.get("quantity_milli", 0))
        unit_price_minor = int(data.get("unit_price_minor", 0))
        line_discount = int(data.get("discount_bp", 0))
        tax_rate = int(data.get("tax_rate_bp", 0))
        description = str(data.get("description", "")).strip()
        if not description:
            raise CommercialError("Every line needs a description.")
        if not 0 < quantity_milli <= MAX_QUANTITY_MILLI:
            raise CommercialError("Line quantities must be positive.")
        if not 0 <= unit_price_minor <= MAX_UNIT_PRICE_MINOR:
            raise CommercialError("Line prices must be zero or positive.")
        if not 0 <= line_discount <= 10000:
            raise CommercialError("Line discounts must be between 0% and 100%.")
        if not 0 <= tax_rate <= 5000:
            raise CommercialError("Tax rates must be between 0% and 50%.")
        item = CommercialLineItem(
            document_id=document.id,
            position=position,
            description=description[:500],
            quantity_milli=quantity_milli,
            unit=str(data.get("unit", "")).strip()[:20],
            unit_price_minor=unit_price_minor,
            discount_bp=line_discount,
            tax_rate_bp=tax_rate,
        )
        db.add(item)
        new_lines.append(item)
    document.discount_bp = discount_bp
    document.customer_notes = customer_notes.strip()[:5000]
    document.terms = terms.strip()[:5000]
    if valid_until is not None:
        document.valid_until = valid_until
    if due_at is not None:
        document.due_at = due_at
    subtotal, discount_total, tax_total, total = compute_totals(new_lines, discount_bp)
    document.subtotal_minor = subtotal
    document.discount_total_minor = discount_total
    document.tax_total_minor = tax_total
    document.total_minor = total
    db.flush()
    return document


def document_lines(db: Session, document: CommercialDocument) -> list[CommercialLineItem]:
    return list(
        db.scalars(
            select(CommercialLineItem)
            .where(CommercialLineItem.document_id == document.id)
            .order_by(CommercialLineItem.position)
        )
    )


# --- issuing -------------------------------------------------------------


def _snapshot_payload(
    db: Session,
    document: CommercialDocument,
    lines: list[CommercialLineItem],
    settings_row: CommunicationSettings,
    settings: Settings,
) -> dict:
    job = db.get(Job, document.job_id)
    assert job is not None
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    return {
        "kind": document.kind,
        "number": document.number,
        "currency": document.currency,
        "business": {
            "name": settings_row.business_name,
            "email": settings_row.business_email,
            "phone": settings_row.business_phone,
            "address": settings_row.business_address,
            "registration_id": settings_row.business_registration_id,
        },
        "customer": {
            "name": lead.name or lead.email or lead.phone or "Customer",
            "email": lead.email,
            "phone": lead.phone,
        },
        "job": {
            "number": job.job_number,
            "title": job.title,
            "service_address": job.service_address,
        },
        "lines": [
            {
                "position": line.position,
                "description": line.description,
                "quantity_milli": line.quantity_milli,
                "unit": line.unit,
                "unit_price_minor": line.unit_price_minor,
                "discount_bp": line.discount_bp,
                "tax_rate_bp": line.tax_rate_bp,
                "line_total_minor": line.line_total_minor,
            }
            for line in lines
        ],
        "discount_bp": document.discount_bp,
        "totals": {
            "subtotal_minor": document.subtotal_minor,
            "discount_total_minor": document.discount_total_minor,
            "tax_total_minor": document.tax_total_minor,
            "total_minor": document.total_minor,
        },
        "customer_notes": document.customer_notes,
        "terms": document.terms,
        "valid_until": document.valid_until.isoformat() if document.valid_until else None,
        "issued_at": document.issued_at.isoformat() if document.issued_at else None,
        "due_at": document.due_at.isoformat() if document.due_at else None,
    }


def snapshot_sha256(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def issue(
    db: Session,
    acting_user: User,
    storage,
    document: CommercialDocument,
    settings_row: CommunicationSettings,
    settings: Settings,
) -> CommercialDocumentVersion:
    """Issue the current draft: final number, immutable snapshot, exact PDF."""
    from app.services.pdf import render_commercial_pdf

    document = lock_document(db, document.id)
    if document.kind not in ("quote", "invoice"):
        raise CommercialError("Receipts are issued from payments.")
    issuable = ("draft", "sent", "viewed") if document.kind == "quote" else ("draft",)
    if document.status not in issuable:
        raise CommercialError(
            "This document cannot be issued in its current state.", status_code=409
        )
    lines = document_lines(db, document)
    if not lines:
        raise CommercialError("Add at least one line before issuing.")

    now = utcnow()
    if document.number is None:
        prefix = (
            settings_row.quote_number_prefix
            if document.kind == "quote"
            else settings_row.invoice_number_prefix
        )
        document.number = allocate_number(db, document.kind, prefix)
    document.issued_at = document.issued_at or now
    if document.kind == "invoice" and document.due_at is None:
        document.due_at = now + timedelta(days=settings_row.default_invoice_due_days)
    if document.kind == "quote" and document.valid_until is None:
        document.valid_until = now + timedelta(days=settings_row.default_quote_valid_days)

    # Supersede the previous active version (its snapshot stays untouched).
    previous = db.scalar(
        select(CommercialDocumentVersion).where(
            CommercialDocumentVersion.document_id == document.id,
            CommercialDocumentVersion.superseded_at.is_(None),
        )
    )
    if previous is not None:
        previous.superseded_at = now

    version_number = document.current_version + 1
    payload = _snapshot_payload(db, document, lines, settings_row, settings)
    pdf_bytes = render_commercial_pdf(payload, settings_row)
    pdf_key = new_object_key(COMMERCIAL_PREFIX, ".pdf")
    storage.put_bytes(pdf_key, pdf_bytes)

    version = CommercialDocumentVersion(
        document_id=document.id,
        version=version_number,
        number=document.number,
        payload=payload,
        pdf_storage_key=pdf_key,
        pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        pdf_byte_size=len(pdf_bytes),
        created_by=acting_user.id,
    )
    db.add(version)
    document.current_version = version_number
    document.status = "sent" if document.kind in ("quote", "invoice") else "issued"
    db.flush()

    job = db.get(Job, document.job_id)
    assert job is not None
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    add_activity(
        db,
        lead,
        "commercial_issued",
        f"{document.kind.capitalize()} {document.number} v{version_number} issued for "
        f"job {job.job_number}: {format_minor(document.total_minor, document.currency)}.",
        acting_user=acting_user,
        meta={
            "document_id": str(document.id),
            "version": version_number,
            "job_id": str(job.id),
        },
    )
    if document.kind == "quote":
        job_service.auto_advance(db, job, "quoted")
    return version


def active_version(db: Session, document: CommercialDocument) -> CommercialDocumentVersion | None:
    return db.scalar(
        select(CommercialDocumentVersion).where(
            CommercialDocumentVersion.document_id == document.id,
            CommercialDocumentVersion.superseded_at.is_(None),
        )
    )


def void_document(
    db: Session, acting_user: User, document: CommercialDocument, reason: str
) -> CommercialDocument:
    document = lock_document(db, document.id)
    if document.status == "voided":
        return document
    if document.kind == "invoice" and document.amount_paid_minor > 0:
        raise CommercialError(
            "This invoice has recorded payments; reverse them before voiding.", status_code=409
        )
    document.status = "voided"
    document.voided_at = utcnow()
    document.voided_by = acting_user.id
    document.void_reason = reason.strip()[:300] or "voided"
    db.flush()
    job = db.get(Job, document.job_id)
    assert job is not None
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    add_activity(
        db,
        lead,
        "commercial_voided",
        f"{document.kind.capitalize()} {document.number or '(draft)'} voided.",
        acting_user=acting_user,
        meta={"document_id": str(document.id), "job_id": str(job.id)},
    )
    return document


# --- quote response ------------------------------------------------------


def respond_to_quote(
    db: Session,
    document_id: uuid.UUID,
    version: CommercialDocumentVersion,
    *,
    accept: bool,
    typed_name: str,
) -> CommercialDocument:
    """Idempotent, deterministic customer response on the active sent snapshot.

    The row lock makes concurrent accept-versus-decline serialize: the first
    committed response wins, an identical retry replays it, a conflicting
    retry gets 409.
    """
    document = lock_document(db, document_id)
    if document.kind != "quote":
        raise CommercialError("Only quotes accept responses.", status_code=409)
    name = typed_name.strip()[:200]
    if not name:
        raise CommercialError("Please type your name to respond.")
    current = active_version(db, document)
    if current is None or current.id != version.id:
        raise CommercialError("This quote has been superseded.", status_code=409)
    if document.status == "voided":
        raise CommercialError("This quote is no longer available.", status_code=410)
    if document.valid_until is not None and document.valid_until <= utcnow():
        if document.status not in ("accepted", "declined"):
            document.status = "expired"
            db.flush()
        raise CommercialError("This quote has expired.", status_code=410)
    wanted = "accepted" if accept else "declined"
    if document.responded_at is not None:
        if document.status == wanted and document.response_name == name:
            return document  # identical retry replays the stored outcome
        raise CommercialError("This quote already has a recorded response.", status_code=409)
    document.status = wanted
    document.responded_at = utcnow()
    document.response_name = name
    document.response_snapshot_sha256 = snapshot_sha256(version.payload)
    db.flush()
    job = db.get(Job, document.job_id)
    assert job is not None
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    add_activity(
        db,
        lead,
        "quote_response",
        f"Quote {document.number} {wanted} by {name}.",
        meta={"document_id": str(document.id), "job_id": str(job.id), "response": wanted},
    )
    if accept:
        job_service.auto_advance(db, job, "approved")
    return document


def convert_quote_to_invoice(
    db: Session,
    acting_user: User,
    storage,
    quote: CommercialDocument,
    settings_row: CommunicationSettings,
    settings: Settings,
) -> CommercialDocument:
    """One-action, idempotent conversion of an accepted quote."""
    quote = lock_document(db, quote.id)
    if quote.kind != "quote":
        raise CommercialError("Only quotes convert to invoices.")
    if quote.status != "accepted":
        raise CommercialError("Only accepted quotes can become invoices.", status_code=409)
    if quote.converted_invoice_id is not None:
        existing = db.get(CommercialDocument, quote.converted_invoice_id)
        if existing is not None:
            return existing  # retry returns the one invoice
    version = active_version(db, quote)
    assert version is not None
    job = db.get(Job, quote.job_id)
    assert job is not None

    invoice = CommercialDocument(
        kind="invoice",
        job_id=quote.job_id,
        currency=quote.currency,
        discount_bp=quote.discount_bp,
        customer_notes=quote.customer_notes,
        terms=quote.terms,
        source_quote_id=quote.id,
        source_quote_version=version.version,
        created_by=acting_user.id,
    )
    db.add(invoice)
    db.flush()
    lines = [
        {
            "description": line["description"],
            "quantity_milli": line["quantity_milli"],
            "unit": line["unit"],
            "unit_price_minor": line["unit_price_minor"],
            "discount_bp": line["discount_bp"],
            "tax_rate_bp": line["tax_rate_bp"],
        }
        for line in version.payload["lines"]
    ]
    replace_lines(
        db,
        invoice,
        lines,
        discount_bp=quote.discount_bp,
        customer_notes=quote.customer_notes,
        terms=quote.terms,
    )
    quote.converted_invoice_id = invoice.id
    db.flush()
    return invoice


# --- invoice state -------------------------------------------------------


def refresh_invoice_status(
    db: Session, invoice: CommercialDocument, settings_row: CommunicationSettings
) -> str:
    """Due/overdue is a business-timezone judgement; voided is terminal."""
    if invoice.kind != "invoice" or invoice.status in ("draft", "voided"):
        return invoice.status
    if invoice.amount_paid_minor >= invoice.total_minor and invoice.total_minor > 0:
        invoice.status = "paid"
    elif invoice.amount_paid_minor > 0:
        invoice.status = "partially_paid"
    else:
        # A reversal can bring the balance back to zero: fall back to sent
        # before the due-date judgement below (viewed state is re-earned).
        if invoice.status in ("partially_paid", "paid"):
            invoice.status = "sent"
        _apply_overdue(invoice, settings_row)
    db.flush()
    return invoice.status


def _apply_overdue(invoice: CommercialDocument, settings_row: CommunicationSettings) -> None:
    if invoice.due_at is None:
        return
    try:
        zone = ZoneInfo(settings_row.business_timezone)
    except (KeyError, ValueError):  # pragma: no cover - validated on save
        zone = ZoneInfo("UTC")
    today = utcnow().astimezone(zone).date()
    due_day = invoice.due_at.astimezone(zone).date()
    if today > due_day and invoice.status in ("sent", "viewed", "overdue"):
        invoice.status = "overdue"


# --- manual payments and receipts ---------------------------------------


def record_payment(
    db: Session,
    acting_user: User,
    storage,
    invoice_id: uuid.UUID,
    settings_row: CommunicationSettings,
    settings: Settings,
    *,
    amount_minor: int,
    currency: str,
    method: str,
    paid_on,
    reference: str = "",
    internal_note: str = "",
    idempotency_key: str,
) -> tuple[Payment, CommercialDocument]:
    """Record an externally completed payment; returns (payment, receipt).

    The invoice row lock serializes concurrent recordings, so the balance
    check holds under contention. A replay (same idempotency key) returns
    the original payment and receipt without touching anything.
    """
    digest = digest_token(f"payment:{invoice_id}:{idempotency_key}", settings.session_token_pepper)
    existing = db.scalar(select(Payment).where(Payment.idempotency_key_digest == digest))
    if existing is not None:
        receipt = (
            db.get(CommercialDocument, existing.receipt_document_id)
            if existing.receipt_document_id
            else None
        )
        assert receipt is not None
        return existing, receipt

    invoice = lock_document(db, invoice_id)
    # Re-check under the lock: a concurrent identical request may have won.
    existing = db.scalar(select(Payment).where(Payment.idempotency_key_digest == digest))
    if existing is not None:
        receipt = (
            db.get(CommercialDocument, existing.receipt_document_id)
            if existing.receipt_document_id
            else None
        )
        assert receipt is not None
        return existing, receipt

    if invoice.kind != "invoice":
        raise CommercialError("Payments are recorded against invoices.")
    if invoice.status == "draft":
        raise CommercialError("Issue this invoice before recording payments.", status_code=409)
    if invoice.status == "voided":
        raise CommercialError("A voided invoice cannot accept payments.", status_code=409)
    if currency != invoice.currency:
        raise CommercialError("The payment currency must match the invoice.")
    if method not in ("cash", "check", "bank_transfer", "card_external", "other"):
        raise CommercialError("Unknown payment method.")
    if amount_minor <= 0:
        raise CommercialError("The payment amount must be positive.")
    remaining = invoice.total_minor - invoice.amount_paid_minor
    if amount_minor > remaining:
        raise CommercialError(
            f"This payment exceeds the remaining balance of "
            f"{format_minor(remaining, invoice.currency)}.",
            status_code=409,
        )
    reference = reject_payment_credentials(reference.strip()[:100], "reference")
    internal_note = reject_payment_credentials(internal_note.strip()[:500], "note")

    payment = Payment(
        invoice_id=invoice.id,
        amount_minor=amount_minor,
        currency=currency,
        method=method,
        paid_on=paid_on,
        reference=reference,
        internal_note=internal_note,
        idempotency_key_digest=digest,
        recorded_by=acting_user.id,
    )
    db.add(payment)
    invoice.amount_paid_minor += amount_minor
    refresh_invoice_status(db, invoice, settings_row)
    db.flush()

    receipt = _issue_receipt(db, acting_user, storage, invoice, payment, settings_row, settings)
    payment.receipt_document_id = receipt.id
    db.flush()

    job = db.get(Job, invoice.job_id)
    assert job is not None
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    add_activity(
        db,
        lead,
        "payment_recorded",
        f"Payment of {format_minor(amount_minor, currency)} recorded on invoice "
        f"{invoice.number} ({method.replace('_', ' ')}); receipt {receipt.number}.",
        acting_user=acting_user,
        meta={
            "payment_id": str(payment.id),
            "invoice_id": str(invoice.id),
            "receipt_id": str(receipt.id),
            "job_id": str(job.id),
        },
    )
    return payment, receipt


def _issue_receipt(
    db: Session,
    acting_user: User,
    storage,
    invoice: CommercialDocument,
    payment: Payment,
    settings_row: CommunicationSettings,
    settings: Settings,
) -> CommercialDocument:
    from app.services.pdf import render_commercial_pdf

    receipt = CommercialDocument(
        kind="receipt",
        job_id=invoice.job_id,
        status="issued",
        number=allocate_number(db, "receipt", settings_row.receipt_number_prefix),
        currency=invoice.currency,
        subtotal_minor=payment.amount_minor,
        total_minor=payment.amount_minor,
        issued_at=utcnow(),
        payment_id=payment.id,
        created_by=acting_user.id,
    )
    db.add(receipt)
    db.flush()

    job = db.get(Job, invoice.job_id)
    assert job is not None
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    remaining = invoice.total_minor - invoice.amount_paid_minor
    payload = {
        "kind": "receipt",
        "number": receipt.number,
        "currency": receipt.currency,
        "business": {
            "name": settings_row.business_name,
            "email": settings_row.business_email,
            "phone": settings_row.business_phone,
            "address": settings_row.business_address,
            "registration_id": settings_row.business_registration_id,
        },
        "customer": {
            "name": lead.name or lead.email or lead.phone or "Customer",
            "email": lead.email,
            "phone": lead.phone,
        },
        "job": {
            "number": job.job_number,
            "title": job.title,
            "service_address": job.service_address,
        },
        "payment": {
            "amount_minor": payment.amount_minor,
            "method": payment.method,
            "paid_on": payment.paid_on.isoformat(),
            "reference": payment.reference,
            "invoice_number": invoice.number,
            "invoice_total_minor": invoice.total_minor,
            "remaining_minor": remaining,
        },
        "lines": [],
        "discount_bp": 0,
        "totals": {
            "subtotal_minor": payment.amount_minor,
            "discount_total_minor": 0,
            "tax_total_minor": 0,
            "total_minor": payment.amount_minor,
        },
        "customer_notes": "",
        "terms": "",
        "issued_at": receipt.issued_at.isoformat(),
        "valid_until": None,
        "due_at": None,
    }
    pdf_bytes = render_commercial_pdf(payload, settings_row)
    pdf_key = new_object_key(COMMERCIAL_PREFIX, ".pdf")
    storage.put_bytes(pdf_key, pdf_bytes)
    version = CommercialDocumentVersion(
        document_id=receipt.id,
        version=1,
        number=receipt.number,
        payload=payload,
        pdf_storage_key=pdf_key,
        pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        pdf_byte_size=len(pdf_bytes),
        created_by=acting_user.id,
    )
    db.add(version)
    receipt.current_version = 1
    db.flush()
    return receipt


def reverse_payment(
    db: Session,
    acting_user: User,
    payment_id: uuid.UUID,
    settings_row: CommunicationSettings,
    *,
    reason: str,
) -> Payment:
    """Audited reversal. The original receipt is marked void and preserved."""
    payment = db.scalar(
        select(Payment)
        .where(Payment.id == payment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if payment is None:
        raise CommercialError("Payment not found.", status_code=404)
    if payment.voided_at is not None:
        return payment
    invoice = lock_document(db, payment.invoice_id)
    payment.voided_at = utcnow()
    payment.voided_by = acting_user.id
    payment.void_reason = reason.strip()[:300] or "reversed"
    invoice.amount_paid_minor -= payment.amount_minor
    refresh_invoice_status(db, invoice, settings_row)
    receipt = (
        db.get(CommercialDocument, payment.receipt_document_id)
        if payment.receipt_document_id
        else None
    )
    if receipt is not None:
        receipt.status = "voided"
        receipt.voided_at = utcnow()
        receipt.voided_by = acting_user.id
        receipt.void_reason = f"payment reversed: {payment.void_reason}"[:300]
    db.flush()
    job = db.get(Job, invoice.job_id)
    assert job is not None
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    add_activity(
        db,
        lead,
        "payment_reversed",
        f"Payment of {format_minor(payment.amount_minor, payment.currency)} on invoice "
        f"{invoice.number} reversed. Receipt {receipt.number if receipt else ''} voided.",
        acting_user=acting_user,
        meta={"payment_id": str(payment.id), "invoice_id": str(invoice.id)},
    )
    return payment


def assert_valid_statuses() -> None:  # pragma: no cover - import-time sanity
    assert set(QUOTE_STATUSES) <= {
        "draft",
        "sent",
        "viewed",
        "accepted",
        "declined",
        "expired",
        "voided",
    }
    assert set(INVOICE_STATUSES) <= {
        "draft",
        "sent",
        "viewed",
        "partially_paid",
        "paid",
        "overdue",
        "voided",
    }
