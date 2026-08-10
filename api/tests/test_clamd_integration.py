"""Real-ClamAV integration proofs for the production scanner backend.

The default suite exercises the scanning pipeline with the stub/fake scanners;
these tests run the SAME pipeline against a real clamd daemon (the compose
`clamav` service) and prove the production behaviors that only a real daemon
can prove: INSTREAM protocol correctness, real signature detection, and
fail-closed handling for timeouts and outages.

Gated so the default suite stays light. Publish clamd to the host, e.g.:

    docker compose --profile scanning up -d clamav
    docker run -d --name crm-verify-socat --network service_crm_default \
        -p 127.0.0.1:3310:3310 alpine/socat \
        tcp-listen:3310,fork,reuseaddr tcp:clamav:3310
    CLAMD_TEST_HOST=127.0.0.1 uv run pytest tests/test_clamd_integration.py

For the end-to-end infected-upload test, additionally install a harmless
custom verification signature (real ClamAV only flags EICAR at the start of
a tiny file, never appended inside a PDF), then remove it afterwards:

    MARKER="CRM-VERIFY-INFECTED-MARKER-7f3a9c"
    HEX=$(printf %s "$MARKER" | xxd -p | tr -d '\n')
    docker exec service_crm-clamav-1 sh -c \
        "printf 'CrmVerify.TestMarker:0:*:%s\n' $HEX > /var/lib/clamav/crm-verify.ndb"
    docker exec service_crm-clamav-1 clamdscan --reload
    CLAMD_TEST_HOST=127.0.0.1 CLAMD_TEST_MARKER="$MARKER" \
        uv run pytest tests/test_clamd_integration.py
    docker exec service_crm-clamav-1 sh -c "rm /var/lib/clamav/crm-verify.ndb"
    docker exec service_crm-clamav-1 clamdscan --reload

Only the harmless EICAR test string and that marker are ever scanned — no
real malware is used anywhere.
"""

import os
import socket
import socketserver
import threading
import uuid

import pytest

from app.models import JobDocument
from app.services import documents as document_service
from app.services.scanner import (
    EICAR_SIGNATURE,
    ClamdScanner,
    ScannerUnavailable,
)
from tests.test_job_documents import job_setup, pdf_bytes, upload  # noqa: F401 - fixture

CLAMD_TEST_HOST = os.environ.get("CLAMD_TEST_HOST")
CLAMD_TEST_PORT = int(os.environ.get("CLAMD_TEST_PORT", "3310"))

pytestmark = pytest.mark.skipif(
    not CLAMD_TEST_HOST,
    reason="CLAMD_TEST_HOST not set; real-ClamAV integration tests need a reachable clamd",
)


def real_scanner(timeout: float = 30.0) -> ClamdScanner:
    return ClamdScanner(CLAMD_TEST_HOST, CLAMD_TEST_PORT, timeout=timeout)


def _closed_port() -> int:
    """A port that was just bound and released — nothing listens on it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --- Daemon-level truths -------------------------------------------------


def test_daemon_answers_ping_and_version():
    scanner = real_scanner()
    assert scanner.health()["status"] == "ok"
    version = scanner._command(None, b"zVERSION\x00")
    assert version.startswith("ClamAV"), version
    print(f"\nclamd version: {version}")


def test_clean_bytes_pass():
    result = real_scanner().scan_bytes(pdf_bytes())
    assert result.clean is True


def test_eicar_is_detected_with_signature_name():
    result = real_scanner().scan_bytes(EICAR_SIGNATURE)
    assert result.clean is False
    assert "eicar" in result.detail.lower()


def test_large_payload_streams_in_chunks():
    # > one 64 KiB chunk, exercising the INSTREAM chunked framing.
    payload = pdf_bytes() + b"\n%" + os.urandom(512 * 1024).hex().encode()
    assert real_scanner().scan_bytes(payload).clean is True


def test_unreachable_daemon_raises_unavailable():
    scanner = ClamdScanner("127.0.0.1", _closed_port(), timeout=2.0)
    with pytest.raises(ScannerUnavailable):
        scanner.scan_bytes(b"anything")


def test_silent_daemon_times_out_as_unavailable():
    """A server that accepts and then never responds must surface as an
    outage (socket timeout), never as a clean result."""

    class Silent(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.recv(1)  # accept, read a byte, then hang
            threading.Event().wait(5.0)

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Silent) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            scanner = ClamdScanner(*server.server_address, timeout=1.0)
            with pytest.raises(ScannerUnavailable):
                scanner.scan_bytes(b"anything")
        finally:
            server.shutdown()


# --- Full upload pipeline against the real daemon ------------------------


def test_clean_upload_is_promoted_by_real_clamd(client, db, app, job_setup):  # noqa: F811
    headers, _lead, job = job_setup
    app.state.document_scanner = real_scanner()
    response = upload(client, headers, job["id"], pdf_bytes(), filename="contract.pdf")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scan_state"] == "clean"
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    assert row.storage_key is not None and row.quarantine_key is None
    assert app.state.document_storage.exists(row.storage_key)
    download = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download")
    assert download.status_code == 200


def test_appended_eicar_is_not_flagged_by_real_clamd():
    """Documented real-daemon behavior: per the EICAR specification, the test
    string is only detected at the start of a small file. Appended to a real
    PDF, real ClamAV reports clean (the dev stub's substring match is
    stricter here). Real malware inside PDFs is matched by real signatures —
    the marker-gated test below proves that path end to end."""
    payload = pdf_bytes() + b"\n%" + EICAR_SIGNATURE
    assert real_scanner().scan_bytes(payload).clean is True


@pytest.mark.skipif(
    not os.environ.get("CLAMD_TEST_MARKER"),
    reason="CLAMD_TEST_MARKER not set; needs the crm-verify.ndb signature loaded in clamd",
)
def test_infected_upload_stays_quarantined_by_real_clamd(client, db, app, job_setup):  # noqa: F811
    """End-to-end infected path against real clamd. Setup installs a custom
    verification signature matching CLAMD_TEST_MARKER (see module docstring);
    a valid PDF carrying the marker must be flagged, stay quarantined and
    never be served."""
    headers, _lead, job = job_setup
    app.state.document_scanner = real_scanner()
    payload = pdf_bytes() + b"\n%" + os.environ["CLAMD_TEST_MARKER"].encode()
    response = upload(client, headers, job["id"], payload, filename="invoice.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["scan_state"] == "infected"
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    assert row.storage_key is None and row.quarantine_key is not None
    assert row.quarantine_key.startswith("quarantine/")
    assert app.state.document_storage.exists(row.quarantine_key)
    download = client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download")
    assert download.status_code == 404


def test_raw_eicar_file_is_rejected_by_content_validation_before_scanning(
    client,
    db,
    app,
    job_setup,  # noqa: F811
):
    """Defense in depth: a bare EICAR file is not a valid PDF/image, so the
    content allowlist refuses it before any scanner is consulted."""
    headers, _lead, job = job_setup
    app.state.document_scanner = real_scanner()
    response = upload(client, headers, job["id"], EICAR_SIGNATURE, filename="eicar.com")
    assert response.status_code == 400


def test_outage_fails_closed_and_rescan_with_real_clamd_promotes(
    client,
    db,
    app,
    job_setup,  # noqa: F811
):
    """Timeout/unavailable during upload -> failed + quarantined + never
    served; a later rescan against the recovered daemon promotes it."""
    headers, _lead, job = job_setup
    app.state.document_scanner = ClamdScanner("127.0.0.1", _closed_port(), timeout=2.0)
    response = upload(client, headers, job["id"], pdf_bytes(), filename="permit.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["scan_state"] == "failed"
    row = db.get(JobDocument, uuid.UUID(body["id"]))
    assert row.storage_key is None and row.quarantine_key is not None
    assert (
        client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download").status_code == 404
    )

    # No API route exposes rescan yet (documents.rescan is service-only), so
    # recovery is proven at the service layer against the recovered daemon.
    document_service.rescan(db, app.state.document_storage, real_scanner(), row)
    db.commit()
    assert row.scan_state == "clean"
    assert row.storage_key is not None and row.quarantine_key is None
    assert (
        client.get(f"/api/v1/jobs/{job['id']}/documents/{body['id']}/download").status_code == 200
    )
