"""Uploaded paperwork: content proofs, quarantine/scan/promote, serving,
ownership, moves, tombstones and reconciliation."""

import io
import logging
import uuid

import pytest
from PIL import Image

from app.config import get_settings
from app.models import JobDocument, Lead
from app.services import documents as document_service
from app.services.scanner import EICAR_SIGNATURE, ScannerUnavailable
from tests.conftest import login
from tests.test_jobs_api import create_job, make_lead, owner_session


def png_bytes(size=(80, 60), color=(200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_bytes(text="Hello invoice") -> bytes:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=LETTER)
    page.drawString(72, 720, text)
    page.showPage()
    page.save()
    return buffer.getvalue()


def upload(client, headers, job_id, data: bytes, filename="file.bin", **fields):
    return client.post(
        f"/api/v1/jobs/{job_id}/documents",
        files={"file": (filename, data, fields.pop("content_type", "application/octet-stream"))},
        data=fields,
        headers=headers,
    )


@pytest.fixture()
def job_setup(client, db, make_user):
    headers = owner_session(client, make_user)
    lead = make_lead(db)
    job = create_job(client, headers, lead.id)
    return headers, lead, job


def test_valid_pdf_upload_is_scanned_promoted_and_downloadable(client, db, job_setup):
    headers, _lead, job = job_setup
    response = upload(
        client, headers, job["id"], pdf_bytes(), filename="quote.pdf", category="quote"
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scan_state"] == "clean"
    assert body["content_type"] == "application/pdf"

    row = db.get(JobDocument, uuid.UUID(body["id"]))
    assert row.storage_key is not None and row.quarantine_key is None

    download = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download")
    assert download.status_code == 200
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-disposition"].startswith("attachment")
    assert download.content.startswith(b"%PDF-")
    # PDFs get no inline preview — only server-generated images do.
    preview = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/preview")
    assert preview.status_code == 404


def test_image_upload_is_reencoded_with_preview(client, db, job_setup):
    headers, _lead, job = job_setup
    raw = png_bytes()
    response = upload(client, headers, job["id"], raw, filename="roof.png", category="photo")
    assert response.status_code == 201
    body = response.json()
    assert body["scan_state"] == "clean"
    assert body["has_preview"] is True

    download = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download")
    assert download.status_code == 200
    # Re-encoded server-side: bytes are the normalized image, not the upload.
    preview = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/jpeg"


def test_spoofed_and_active_content_is_rejected(client, db, job_setup):
    headers, _lead, job = job_setup
    html = b"<html><script>alert(1)</script></html>"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>'
    corrupt_image = b"\x89PNG\r\n\x1a\n" + b"garbage-not-a-real-png"
    executable = b"MZ\x90\x00" + b"\x00" * 64
    archive = b"PK\x03\x04" + b"\x00" * 64

    for payload, name in (
        (html, "page.pdf"),  # spoofed extension
        (svg, "logo.svg"),
        (corrupt_image, "photo.png"),
        (executable, "tool.exe"),
        (archive, "docs.zip"),
    ):
        response = upload(
            client, headers, job["id"], payload, filename=name, content_type="image/png"
        )
        assert response.status_code == 400, name

    # Animated images are refused even in an accepted format.
    frames = [Image.new("RGB", (40, 40), (i * 40, 0, 0)) for i in range(3)]
    animated = io.BytesIO()
    frames[0].save(animated, format="WEBP", save_all=True, append_images=frames[1:])
    response = upload(client, headers, job["id"], animated.getvalue(), filename="anim.webp")
    assert response.status_code == 400
    assert "Animated" in response.json()["detail"]


def test_dangerous_pdfs_are_rejected(client, db, job_setup):
    headers, _lead, job = job_setup
    from pypdf import PdfReader, PdfWriter

    def build(mutate) -> bytes:
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(pdf_bytes())))
        mutate(writer)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    encrypted = build(lambda writer: writer.encrypt("secret"))
    with_js = build(lambda writer: writer.add_js("app.alert('x');"))
    with_attachment = build(lambda writer: writer.add_attachment("inner.txt", b"hidden"))

    for payload, label in (
        (encrypted, "encrypted"),
        (with_js, "javascript"),
        (with_attachment, "embedded"),
    ):
        response = upload(client, headers, job["id"], payload, filename=f"{label}.pdf")
        assert response.status_code == 400, label


def test_infected_file_stays_quarantined(client, db, job_setup):
    headers, _lead, job = job_setup
    # EICAR inside an otherwise-valid PDF: content checks pass, the scanner
    # must still catch it and the file must never become downloadable.
    payload = pdf_bytes() + b"\n%" + EICAR_SIGNATURE
    response = upload(client, headers, job["id"], payload, filename="invoice.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["scan_state"] == "infected"

    row = db.get(JobDocument, uuid.UUID(body["id"]))
    assert row.storage_key is None and row.quarantine_key is not None
    download = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download")
    assert download.status_code == 404


def test_scanner_outage_fails_closed(client, db, app, job_setup):
    headers, _lead, job = job_setup

    class DownScanner:
        def scan_bytes(self, data):
            raise ScannerUnavailable("connection refused")

        def health(self):
            return {"backend": "down", "status": "error"}

    app.state.document_scanner = DownScanner()
    response = upload(client, headers, job["id"], pdf_bytes(), filename="contract.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["scan_state"] == "failed"
    download = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download")
    assert download.status_code == 404


def test_rescan_after_outage_promotes_and_serves(client, db, app, job_setup):
    """The recovery path for fail-closed outages: a failed document can be
    rescanned once the scanner is back, and a clean result promotes it."""
    headers, _lead, job = job_setup

    class DownScanner:
        def scan_bytes(self, data):
            raise ScannerUnavailable("connection refused")

    working_scanner = app.state.document_scanner
    app.state.document_scanner = DownScanner()
    body = upload(client, headers, job["id"], pdf_bytes(), filename="permit.pdf").json()
    assert body["scan_state"] == "failed"

    app.state.document_scanner = working_scanner
    rescanned = client.post(
        f"/api/v1/jobs/{job['id']}/documents/{body['id']}/rescan", headers=headers
    )
    assert rescanned.status_code == 200, rescanned.text
    assert rescanned.json()["scan_state"] == "clean"
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    assert row.storage_key is not None and row.quarantine_key is None
    download = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download")
    assert download.status_code == 200


def test_rescan_can_still_find_an_infection(client, db, app, job_setup):
    headers, _lead, job = job_setup

    class DownScanner:
        def scan_bytes(self, data):
            raise ScannerUnavailable("connection refused")

    working_scanner = app.state.document_scanner
    app.state.document_scanner = DownScanner()
    payload = pdf_bytes() + b"\n%" + EICAR_SIGNATURE
    body = upload(client, headers, job["id"], payload, filename="invoice.pdf").json()
    assert body["scan_state"] == "failed"

    app.state.document_scanner = working_scanner
    rescanned = client.post(
        f"/api/v1/jobs/{job['id']}/documents/{body['id']}/rescan", headers=headers
    )
    assert rescanned.status_code == 200
    assert rescanned.json()["scan_state"] == "infected"
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    assert row.storage_key is None and row.quarantine_key is not None
    download = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download")
    assert download.status_code == 404


def test_rescan_refuses_ineligible_documents_and_strangers(client, job_setup):
    headers, _lead, job = job_setup
    clean = upload(client, headers, job["id"], pdf_bytes(), filename="quote.pdf").json()
    assert clean["scan_state"] == "clean"
    refused = client.post(
        f"/api/v1/jobs/{job['id']}/documents/{clean['id']}/rescan", headers=headers
    )
    assert refused.status_code == 409
    unauthenticated = client.post(f"/api/v1/jobs/{job['id']}/documents/{clean['id']}/rescan")
    assert unauthenticated.status_code in (401, 403)


def test_streamed_oversize_upload_is_rejected_before_parsing(client, job_setup):
    headers, _lead, job = job_setup
    huge = b"x" * (document_service.MAX_UPLOAD_BYTES + 128 * 1024)
    response = upload(client, headers, job["id"], huge, filename="huge.pdf")
    assert response.status_code == 413


def test_document_moves_only_within_the_same_customer(client, db, job_setup):
    headers, lead, job = job_setup
    sibling = create_job(client, headers, lead.id, title="Second job")
    stranger = make_lead(db, name="Stranger")
    foreign = create_job(client, headers, stranger.id, title="Foreign job")

    uploaded = upload(client, headers, job["id"], png_bytes(), filename="permit.png").json()

    moved = client.post(
        f"/api/v1/jobs/{job['id']}/documents/{uploaded['id']}/move",
        json={"target_job_id": sibling["id"]},
        headers=headers,
    )
    assert moved.status_code == 200
    assert moved.json()["job_id"] == sibling["id"]

    refused = client.post(
        f"/api/v1/jobs/{sibling['id']}/documents/{uploaded['id']}/move",
        json={"target_job_id": foreign["id"]},
        headers=headers,
    )
    assert refused.status_code == 409


def test_deleted_upload_leaves_tombstone_and_removes_object(client, db, app, job_setup):
    headers, _lead, job = job_setup
    uploaded = upload(client, headers, job["id"], png_bytes(), filename="old.png").json()
    row = db.get(JobDocument, uuid.UUID(uploaded["id"]))
    storage = app.state.document_storage
    key = row.storage_key
    assert storage.exists(key)

    deleted = client.post(
        f"/api/v1/jobs/{job['id']}/documents/{uploaded['id']}/delete",
        json={"reason": "customer sent a duplicate"},
        headers=headers,
    )
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["deleted_at"] is not None
    db.refresh(row)
    assert row.delete_reason == "customer sent a duplicate"
    assert row.storage_key is None
    assert not storage.exists(key)
    assert (
        client.get(f"/api/v1/jobs/{job['id']}/documents/{uploaded['id']}/download").status_code
        == 404
    )


def test_unauthorized_users_cannot_reach_documents(client, db, make_user, job_setup):
    headers, _lead, job = job_setup
    uploaded = upload(client, headers, job["id"], png_bytes(), filename="private.png").json()
    client.cookies.clear()

    # Unauthenticated: nothing.
    assert client.get(f"/api/v1/jobs/{job['id']}/documents").status_code == 401
    assert (
        client.get(f"/api/v1/jobs/{job['id']}/documents/{uploaded['id']}/download").status_code
        == 401
    )

    # A team member with no relationship to the customer: not even existence.
    make_user(email="outsider@example.com", role="team_member")
    login(client, "outsider@example.com")
    assert client.get(f"/api/v1/jobs/{job['id']}/documents").status_code == 404
    assert (
        client.get(f"/api/v1/jobs/{job['id']}/documents/{uploaded['id']}/download").status_code
        == 404
    )


def test_failed_transaction_exposes_no_orphan(client, db, app, job_setup, monkeypatch):
    headers, _lead, job = job_setup
    storage = app.state.document_storage
    before = set(storage.list_keys("quarantine/"))

    # Force the durable insert to fail AFTER the quarantine object is written,
    # mirroring a commit failure between the object write and the row commit.
    from app.services import documents as documents_module

    real_validate = documents_module.validate_content

    def store_and_fail(db_session, storage_arg, scanner, user, job_arg, *, raw, **kwargs):
        stored, _mime, _preview = real_validate(raw)
        from app.services.storage import QUARANTINE_PREFIX, new_object_key

        storage_arg.put_bytes(new_object_key(QUARANTINE_PREFIX, ".bin"), stored)
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(documents_module, "store_upload", store_and_fail)
    monkeypatch.setattr("app.api.v1.jobs.document_service.store_upload", store_and_fail)
    with pytest.raises(RuntimeError):
        upload(client, headers, job["id"], png_bytes(), filename="orphan.png")

    orphans = set(storage.list_keys("quarantine/")) - before
    assert orphans  # the object exists but no row references it

    # No API path serves quarantine objects; reconciliation sweeps them.
    result = document_service.reconcile_storage(db, storage)
    assert result["removed_orphans"] >= 1
    assert not (set(storage.list_keys("quarantine/")) & orphans)


def test_nothing_sensitive_reaches_logs(client, caplog, job_setup):
    headers, _lead, job = job_setup
    caplog.set_level(logging.DEBUG)
    secret_name = "TOP-SECRET-CUSTOMER-FILE-NAME.pdf"
    upload(client, headers, job["id"], pdf_bytes("confidential body"), filename=secret_name)
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert secret_name not in joined
    assert "confidential body" not in joined
    assert get_settings().internal_bff_key not in joined


def test_documents_group_by_job_and_never_leak_across_jobs(client, db, job_setup):
    headers, lead, job = job_setup
    second = create_job(client, headers, lead.id, title="Second")
    upload(client, headers, job["id"], png_bytes(), filename="a.png")
    upload(client, headers, second["id"], png_bytes(), filename="b.png")

    first_docs = client.get(f"/api/v1/jobs/{job['id']}/documents").json()
    second_docs = client.get(f"/api/v1/jobs/{second['id']}/documents").json()
    assert len(first_docs) == 1 and len(second_docs) == 1
    assert first_docs[0]["job_id"] == job["id"]
    assert second_docs[0]["job_id"] == second["id"]

    # Ownership resolves document → job → exactly this customer.
    row = db.get(JobDocument, uuid.UUID(first_docs[0]["id"]))
    from app.models import Job as JobModel

    parent = db.get(JobModel, row.job_id)
    assert db.get(Lead, parent.lead_id).id == lead.id
