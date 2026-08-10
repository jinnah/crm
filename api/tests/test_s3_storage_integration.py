"""S3-compatible storage integration proofs for the document lifecycle.

The default suite proves the pipeline against LocalDiskStorage; these tests
run the SAME pipeline against a real S3-compatible service through the real
boto3-backed S3Storage: quarantine upload, promotion (copy+delete), authorized
download, deletion with tombstones, forced mid-operation failures, orphan
reconciliation and missing-object reporting.

Gated so the default suite stays light. Point them at a DISPOSABLE bucket —
reconciliation deletes unreferenced objects under the app prefixes:

    DOCUMENTS_S3_TEST_ENDPOINT=http://127.0.0.1:9000 \
    DOCUMENTS_S3_TEST_BUCKET=crm-verify-docs \
    DOCUMENTS_S3_TEST_ACCESS_KEY=... DOCUMENTS_S3_TEST_SECRET_KEY=... \
        uv run pytest tests/test_s3_storage_integration.py

Against a local MinIO this verifies S3-compatibility of the implementation;
verification against the installation's intended production object store
still requires that store's endpoint and credentials.
"""

import os
import uuid

import pytest

from app.config import Settings
from app.models import JobDocument
from app.services import documents as document_service
from app.services.scanner import ScannerUnavailable, ScanResult
from app.services.storage import (
    DOCUMENTS_PREFIX,
    QUARANTINE_PREFIX,
    S3Storage,
    StorageError,
)
from tests.test_job_documents import job_setup, pdf_bytes, png_bytes, upload  # noqa: F401

TEST_ENDPOINT = os.environ.get("DOCUMENTS_S3_TEST_ENDPOINT")

pytestmark = pytest.mark.skipif(
    not TEST_ENDPOINT,
    reason="DOCUMENTS_S3_TEST_* not set; S3 integration tests need a disposable bucket",
)


class AlwaysClean:
    def scan_bytes(self, data: bytes) -> ScanResult:
        return ScanResult(True)


class AlwaysInfected:
    def scan_bytes(self, data: bytes) -> ScanResult:
        return ScanResult(False, "Verification-Test-Signature")


class AlwaysDown:
    def scan_bytes(self, data: bytes) -> ScanResult:
        raise ScannerUnavailable("simulated outage")


@pytest.fixture()
def s3_storage() -> S3Storage:
    settings = Settings(
        documents_storage_backend="s3",
        documents_s3_bucket=os.environ["DOCUMENTS_S3_TEST_BUCKET"],
        documents_s3_endpoint_url=TEST_ENDPOINT,
        documents_s3_region=os.environ.get("DOCUMENTS_S3_TEST_REGION", "us-east-1"),
        documents_s3_access_key_id=os.environ["DOCUMENTS_S3_TEST_ACCESS_KEY"],
        documents_s3_secret_access_key=os.environ["DOCUMENTS_S3_TEST_SECRET_KEY"],
    )
    storage = S3Storage(settings)
    yield storage
    # Sweep everything the app may have written, leaving the bucket reusable.
    for prefix in ("quarantine/", "documents/", "previews/", "commercial/"):
        for key in storage.list_keys(prefix):
            storage.delete(key)


@pytest.fixture()
def s3_app(app, s3_storage):
    """The full application with its storage swapped to the real S3 backend."""
    app.state.document_storage = s3_storage
    app.state.document_scanner = AlwaysClean()
    return app


# --- Backend contract against the real service ---------------------------


def test_health_and_roundtrip(s3_storage):
    assert s3_storage.health() == {"backend": "s3", "status": "ok"}
    key = f"{QUARANTINE_PREFIX}roundtrip-{uuid.uuid4().hex}.pdf"
    s3_storage.put_bytes(key, b"payload")
    assert s3_storage.exists(key)
    assert s3_storage.get_bytes(key) == b"payload"

    target = f"{DOCUMENTS_PREFIX}promoted-{uuid.uuid4().hex}.pdf"
    s3_storage.move(key, target)
    assert not s3_storage.exists(key)
    assert s3_storage.get_bytes(target) == b"payload"

    s3_storage.delete(target)
    assert not s3_storage.exists(target)
    with pytest.raises(StorageError):
        s3_storage.get_bytes(target)


def test_key_validation_blocks_traversal_and_junk(s3_storage):
    for bad in ("../escape", "/absolute", "documents/../../x", "UPPER/Case", "a" * 301):
        with pytest.raises(StorageError):
            s3_storage.get_bytes(bad)


# --- Full pipeline lifecycle over HTTP with S3 storage --------------------


def test_clean_upload_lands_quarantined_then_promotes_in_s3(client, db, s3_app, job_setup):  # noqa: F811
    headers, _lead, job = job_setup
    storage = s3_app.state.document_storage
    response = upload(client, headers, job["id"], pdf_bytes(), filename="quote.pdf")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scan_state"] == "clean"
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    assert row.storage_key.startswith(DOCUMENTS_PREFIX)
    assert row.quarantine_key is None
    assert storage.exists(row.storage_key)
    assert storage.list_keys(QUARANTINE_PREFIX) == []

    download = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download")
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF-")


def test_infected_upload_stays_in_s3_quarantine(client, db, s3_app, job_setup):  # noqa: F811
    headers, _lead, job = job_setup
    s3_app.state.document_scanner = AlwaysInfected()
    storage = s3_app.state.document_storage
    response = upload(client, headers, job["id"], pdf_bytes(), filename="bad.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["scan_state"] == "infected"
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    assert row.storage_key is None
    assert row.quarantine_key.startswith(QUARANTINE_PREFIX)
    assert storage.exists(row.quarantine_key)
    assert storage.list_keys(DOCUMENTS_PREFIX) == []
    assert (
        client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download").status_code == 404
    )


def test_scanner_outage_leaves_s3_quarantine_then_rescan_promotes(
    client,
    db,
    s3_app,
    job_setup,  # noqa: F811
):
    headers, _lead, job = job_setup
    s3_app.state.document_scanner = AlwaysDown()
    storage = s3_app.state.document_storage
    body = upload(client, headers, job["id"], pdf_bytes(), filename="permit.pdf").json()
    assert body["scan_state"] == "failed"
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    assert row.quarantine_key is not None and storage.exists(row.quarantine_key)

    document_service.rescan(db, storage, AlwaysClean(), row)
    db.commit()
    assert row.scan_state == "clean"
    assert storage.exists(row.storage_key)
    assert not storage.list_keys(QUARANTINE_PREFIX)


def test_deletion_removes_objects_and_keeps_tombstone(client, db, s3_app, job_setup):  # noqa: F811
    headers, _lead, job = job_setup
    storage = s3_app.state.document_storage
    body = upload(client, headers, job["id"], png_bytes(), filename="roof.png").json()
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    object_keys = [row.storage_key, row.preview_storage_key]
    assert all(storage.exists(key) for key in object_keys)

    deleted = client.post(
        f"/api/v1/jobs/{job['id']}/documents/{body['id']}/delete",
        json={"reason": "verification cleanup"},
        headers=headers,
    )
    assert deleted.status_code == 200
    db.refresh(row)
    # Tombstone retained: the row survives with audit fields, keys nulled.
    assert row.deleted_at is not None
    assert row.delete_reason == "verification cleanup"
    assert row.storage_key is None and row.preview_storage_key is None
    for key in object_keys:
        assert not storage.exists(key)
    assert (
        client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download").status_code == 404
    )


# --- Failure injection and reconciliation --------------------------------


def test_crash_between_tombstone_and_object_delete_is_reconciled(
    client,
    db,
    s3_app,
    job_setup,  # noqa: F811
):
    """Forced mid-operation failure: the tombstone commits but object removal
    'crashes'. The orphaned objects must be swept by reconciliation."""
    headers, _lead, job = job_setup
    storage = s3_app.state.document_storage
    body = upload(client, headers, job["id"], pdf_bytes(), filename="dup.pdf").json()
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    orphan_key = row.storage_key

    from app.models import User, utcnow

    acting = db.query(User).first()
    row.deleted_at = utcnow()
    row.deleted_by = acting.id
    row.delete_reason = "crash simulation"
    row.storage_key = None
    row.preview_storage_key = None
    db.commit()  # tombstone durable; object still in the bucket -> orphan
    assert storage.exists(orphan_key)

    result = document_service.reconcile_storage(db, storage)
    db.commit()
    assert result["removed_orphans"] >= 1
    assert not storage.exists(orphan_key)
    assert result["missing_objects"] == 0


def test_abandoned_quarantine_object_is_swept(db, s3_storage):
    """An upload whose database transaction never committed leaves a
    quarantine object referenced by no row; reconciliation removes it."""
    stray = f"{QUARANTINE_PREFIX}abandoned-{uuid.uuid4().hex}.pdf"
    s3_storage.put_bytes(stray, b"%PDF- abandoned")
    result = document_service.reconcile_storage(db, s3_storage)
    assert result["removed_orphans"] >= 1
    assert not s3_storage.exists(stray)


def test_missing_object_behind_live_row_is_reported_not_deleted(
    client,
    db,
    s3_app,
    job_setup,  # noqa: F811
):
    headers, _lead, job = job_setup
    storage = s3_app.state.document_storage
    body = upload(client, headers, job["id"], pdf_bytes(), filename="ok.pdf").json()
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    storage.delete(row.storage_key)  # simulate divergence (restore mismatch)

    result = document_service.reconcile_storage(db, storage)
    db.commit()
    assert result["missing_objects"] >= 1
    db.refresh(row)
    # The row is reported, never deleted by reconciliation.
    assert row.deleted_at is None and row.storage_key is not None


# --- Isolation ------------------------------------------------------------


def test_cross_job_and_cross_customer_access_is_impossible(client, db, s3_app, job_setup):  # noqa: F811
    """Object keys grant no authority: every read resolves document → job →
    lead and a document fetched through any other job 404s."""
    from tests.test_jobs_api import create_job, make_lead

    headers, lead, job = job_setup
    body = upload(client, headers, job["id"], pdf_bytes(), filename="private.pdf").json()

    sibling = create_job(client, headers, lead.id, title="Sibling job")
    stranger = make_lead(db, name="Other Customer")
    foreign = create_job(client, headers, stranger.id, title="Foreign job")

    for other_job in (sibling, foreign):
        assert (
            client.get(
                f"/api/v1/jobs/{other_job['id']}/documents/{body['id']}/download"
            ).status_code
            == 404
        )

    row = db.get(JobDocument, uuid.UUID(body["id"]))
    # Keys are random UUIDs under fixed prefixes — never customer-derived.
    assert row.storage_key.split("/")[1].split(".")[0].isalnum()
