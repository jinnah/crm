"""Jobs, their documents, commercial records, payments and email history.

Authorization (enforced here, on the server):

- Owner and manager: everything.
- Team members: view and work with jobs on their own leads or assigned to
  them. They MAY upload documents and create/edit drafts on those jobs.
- Issuing, voiding, converting, sending email, moving/deleting documents and
  recording/reversing payments are owner/manager only.

Uploads and downloads are rate-limited per account. Download responses carry
nosniff and a safe disposition; previews are server-generated images only.
Nothing here logs filenames, tokens, file bodies or customer data.
"""

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import (
    DbDep,
    FullyAuthedUserDep,
    SettingsDep,
    check_csrf,
    check_origin,
)
from app.api.v1.schemas import (
    CommercialDocumentOut,
    CreateJobRequest,
    DeleteJobDocumentRequest,
    DraftRequest,
    EmailDeliveryOut,
    JobDocumentOut,
    JobListOut,
    JobOut,
    JobStatusRequest,
    LineItemOut,
    LinkAppointmentRequest,
    MessageResponse,
    MoveJobDocumentRequest,
    PaymentOut,
    RecordPaymentRequest,
    ReversePaymentRequest,
    SendDocumentEmailRequest,
    UpdateDraftRequest,
    UpdateJobDocumentRequest,
    UpdateJobRequest,
    VersionOut,
    VoidRequest,
)
from app.models import (
    Appointment,
    CommercialDocument,
    CommercialDocumentVersion,
    EmailDelivery,
    Job,
    JobDocument,
    Lead,
    Payment,
    User,
)
from app.services import commercial as commercial_service
from app.services import document_access
from app.services import document_email as email_service
from app.services import documents as document_service
from app.services import jobs as job_service
from app.services.commercial import CommercialError
from app.services.document_access import AccessError
from app.services.document_email import EmailError
from app.services.documents import DocumentError
from app.services.jobs import JobError
from app.services.leads import LeadError, can_manage_leads, get_visible_lead
from app.services.messaging import get_settings_row
from app.services.numbering import NumberingError
from app.services.storage import StorageError

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(check_origin), Depends(check_csrf)],
)

_ERRORS = (
    JobError,
    DocumentError,
    CommercialError,
    EmailError,
    AccessError,
    NumberingError,
    StorageError,
    LeadError,
)


def _http(error: Exception) -> HTTPException:
    status = getattr(error, "status_code", 400)
    message = getattr(error, "message", "Request failed.")
    return HTTPException(status_code=status, detail=message)


def _require_manager(user: User) -> None:
    if not can_manage_leads(user):
        raise HTTPException(status_code=403, detail="Only owners and managers can do this.")


def _get_storage(request: Request):
    return request.app.state.document_storage


def _get_scanner(request: Request):
    return request.app.state.document_scanner


def _check_limit(request: Request, limiter_name: str, user: User) -> None:
    limiter = getattr(request.app.state, limiter_name)
    key = str(user.id)
    if not limiter.allowed(key):
        raise HTTPException(status_code=429, detail="Too many requests. Try again later.")
    limiter.record(key)


def serialize_job(db: Session, job: Job) -> JobOut:
    lead = db.get(Lead, job.lead_id)
    assignee = db.get(User, job.assigned_to) if job.assigned_to else None
    return JobOut(
        id=job.id,
        job_number=job.job_number,
        lead_id=job.lead_id,
        lead_name=(lead.name or lead.email or lead.phone) if lead else None,
        title=job.title,
        service_type=job.service_type,
        service_address=job.service_address,
        status=job.status,
        assigned_to=job.assigned_to,
        assignee_name=(assignee.display_name or assignee.email) if assignee else None,
        scheduled_for=job.scheduled_for,
        started_at=job.started_at,
        completed_at=job.completed_at,
        internal_notes=job.internal_notes,
        archived_at=job.archived_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _serialize_document(document: JobDocument) -> JobDocumentOut:
    out = JobDocumentOut.model_validate(document)
    out.has_preview = document.preview_storage_key is not None
    return out


def serialize_commercial(db: Session, document: CommercialDocument) -> CommercialDocumentOut:
    lines = [
        LineItemOut(
            position=line.position,
            description=line.description,
            quantity_milli=line.quantity_milli,
            unit=line.unit,
            unit_price_minor=line.unit_price_minor,
            discount_bp=line.discount_bp,
            tax_rate_bp=line.tax_rate_bp,
            line_total_minor=line.line_total_minor,
        )
        for line in commercial_service.document_lines(db, document)
    ]
    return CommercialDocumentOut(
        id=document.id,
        kind=document.kind,
        job_id=document.job_id,
        status=document.status,
        number=document.number,
        currency=document.currency,
        discount_bp=document.discount_bp,
        subtotal_minor=document.subtotal_minor,
        discount_total_minor=document.discount_total_minor,
        tax_total_minor=document.tax_total_minor,
        total_minor=document.total_minor,
        amount_paid_minor=document.amount_paid_minor,
        customer_notes=document.customer_notes,
        terms=document.terms,
        valid_until=document.valid_until,
        issued_at=document.issued_at,
        due_at=document.due_at,
        current_version=document.current_version,
        responded_at=document.responded_at,
        response_name=document.response_name,
        source_quote_id=document.source_quote_id,
        converted_invoice_id=document.converted_invoice_id,
        payment_id=document.payment_id,
        voided_at=document.voided_at,
        void_reason=document.void_reason,
        created_at=document.created_at,
        lines=lines,
    )


# --- jobs ----------------------------------------------------------------


@router.get("", response_model=JobListOut)
def list_jobs(
    user: FullyAuthedUserDep,
    db: DbDep,
    query: str | None = Query(default=None, max_length=200),
    status: str | None = None,
    assignee_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    archived: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> JobListOut:
    try:
        jobs, total = job_service.list_jobs(
            db,
            user,
            query=query,
            status=status,
            assignee_id=assignee_id,
            lead_id=lead_id,
            archived=archived,
            page=page,
            page_size=page_size,
        )
    except _ERRORS as error:
        raise _http(error) from error
    return JobListOut(
        items=[serialize_job(db, job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=JobOut, status_code=201)
def create_job(body: CreateJobRequest, user: FullyAuthedUserDep, db: DbDep) -> JobOut:
    try:
        lead = get_visible_lead(db, user, body.lead_id)
        job = job_service.create_job(
            db,
            user,
            lead,
            title=body.title,
            service_type=body.service_type,
            service_address=body.service_address,
            assigned_to=body.assigned_to,
            scheduled_for=body.scheduled_for,
            internal_notes=body.internal_notes,
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return serialize_job(db, job)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> JobOut:
    try:
        job = job_service.get_visible_job(db, user, job_id)
    except _ERRORS as error:
        raise _http(error) from error
    return serialize_job(db, job)


@router.patch("/{job_id}", response_model=JobOut)
def update_job(
    job_id: uuid.UUID, body: UpdateJobRequest, user: FullyAuthedUserDep, db: DbDep
) -> JobOut:
    try:
        job = job_service.get_visible_job(db, user, job_id)
        job = job_service.update_job(
            db,
            user,
            job,
            title=body.title,
            service_type=body.service_type,
            service_address=body.service_address,
            assigned_to=body.assigned_to,
            clear_assignee=body.clear_assignee,
            scheduled_for=body.scheduled_for,
            clear_scheduled_for=body.clear_scheduled_for,
            internal_notes=body.internal_notes,
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return serialize_job(db, job)


@router.post("/{job_id}/status", response_model=JobOut)
def change_job_status(
    job_id: uuid.UUID, body: JobStatusRequest, user: FullyAuthedUserDep, db: DbDep
) -> JobOut:
    try:
        job = job_service.get_visible_job(db, user, job_id)
        job = job_service.change_status(db, user, job, body.status, note=body.note)
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return serialize_job(db, job)


@router.post("/{job_id}/archive", response_model=JobOut)
def archive_job(job_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> JobOut:
    _require_manager(user)
    try:
        job = job_service.get_visible_job(db, user, job_id)
        job = job_service.archive_job(db, user, job)
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return serialize_job(db, job)


@router.post("/{job_id}/restore", response_model=JobOut)
def restore_job(job_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> JobOut:
    _require_manager(user)
    try:
        job = job_service.get_visible_job(db, user, job_id)
        job = job_service.restore_job(db, user, job)
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return serialize_job(db, job)


@router.delete("/{job_id}", response_model=MessageResponse)
def delete_job(job_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> MessageResponse:
    """Jobs are archived, never hard-deleted — this always refuses."""
    _require_manager(user)
    try:
        job = job_service.get_visible_job(db, user, job_id)
        job_service.assert_never_deleted(db, job)
    except _ERRORS as error:
        raise _http(error) from error
    raise HTTPException(status_code=405, detail="Jobs are archived, never deleted.")


@router.post("/{job_id}/link-appointment", response_model=MessageResponse)
def link_appointment(
    job_id: uuid.UUID, body: LinkAppointmentRequest, user: FullyAuthedUserDep, db: DbDep
) -> MessageResponse:
    try:
        job = job_service.get_visible_job(db, user, job_id)
        appointment = db.get(Appointment, body.appointment_id)
        if appointment is None:
            raise JobError("Appointment not found.", status_code=404)
        job_service.link_appointment(db, user, job, appointment)
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return MessageResponse(detail="Appointment linked to the job.")


# --- uploaded documents --------------------------------------------------


@router.get("/{job_id}/documents", response_model=list[JobDocumentOut])
def list_documents(job_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> list[JobDocumentOut]:
    try:
        job = job_service.get_visible_job(db, user, job_id)
    except _ERRORS as error:
        raise _http(error) from error
    return [
        _serialize_document(document)
        for document in document_service.visible_documents_for_job(db, job)
    ]


@router.post("/{job_id}/documents", response_model=JobDocumentOut, status_code=201)
async def upload_document(
    job_id: uuid.UUID,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
    file: UploadFile,
    title: str = Form(default="", max_length=200),
    category: str = Form(default="other", max_length=16),
    description: str = Form(default="", max_length=1000),
) -> JobDocumentOut:
    _check_limit(request, "document_upload_limiter", user)
    raw = await file.read()
    try:
        job = job_service.get_visible_job(db, user, job_id)
        document = document_service.store_upload(
            db,
            _get_storage(request),
            _get_scanner(request),
            user,
            job,
            raw=raw,
            filename=file.filename or "document",
            title=title,
            category=category,
            description=description,
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return _serialize_document(document)


@router.get("/{job_id}/documents/{document_id}/download")
def download_document(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
) -> Response:
    _check_limit(request, "document_download_limiter", user)
    try:
        job = job_service.get_visible_job(db, user, job_id)
        document = document_service.find_document(db, job, document_id)
        document_service.assert_servable(document)
        data = _get_storage(request).get_bytes(document.storage_key)
    except _ERRORS as error:
        raise _http(error) from error
    return Response(
        content=data,
        media_type=document.content_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": document_service.content_disposition(document),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{job_id}/documents/{document_id}/preview")
def preview_document(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
) -> Response:
    """Server-generated normalized image preview — never the original file."""
    _check_limit(request, "document_download_limiter", user)
    try:
        job = job_service.get_visible_job(db, user, job_id)
        document = document_service.find_document(db, job, document_id)
        document_service.assert_servable(document)
        if document.preview_storage_key is None:
            raise DocumentError("No preview is available for this file.", status_code=404)
        data = _get_storage(request).get_bytes(document.preview_storage_key)
    except _ERRORS as error:
        raise _http(error) from error
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.patch("/{job_id}/documents/{document_id}", response_model=JobDocumentOut)
def update_document(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    body: UpdateJobDocumentRequest,
    user: FullyAuthedUserDep,
    db: DbDep,
) -> JobDocumentOut:
    try:
        job = job_service.get_visible_job(db, user, job_id)
        document = document_service.find_document(db, job, document_id)
        if document.deleted_at is not None:
            raise DocumentError("This document has been deleted.", status_code=404)
        if body.title is not None:
            document.title = body.title.strip()[:200] or document.title
        if body.category is not None:
            from app.models import JOB_DOCUMENT_CATEGORIES

            if body.category not in JOB_DOCUMENT_CATEGORIES:
                raise DocumentError("Unknown document category.")
            document.category = body.category
        if body.description is not None:
            document.description = body.description.strip()[:1000]
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return _serialize_document(document)


@router.post("/{job_id}/documents/{document_id}/archive", response_model=JobDocumentOut)
def archive_document(
    job_id: uuid.UUID, document_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep
) -> JobDocumentOut:
    try:
        job = job_service.get_visible_job(db, user, job_id)
        document = document_service.find_document(db, job, document_id)
        if document.deleted_at is not None:
            raise DocumentError("This document has been deleted.", status_code=404)
        from app.models import utcnow

        if document.archived_at is None:
            document.archived_at = utcnow()
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return _serialize_document(document)


@router.post("/{job_id}/documents/{document_id}/restore", response_model=JobDocumentOut)
def restore_document(
    job_id: uuid.UUID, document_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep
) -> JobDocumentOut:
    try:
        job = job_service.get_visible_job(db, user, job_id)
        document = document_service.find_document(db, job, document_id)
        if document.deleted_at is not None:
            raise DocumentError("Deleted documents cannot be restored.", status_code=409)
        document.archived_at = None
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return _serialize_document(document)


@router.post("/{job_id}/documents/{document_id}/rescan", response_model=JobDocumentOut)
def rescan_document(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
) -> JobDocumentOut:
    """Retry malware scanning for a quarantined upload after a scanner
    outage. Only pending/failed documents are eligible; a clean result
    promotes the file, an infected result keeps it quarantined."""
    try:
        job = job_service.get_visible_job(db, user, job_id)
        document = document_service.find_document(db, job, document_id)
        if document.deleted_at is not None:
            raise DocumentError("This document has been deleted.", status_code=404)
        if document.scan_state not in ("pending", "failed") or document.quarantine_key is None:
            raise DocumentError(
                "Only quarantined documents awaiting a scan can be rescanned.", status_code=409
            )
        document = document_service.rescan(
            db, _get_storage(request), _get_scanner(request), document
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return _serialize_document(document)


@router.post("/{job_id}/documents/{document_id}/move", response_model=JobDocumentOut)
def move_document(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    body: MoveJobDocumentRequest,
    user: FullyAuthedUserDep,
    db: DbDep,
) -> JobDocumentOut:
    _require_manager(user)
    try:
        job = job_service.get_visible_job(db, user, job_id)
        document = document_service.find_document(db, job, document_id)
        target = job_service.get_visible_job(db, user, body.target_job_id)
        document = document_service.move_document(db, user, document, target)
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return _serialize_document(document)


@router.post("/{job_id}/documents/{document_id}/delete", response_model=JobDocumentOut)
def delete_document(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    body: DeleteJobDocumentRequest,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
) -> JobDocumentOut:
    """Audited deletion of an uploaded document; the row survives as a
    tombstone. Generated commercial documents can never reach this path."""
    _require_manager(user)
    try:
        job = job_service.get_visible_job(db, user, job_id)
        document = document_service.find_document(db, job, document_id)
        document = document_service.delete_upload(
            db, _get_storage(request), user, document, reason=body.reason
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return _serialize_document(document)


# --- commercial documents ------------------------------------------------


@router.get("/{job_id}/commercial", response_model=list[CommercialDocumentOut])
def list_commercial(
    job_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep
) -> list[CommercialDocumentOut]:
    try:
        job = job_service.get_visible_job(db, user, job_id)
    except _ERRORS as error:
        raise _http(error) from error
    settings_row = get_settings_row(db)
    documents = list(
        db.scalars(
            select(CommercialDocument)
            .where(CommercialDocument.job_id == job.id)
            .order_by(CommercialDocument.created_at.desc())
        )
    )
    for document in documents:
        if document.kind == "invoice":
            commercial_service.refresh_invoice_status(db, document, settings_row)
    db.commit()
    return [serialize_commercial(db, document) for document in documents]


@router.post("/{job_id}/commercial", response_model=CommercialDocumentOut, status_code=201)
def create_draft(
    job_id: uuid.UUID, body: DraftRequest, user: FullyAuthedUserDep, db: DbDep
) -> CommercialDocumentOut:
    try:
        job = job_service.get_visible_job(db, user, job_id)
        settings_row = get_settings_row(db)
        document = commercial_service.create_draft(db, user, job, settings_row, kind=body.kind)
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return serialize_commercial(db, document)


def _get_job_commercial(
    db: Session, user: User, job_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[Job, CommercialDocument]:
    job = job_service.get_visible_job(db, user, job_id)
    document = db.scalar(
        select(CommercialDocument).where(
            CommercialDocument.id == document_id, CommercialDocument.job_id == job.id
        )
    )
    if document is None:
        raise CommercialError("Document not found.", status_code=404)
    return job, document


@router.patch("/{job_id}/commercial/{document_id}", response_model=CommercialDocumentOut)
def update_draft(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    body: UpdateDraftRequest,
    user: FullyAuthedUserDep,
    db: DbDep,
) -> CommercialDocumentOut:
    try:
        _, document = _get_job_commercial(db, user, job_id, document_id)
        document = commercial_service.replace_lines(
            db,
            document,
            [line.model_dump() for line in body.lines],
            discount_bp=body.discount_bp,
            customer_notes=body.customer_notes,
            terms=body.terms,
            valid_until=body.valid_until,
            due_at=body.due_at,
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return serialize_commercial(db, document)


@router.post("/{job_id}/commercial/{document_id}/issue", response_model=CommercialDocumentOut)
def issue_document(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
    settings: SettingsDep,
) -> CommercialDocumentOut:
    _require_manager(user)
    try:
        _, document = _get_job_commercial(db, user, job_id, document_id)
        settings_row = get_settings_row(db)
        commercial_service.issue(db, user, _get_storage(request), document, settings_row, settings)
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return serialize_commercial(db, document)


@router.post("/{job_id}/commercial/{document_id}/void", response_model=CommercialDocumentOut)
def void_document(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    body: VoidRequest,
    user: FullyAuthedUserDep,
    db: DbDep,
) -> CommercialDocumentOut:
    _require_manager(user)
    try:
        _, document = _get_job_commercial(db, user, job_id, document_id)
        document = commercial_service.void_document(db, user, document, body.reason)
        document_access.revoke_for_document(db, document.id)
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return serialize_commercial(db, document)


@router.post("/{job_id}/commercial/{document_id}/convert", response_model=CommercialDocumentOut)
def convert_quote(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
    settings: SettingsDep,
) -> CommercialDocumentOut:
    _require_manager(user)
    try:
        _, quote = _get_job_commercial(db, user, job_id, document_id)
        settings_row = get_settings_row(db)
        invoice = commercial_service.convert_quote_to_invoice(
            db, user, _get_storage(request), quote, settings_row, settings
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return serialize_commercial(db, invoice)


@router.get("/{job_id}/commercial/{document_id}/versions", response_model=list[VersionOut])
def list_versions(
    job_id: uuid.UUID, document_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep
) -> list[VersionOut]:
    try:
        _, document = _get_job_commercial(db, user, job_id, document_id)
    except _ERRORS as error:
        raise _http(error) from error
    versions = db.scalars(
        select(CommercialDocumentVersion)
        .where(CommercialDocumentVersion.document_id == document.id)
        .order_by(CommercialDocumentVersion.version.desc())
    )
    return [VersionOut.model_validate(version) for version in versions]


@router.get("/{job_id}/commercial/{document_id}/versions/{version}/pdf")
def download_version_pdf(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    version: int,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
) -> Response:
    _check_limit(request, "document_download_limiter", user)
    try:
        _, document = _get_job_commercial(db, user, job_id, document_id)
        row = db.scalar(
            select(CommercialDocumentVersion).where(
                CommercialDocumentVersion.document_id == document.id,
                CommercialDocumentVersion.version == version,
            )
        )
        if row is None:
            raise CommercialError("Version not found.", status_code=404)
        data = _get_storage(request).get_bytes(row.pdf_storage_key)
    except _ERRORS as error:
        raise _http(error) from error
    filename = f"{row.number}-v{row.version}.pdf".replace("/", "-")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


# --- payments ------------------------------------------------------------


@router.get("/{job_id}/payments", response_model=list[PaymentOut])
def list_payments(job_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> list[PaymentOut]:
    try:
        job = job_service.get_visible_job(db, user, job_id)
    except _ERRORS as error:
        raise _http(error) from error
    payments = db.scalars(
        select(Payment)
        .join(CommercialDocument, CommercialDocument.id == Payment.invoice_id)
        .where(CommercialDocument.job_id == job.id)
        .order_by(Payment.created_at.desc())
    )
    return [PaymentOut.model_validate(payment) for payment in payments]


@router.post(
    "/{job_id}/commercial/{document_id}/payments", response_model=PaymentOut, status_code=201
)
def record_payment(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    body: RecordPaymentRequest,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
    settings: SettingsDep,
) -> PaymentOut:
    _require_manager(user)
    try:
        _, invoice = _get_job_commercial(db, user, job_id, document_id)
        settings_row = get_settings_row(db)
        payment, _receipt = commercial_service.record_payment(
            db,
            user,
            _get_storage(request),
            invoice.id,
            settings_row,
            settings,
            amount_minor=body.amount_minor,
            currency=body.currency.upper(),
            method=body.method,
            paid_on=body.paid_on,
            reference=body.reference,
            internal_note=body.internal_note,
            idempotency_key=body.idempotency_key,
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return PaymentOut.model_validate(payment)


@router.post("/{job_id}/payments/{payment_id}/reverse", response_model=PaymentOut)
def reverse_payment(
    job_id: uuid.UUID,
    payment_id: uuid.UUID,
    body: ReversePaymentRequest,
    user: FullyAuthedUserDep,
    db: DbDep,
) -> PaymentOut:
    _require_manager(user)
    try:
        job = job_service.get_visible_job(db, user, job_id)
        payment = db.scalar(
            select(Payment)
            .join(CommercialDocument, CommercialDocument.id == Payment.invoice_id)
            .where(Payment.id == payment_id, CommercialDocument.job_id == job.id)
        )
        if payment is None:
            raise CommercialError("Payment not found.", status_code=404)
        settings_row = get_settings_row(db)
        payment = commercial_service.reverse_payment(
            db, user, payment.id, settings_row, reason=body.reason
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return PaymentOut.model_validate(payment)


# --- document email ------------------------------------------------------


@router.get("/{job_id}/emails", response_model=list[EmailDeliveryOut])
def list_emails(job_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> list[EmailDeliveryOut]:
    try:
        job = job_service.get_visible_job(db, user, job_id)
    except _ERRORS as error:
        raise _http(error) from error
    deliveries = db.scalars(
        select(EmailDelivery)
        .where(EmailDelivery.job_id == job.id)
        .order_by(EmailDelivery.created_at.desc())
    )
    return [EmailDeliveryOut.model_validate(delivery) for delivery in deliveries]


@router.post(
    "/{job_id}/commercial/{document_id}/send",
    response_model=EmailDeliveryOut,
    status_code=201,
)
def send_document_email(
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    body: SendDocumentEmailRequest,
    user: FullyAuthedUserDep,
    db: DbDep,
    settings: SettingsDep,
) -> EmailDeliveryOut:
    """Queue the active version for email. The durable delivery record (and
    its capability link) commits here; n8n picks it up separately — no
    provider is contacted inside this transaction."""
    _require_manager(user)
    try:
        _, document = _get_job_commercial(db, user, job_id, document_id)
        version = commercial_service.active_version(db, document)
        if version is None:
            raise CommercialError("Issue this document before sending it.", status_code=409)
        settings_row = get_settings_row(db)
        purpose = "quote_response" if document.kind == "quote" else "view"
        capability, raw_token = document_access.issue_capability(
            db, settings, settings_row, version.id, purpose=purpose, created_by=user
        )
        secure_link = f"{settings.frontend_url}/document/{raw_token}"
        delivery = email_service.create_delivery(
            db,
            user,
            settings,
            settings_row,
            version=version,
            recipient=str(body.recipient),
            secure_link=secure_link,
            capability_id=capability.id,
            attach_pdf=body.attach_pdf,
            send_key=body.send_key,
        )
        db.commit()
    except _ERRORS as error:
        db.rollback()
        raise _http(error) from error
    return EmailDeliveryOut.model_validate(delivery)
