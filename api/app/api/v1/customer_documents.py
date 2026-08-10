"""Customer document access: internal BFF endpoints and n8n email transport.

Internal endpoints (/internal/documents/*) follow the corrected capability
architecture: fixed paths, the server-only BFF credential, and the raw
capability in the request BODY — never a path segment an access log records.
A capability exposes exactly one immutable version plus minimal branding.

Inbound endpoints (/inbound/document-emails/*) are the n8n side of email
delivery: claim leased work, fetch the authorized PDF, report the outcome.
They authenticate with the dedicated document-email key (inbound key as the
fallback) and fail closed with neither configured. A reconciliation endpoint
sweeps abandoned storage objects on an n8n schedule.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.api.v1.deps import DbDep, SettingsDep, require_internal_key
from app.api.v1.schemas import (
    ClaimedEmailOut,
    ClaimEmailWorkRequest,
    DocumentAccessRequest,
    QuoteResponseRequest,
    ReportEmailResultRequest,
)
from app.models import CommercialDocument, CommercialDocumentVersion, EmailDelivery
from app.security.tokens import constant_time_equals
from app.services import commercial as commercial_service
from app.services import document_access
from app.services import document_email as email_service
from app.services.branding import initials
from app.services.commercial import CommercialError
from app.services.document_access import AccessError
from app.services.document_email import EmailError
from app.services.documents import reconcile_storage
from app.services.messaging import get_settings_row
from app.services.storage import StorageError

logger = logging.getLogger(__name__)

internal_router = APIRouter(
    prefix="/internal/documents",
    tags=["internal"],
    dependencies=[Depends(require_internal_key)],
)

inbound_router = APIRouter(prefix="/inbound", tags=["document-email"])

_ERRORS = (AccessError, CommercialError, EmailError, StorageError)


def _http(error: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(error, "status_code", 400),
        detail=getattr(error, "message", "Request failed."),
    )


def _require_email_key(request: Request, settings) -> None:
    provided = request.headers.get("X-API-Key") or ""
    configured = settings.document_email_api_key or settings.inbound_api_key
    if not configured or not provided or not constant_time_equals(provided, configured):
        raise HTTPException(status_code=401, detail="Invalid API key.")


# --- customer-facing (via BFF) ------------------------------------------


@internal_router.post("/info")
def document_info(body: DocumentAccessRequest, db: DbDep, settings: SettingsDep) -> dict:
    """The one document version this capability grants, plus branding. No
    other jobs, documents, notes, staff data or internal identifiers."""
    try:
        capability = document_access.resolve_capability(db, body.token, settings)
        version = capability.version
        document = db.get(CommercialDocument, version.document_id)
        assert document is not None
        document_access.mark_viewed(db, capability)
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    settings_row = get_settings_row(db)
    payload = version.payload
    can_respond = (
        capability.purpose == "quote_response"
        and document.kind == "quote"
        and document.status in ("sent", "viewed")
        and document.voided_at is None
    )
    return {
        "kind": document.kind,
        "number": version.number,
        "status": document.status,
        "business_name": settings_row.business_name,
        "business_initials": initials(settings_row.business_name),
        "payload": payload,
        "responded_at": (document.responded_at.isoformat() if document.responded_at else None),
        "response_name": document.response_name,
        "can_respond": can_respond,
    }


@internal_router.post("/pdf")
def document_pdf(
    body: DocumentAccessRequest, request: Request, db: DbDep, settings: SettingsDep
) -> Response:
    try:
        capability = document_access.resolve_capability(db, body.token, settings)
        version = capability.version
        document_access.mark_viewed(db, capability)
        db.commit()
        data = request.app.state.document_storage.get_bytes(version.pdf_storage_key)
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    filename = f"{version.number}.pdf".replace("/", "-")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@internal_router.post("/respond")
def respond_to_quote(body: QuoteResponseRequest, db: DbDep, settings: SettingsDep) -> dict:
    """Accept or decline the active sent snapshot. Idempotent; concurrent
    opposite responses have one deterministic winner (the committed one)."""
    if body.website:
        # Honeypot filled: pretend success, change nothing.
        return {"status": "ok"}
    try:
        capability = document_access.resolve_capability(db, body.token, settings)
        if capability.purpose != "quote_response":
            raise AccessError("This link does not allow responding.", status_code=403)
        version = capability.version
        document = commercial_service.respond_to_quote(
            db,
            version.document_id,
            version,
            accept=body.accept,
            typed_name=body.typed_name,
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return {
        "status": document.status,
        "responded_at": document.responded_at.isoformat() if document.responded_at else None,
        "response_name": document.response_name,
    }


# --- n8n email transport -------------------------------------------------


@inbound_router.post("/document-emails/claim", response_model=list[ClaimedEmailOut])
def claim_email_work(
    body: ClaimEmailWorkRequest, request: Request, db: DbDep, settings: SettingsDep
) -> list[ClaimedEmailOut]:
    _require_email_key(request, settings)
    try:
        email_service.recover_stale_claims(db)
        claimed = email_service.claim_pending(db, limit=body.limit)
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    results: list[ClaimedEmailOut] = []
    for delivery in claimed:
        pdf_filename = None
        if delivery.attach_pdf and delivery.version_id is not None:
            version = db.get(CommercialDocumentVersion, delivery.version_id)
            if version is not None:
                pdf_filename = f"{version.number}.pdf".replace("/", "-")
        results.append(
            ClaimedEmailOut(
                id=delivery.id,
                recipient=delivery.recipient,
                from_name=delivery.from_name,
                from_address=delivery.from_address,
                reply_to=delivery.reply_to,
                subject=delivery.subject,
                body_text=delivery.body_text,
                body_html=delivery.body_html,
                attach_pdf=delivery.attach_pdf,
                purpose=delivery.purpose,
                version_id=delivery.version_id,
                pdf_filename=pdf_filename,
            )
        )
    return results


@inbound_router.get("/document-emails/{delivery_id}/pdf")
def claimed_email_pdf(
    delivery_id: uuid.UUID, request: Request, db: DbDep, settings: SettingsDep
) -> Response:
    """The exact immutable PDF for a claimed delivery — nothing else is
    fetchable through this path, and only while the claim/submission is live."""
    _require_email_key(request, settings)
    delivery = db.get(EmailDelivery, delivery_id)
    if delivery is None or delivery.version_id is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if delivery.status not in ("claimed", "submitted"):
        raise HTTPException(status_code=409, detail="This delivery is not claimed.")
    version = db.get(CommercialDocumentVersion, delivery.version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        data = request.app.state.document_storage.get_bytes(version.pdf_storage_key)
    except StorageError as error:
        raise _http(error) from error
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{version.number}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


@inbound_router.post("/document-emails/report")
def report_email_result(
    body: ReportEmailResultRequest, request: Request, db: DbDep, settings: SettingsDep
) -> dict:
    _require_email_key(request, settings)
    delivery = db.get(EmailDelivery, body.delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        email_service.report_result(
            db,
            delivery,
            outcome=body.outcome,
            provider_message_id=body.provider_message_id,
            failure_class=body.failure_class,
            failure_message=body.failure_message,
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return {"status": delivery.status}


@inbound_router.post("/documents/reconcile")
def reconcile_documents(request: Request, db: DbDep, settings: SettingsDep) -> dict:
    """Scheduled cleanup: sweep abandoned quarantine/orphan objects, report
    referenced-but-missing ones. Never touches referenced objects."""
    _require_email_key(request, settings)
    try:
        result = reconcile_storage(db, request.app.state.document_storage)
        db.commit()
    except StorageError as error:
        db.rollback()
        raise _http(error) from error
    return result
