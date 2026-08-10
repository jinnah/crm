"""Uploaded job paperwork: validation, quarantine, scanning, serving.

The pipeline, in order:

1. The route enforces a streamed byte ceiling BEFORE this module runs
   (BodyLimitMiddleware), so oversize uploads die while still streaming.
2. Content is proven by decoding/signature — the filename and the client's
   MIME label are display data only. Only PDF, PNG, JPEG and WebP pass.
   HTML, SVG, executables, archives, scripts, polyglots, animated images,
   encrypted PDFs and PDFs carrying JavaScript, launch actions or embedded
   files are refused outright.
3. Images are re-encoded (orientation applied, metadata stripped, pixels
   bounded); the uploaded bytes are never stored. PDFs are stored as
   received once they pass structural checks, and are only ever served as
   attachments — never rendered inline.
4. The object lands under a quarantine key; the database row commits with
   scan_state=pending; scanning runs; only a clean result promotes the
   object to its permanent key. A scanner outage fails closed.
5. Downloads and previews go through authorized CRM routes with nosniff and
   safe filenames. Previews are server-generated normalized images only.

Nothing in this module logs file bodies, filenames, tokens or customer data.
"""

import hashlib
import io
import re
import uuid
from typing import Protocol

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    JOB_DOCUMENT_CATEGORIES,
    Job,
    JobDocument,
    Lead,
    User,
    utcnow,
)
from app.services.leads import add_activity
from app.services.scanner import ScannerUnavailable, ScanResult
from app.services.storage import (
    DOCUMENTS_PREFIX,
    PREVIEWS_PREFIX,
    QUARANTINE_PREFIX,
    new_object_key,
)

# Documented limits: per-file ceiling enforced on streamed bytes by the
# middleware; the pixel bounds mirror the logo pipeline.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8000
MAX_IMAGE_PIXELS = 40_000_000
MAX_PDF_PAGES = 200
PREVIEW_DIMENSION = 640

ACCEPTED_IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]")


class DocumentError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def move(self, source_key: str, target_key: str) -> None: ...
    def list_keys(self, prefix: str) -> list[str]: ...


class Scanner(Protocol):
    def scan_bytes(self, data: bytes) -> ScanResult: ...


def safe_filename(name: str, fallback: str) -> str:
    """Display-only filename: no paths, no control characters, bounded."""
    cleaned = _SAFE_FILENAME.sub("_", name.replace("\\", "/").split("/")[-1]).strip(" .")
    return (cleaned or fallback)[:200]


# --- Content validation -------------------------------------------------


def _validate_pdf(raw: bytes) -> None:
    if not raw.startswith(b"%PDF-"):
        raise DocumentError("This file is not a valid PDF.")
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise DocumentError("Encrypted PDFs are not accepted.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentError(f"PDFs are limited to {MAX_PDF_PAGES} pages.")
        catalog = reader.trailer.get("/Root", {})
        for forbidden in ("/OpenAction", "/AA"):
            if forbidden in catalog:
                raise DocumentError("PDFs with automatic actions are not accepted.")
        names = catalog.get("/Names", {})
        if hasattr(names, "get"):
            if "/JavaScript" in names:
                raise DocumentError("PDFs containing JavaScript are not accepted.")
            if "/EmbeddedFiles" in names:
                raise DocumentError("PDFs with embedded files are not accepted.")
        # Sweep page-level launch/JS annotations and additional actions.
        for page in reader.pages:
            if "/AA" in page:
                raise DocumentError("PDFs with automatic actions are not accepted.")
            for annotation in page.get("/Annots") or []:
                obj = annotation.get_object()
                action = obj.get("/A")
                if action is not None:
                    action_type = str(action.get_object().get("/S", ""))
                    if action_type in ("/Launch", "/JavaScript", "/SubmitForm", "/ImportData"):
                        raise DocumentError("PDFs with launch or script actions are not accepted.")
    except DocumentError:
        raise
    except (PdfReadError, KeyError, ValueError, TypeError) as error:
        raise DocumentError("This PDF could not be read safely.") from error


def _validate_and_normalize_image(raw: bytes) -> tuple[bytes, str, bytes]:
    """Prove, bound and re-encode an image; returns (bytes, mime, preview)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            image_format = probe.format
            if getattr(probe, "n_frames", 1) > 1:
                raise DocumentError("Animated images are not accepted.")
            probe.verify()
    except DocumentError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as error:
        raise DocumentError("This file is not a valid image.") from error
    if image_format not in ACCEPTED_IMAGE_FORMATS:
        raise DocumentError("Only PDF, PNG, JPEG and WebP files are accepted.")

    try:
        with Image.open(io.BytesIO(raw)) as source:
            width, height = source.size
            if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                raise DocumentError(
                    f"Images must be at most {MAX_IMAGE_DIMENSION} pixels per side."
                )
            if width * height > MAX_IMAGE_PIXELS:
                raise DocumentError("This image has too many pixels.")
            oriented = ImageOps.exif_transpose(source) or source
            if image_format == "JPEG":
                normalized = oriented.convert("RGB")
                mime = "image/jpeg"
                save_format = "JPEG"
                save_kwargs = {"quality": 90}
            else:
                normalized = oriented.convert("RGBA" if oriented.mode != "RGB" else "RGB")
                mime = ACCEPTED_IMAGE_FORMATS[image_format]
                save_format = image_format
                save_kwargs = {}
            # Re-encode with no exif/icc/info arguments: pixels only.
            buffer = io.BytesIO()
            normalized.save(buffer, format=save_format, **save_kwargs)

            preview = normalized.copy()
            preview.thumbnail((PREVIEW_DIMENSION, PREVIEW_DIMENSION), Image.LANCZOS)
            preview_buffer = io.BytesIO()
            preview.convert("RGB").save(preview_buffer, format="JPEG", quality=80)
            return buffer.getvalue(), mime, preview_buffer.getvalue()
    except DocumentError:
        raise
    except (OSError, ValueError) as error:
        raise DocumentError("This image could not be processed.") from error


def validate_content(raw: bytes) -> tuple[bytes, str, bytes | None]:
    """Returns (bytes_to_store, verified_mime, preview_or_none).

    The decision comes from the bytes alone — a spoofed extension or MIME
    label changes nothing. Everything outside the allowlist is refused.
    """
    if not raw:
        raise DocumentError("The file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise DocumentError("The file is too large.", status_code=413)
    if raw.startswith(b"%PDF-"):
        _validate_pdf(raw)
        return raw, "application/pdf", None
    stored, mime, preview = _validate_and_normalize_image(raw)
    return stored, mime, preview


# --- Upload pipeline ----------------------------------------------------


def store_upload(
    db: Session,
    storage: Storage,
    scanner: Scanner,
    acting_user: User,
    job: Job,
    *,
    raw: bytes,
    filename: str,
    title: str = "",
    category: str = "other",
    description: str = "",
) -> JobDocument:
    """Validate → quarantine object → durable row → scan → promote.

    The object is written before the row commits; if the transaction fails
    the quarantine object is unreachable (no row points anywhere else) and
    reconciliation sweeps it. Promotion happens only after a clean scan.
    """
    if job.archived_at is not None:
        raise DocumentError("Restore this job before uploading documents.", status_code=409)
    if category not in JOB_DOCUMENT_CATEGORIES:
        raise DocumentError("Unknown document category.")

    stored_bytes, mime, preview = validate_content(raw)
    digest = hashlib.sha256(stored_bytes).hexdigest()
    suffix = {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }[mime]
    quarantine_key = new_object_key(QUARANTINE_PREFIX, suffix)
    storage.put_bytes(quarantine_key, stored_bytes)

    document = JobDocument(
        job_id=job.id,
        title=title.strip()[:200] or safe_filename(filename, "document"),
        category=category,
        description=description.strip()[:1000],
        original_filename=safe_filename(filename, f"document{suffix}"),
        content_type=mime,
        byte_size=len(stored_bytes),
        sha256=digest,
        quarantine_key=quarantine_key,
        scan_state="pending",
        uploaded_by=acting_user.id,
    )
    db.add(document)
    db.flush()

    # Scan while the row is durable-pending. A clean result promotes; an
    # infected or failed result leaves the object in quarantine, inaccessible.
    try:
        result = scanner.scan_bytes(stored_bytes)
    except ScannerUnavailable as error:
        document.scan_state = "failed"
        document.scan_detail = f"scanner unavailable: {error}"[:300]
        document.scanned_at = utcnow()
        db.flush()
        return document

    document.scanned_at = utcnow()
    if not result.clean:
        document.scan_state = "infected"
        document.scan_detail = result.detail
        db.flush()
        return document

    permanent_key = new_object_key(DOCUMENTS_PREFIX, suffix)
    storage.move(quarantine_key, permanent_key)
    document.storage_key = permanent_key
    document.quarantine_key = None
    document.scan_state = "clean"
    if preview is not None:
        preview_key = new_object_key(PREVIEWS_PREFIX, ".jpg")
        storage.put_bytes(preview_key, preview)
        document.preview_storage_key = preview_key
    db.flush()

    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    add_activity(
        db,
        lead,
        "document_uploaded",
        f"Document added to job {job.job_number}: {document.title} ({category}).",
        acting_user=acting_user,
        meta={"job_id": str(job.id), "document_id": str(document.id)},
    )
    return document


def rescan(db: Session, storage: Storage, scanner: Scanner, document: JobDocument) -> JobDocument:
    """Retry scanning for a quarantined file after a scanner outage."""
    if document.scan_state not in ("pending", "failed") or document.quarantine_key is None:
        return document
    raw = storage.get_bytes(document.quarantine_key)
    try:
        result = scanner.scan_bytes(raw)
    except ScannerUnavailable as error:
        document.scan_state = "failed"
        document.scan_detail = f"scanner unavailable: {error}"[:300]
        document.scanned_at = utcnow()
        db.flush()
        return document
    document.scanned_at = utcnow()
    if not result.clean:
        document.scan_state = "infected"
        document.scan_detail = result.detail
        db.flush()
        return document
    suffix = "." + document.content_type.split("/")[-1].replace("jpeg", "jpg")
    permanent_key = new_object_key(DOCUMENTS_PREFIX, suffix)
    storage.move(document.quarantine_key, permanent_key)
    document.storage_key = permanent_key
    document.quarantine_key = None
    document.scan_state = "clean"
    document.scan_detail = None
    db.flush()
    return document


def assert_servable(document: JobDocument) -> None:
    """Only clean, promoted, non-deleted files may ever leave the server."""
    if (
        document.deleted_at is not None
        or document.scan_state != "clean"
        or document.storage_key is None
    ):
        raise DocumentError("This document is not available.", status_code=404)


def move_document(
    db: Session, acting_user: User, document: JobDocument, target_job: Job
) -> JobDocument:
    """Controlled, audited move — only between jobs of the SAME customer."""
    source_job = db.get(Job, document.job_id)
    assert source_job is not None
    if document.deleted_at is not None:
        raise DocumentError("This document has been deleted.", status_code=404)
    if target_job.id == source_job.id:
        return document
    if target_job.lead_id != source_job.lead_id:
        raise DocumentError(
            "Documents can only move between jobs of the same customer.", status_code=409
        )
    if target_job.archived_at is not None:
        raise DocumentError("Restore the target job before moving documents into it.", 409)
    document.job_id = target_job.id
    db.flush()
    lead = db.get(Lead, target_job.lead_id)
    assert lead is not None
    add_activity(
        db,
        lead,
        "document_moved",
        f"Document {document.title} moved from job {source_job.job_number} "
        f"to {target_job.job_number}.",
        acting_user=acting_user,
        meta={
            "document_id": str(document.id),
            "from_job_id": str(source_job.id),
            "to_job_id": str(target_job.id),
        },
    )
    return document


def delete_upload(
    db: Session,
    storage: Storage,
    acting_user: User,
    document: JobDocument,
    *,
    reason: str,
) -> JobDocument:
    """Audited deletion of an eligible upload: the stored object is removed
    AFTER the tombstone row state is durably committed by the caller; the row
    itself survives as the audit tombstone. Generated/issued documents are
    not JobDocuments and can never reach this path."""
    if document.deleted_at is not None:
        return document
    document.deleted_at = utcnow()
    document.deleted_by = acting_user.id
    document.delete_reason = reason.strip()[:300] or "deleted"
    keys = [document.storage_key, document.quarantine_key, document.preview_storage_key]
    document.storage_key = None
    document.quarantine_key = None
    document.preview_storage_key = None
    db.flush()
    job = db.get(Job, document.job_id)
    assert job is not None
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    add_activity(
        db,
        lead,
        "document_deleted",
        f"Document {document.title} deleted from job {job.job_number}.",
        acting_user=acting_user,
        meta={"document_id": str(document.id), "job_id": str(job.id)},
    )
    # Deleting the object last: if this raises after commit, reconciliation
    # sweeps the now-unreferenced keys.
    for key in keys:
        if key:
            storage.delete(key)
    return document


def reconcile_storage(db: Session, storage: Storage) -> dict[str, int]:
    """Remove abandoned objects; report referenced-but-missing ones.

    Suitable for a scheduled n8n cleanup call. Quarantine objects not
    referenced by any row are upload transactions that never committed;
    promoted objects not referenced by any live row are orphans from crashes
    between delete-commit and object removal.
    """
    referenced: set[str] = set()
    for row in db.execute(
        select(JobDocument.storage_key, JobDocument.quarantine_key, JobDocument.preview_storage_key)
    ):
        referenced.update(key for key in row if key)
    from app.models import CommercialDocumentVersion

    referenced.update(
        key for key in db.scalars(select(CommercialDocumentVersion.pdf_storage_key)) if key
    )

    removed = 0
    for prefix in (QUARANTINE_PREFIX, DOCUMENTS_PREFIX, PREVIEWS_PREFIX):
        for key in storage.list_keys(prefix):
            if key not in referenced:
                storage.delete(key)
                removed += 1
    missing = sum(1 for key in referenced if not storage.exists(key))
    return {"removed_orphans": removed, "missing_objects": missing}


def visible_documents_for_job(db: Session, job: Job) -> list[JobDocument]:
    return list(
        db.scalars(
            select(JobDocument)
            .where(JobDocument.job_id == job.id)
            .order_by(JobDocument.created_at.desc())
        )
    )


def content_disposition(document: JobDocument, *, inline: bool = False) -> str:
    kind = "inline" if inline else "attachment"
    name = document.original_filename.replace('"', "")
    return f'{kind}; filename="{name}"'


def make_key_suffix(mime: str) -> str:
    return {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(mime, ".bin")


def find_document(db: Session, job: Job, document_id: uuid.UUID) -> JobDocument:
    document = db.scalar(
        select(JobDocument).where(JobDocument.id == document_id, JobDocument.job_id == job.id)
    )
    if document is None:
        raise DocumentError("Document not found.", status_code=404)
    return document
