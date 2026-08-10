"""Customer capabilities, the internal BFF endpoints and document email."""

import logging
import uuid

import pytest

from app.config import get_settings
from app.models import CommercialDocument, CommercialDocumentVersion, EmailDelivery
from app.services import document_access
from app.services import document_email as email_service
from app.services.messaging import get_settings_row
from tests.conftest import email_key_headers, internal_headers
from tests.test_commercial_api import LINES, issue, make_quote
from tests.test_jobs_api import create_job, make_lead, owner_session


@pytest.fixture()
def issued_quote(client, db, make_user):
    headers = owner_session(client, make_user)
    lead = make_lead(db, name="Pat Customer", email="pat@customer.test")
    job = create_job(client, headers, lead.id)
    quote = make_quote(client, headers, job["id"])
    issued = issue(client, headers, job["id"], quote["id"])
    version = (
        db.query(CommercialDocumentVersion)
        .filter_by(document_id=uuid.UUID(quote["id"]), superseded_at=None)
        .one()
    )
    return headers, lead, job, issued, version


def grant(db, version, purpose="quote_response"):
    settings = get_settings()
    capability, raw = document_access.issue_capability(
        db, settings, get_settings_row(db), version.id, purpose=purpose
    )
    db.commit()
    return capability, raw


def test_capability_grants_exactly_one_version(client, db, issued_quote):
    _headers, lead, job, quote, version = issued_quote
    _capability, raw = grant(db, version)

    info = client.post(
        "/api/v1/internal/documents/info", json={"token": raw}, headers=internal_headers()
    )
    assert info.status_code == 200, info.text
    body = info.json()
    assert body["number"] == quote["number"]
    assert body["can_respond"] is True
    # Minimal exposure: business branding + snapshot; no ids, no staff, no
    # storage keys, no other documents.
    text = info.text
    assert str(lead.id) not in text
    assert str(job["id"]) not in text
    assert "storage_key" not in text
    assert "internal_notes" not in text

    # Viewing marked the quote viewed — viewing is never acceptance.
    document = db.get(CommercialDocument, uuid.UUID(quote["id"]))
    db.refresh(document)
    assert document.status == "viewed"
    assert document.responded_at is None

    pdf = client.post(
        "/api/v1/internal/documents/pdf", json={"token": raw}, headers=internal_headers()
    )
    assert pdf.status_code == 200
    assert pdf.headers["x-content-type-options"] == "nosniff"


def test_capability_requires_bff_credential_and_valid_state(client, db, issued_quote):
    _headers, _lead, _job, _quote, version = issued_quote
    capability, raw = grant(db, version)

    # No BFF key, no service — regardless of a valid token.
    assert client.post("/api/v1/internal/documents/info", json={"token": raw}).status_code == 401

    # Garbage token: not found.
    bad = client.post(
        "/api/v1/internal/documents/info",
        json={"token": "A" * 40},
        headers=internal_headers(),
    )
    assert bad.status_code == 404

    # Revoked: gone.
    capability.revoked_at = capability.created_at
    db.commit()
    revoked = client.post(
        "/api/v1/internal/documents/info", json={"token": raw}, headers=internal_headers()
    )
    assert revoked.status_code == 410


def test_superseded_version_rejects_response(client, db, issued_quote):
    headers, _lead, job, quote, version = issued_quote
    _capability, raw = grant(db, version)

    # Correct and re-issue: v1 capability may not respond any more.
    client.patch(
        f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}",
        json={"lines": LINES, "discount_bp": 1000},
        headers=headers,
    )
    issue(client, headers, job["id"], quote["id"])

    stale = client.post(
        "/api/v1/internal/documents/respond",
        json={"token": raw, "accept": True, "typed_name": "Pat Customer"},
        headers=internal_headers(),
    )
    assert stale.status_code == 409


def test_quote_response_via_capability_is_idempotent(client, db, issued_quote):
    _headers, _lead, _job, quote, version = issued_quote
    _capability, raw = grant(db, version)

    payload = {"token": raw, "accept": True, "typed_name": "Pat Customer"}
    first = client.post(
        "/api/v1/internal/documents/respond", json=payload, headers=internal_headers()
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "accepted"

    replay = client.post(
        "/api/v1/internal/documents/respond", json=payload, headers=internal_headers()
    )
    assert replay.status_code == 200
    assert replay.json()["status"] == "accepted"

    flipped = client.post(
        "/api/v1/internal/documents/respond",
        json={"token": raw, "accept": False, "typed_name": "Pat Customer"},
        headers=internal_headers(),
    )
    assert flipped.status_code == 409

    document = db.get(CommercialDocument, uuid.UUID(quote["id"]))
    db.refresh(document)
    assert document.response_snapshot_sha256 is not None
    assert document.response_name == "Pat Customer"


def test_view_capability_cannot_respond(client, db, issued_quote):
    _headers, _lead, _job, _quote, version = issued_quote
    _capability, raw = grant(db, version, purpose="view")
    refused = client.post(
        "/api/v1/internal/documents/respond",
        json={"token": raw, "accept": True, "typed_name": "Pat"},
        headers=internal_headers(),
    )
    assert refused.status_code == 403


# --- email ---------------------------------------------------------------


def send_email(client, headers, job_id, document_id, key="send-key-0001"):
    return client.post(
        f"/api/v1/jobs/{job_id}/commercial/{document_id}/send",
        json={"recipient": "pat@customer.test", "send_key": key},
        headers=headers,
    )


def test_email_record_commits_before_any_transport(client, db, issued_quote):
    headers, _lead, job, quote, _version = issued_quote
    response = send_email(client, headers, job["id"], quote["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    # Durable and pending: nothing has contacted a provider (the CRM cannot
    # even do so — only n8n holds the credential).
    assert body["status"] == "pending"
    assert body["from_address"] == "documents@crm.test"
    assert body["attempts"] == 0
    row = db.get(EmailDelivery, uuid.UUID(body["id"]))
    assert row is not None and row.status == "pending"


def test_email_send_is_deduplicated(client, db, issued_quote):
    headers, _lead, job, quote, _version = issued_quote
    first = send_email(client, headers, job["id"], quote["id"], key="dup-key-01")
    retry = send_email(client, headers, job["id"], quote["id"], key="dup-key-01")
    assert first.json()["id"] == retry.json()["id"]
    assert db.query(EmailDelivery).count() == 1

    # A deliberate second send (new key) is a new delivery.
    second = send_email(client, headers, job["id"], quote["id"], key="dup-key-02")
    assert second.json()["id"] != first.json()["id"]


def test_claim_report_lifecycle_and_unknown_is_terminal(client, db, issued_quote):
    headers, _lead, job, quote, _version = issued_quote
    delivery_id = send_email(client, headers, job["id"], quote["id"]).json()["id"]

    claimed = client.post(
        "/api/v1/inbound/document-emails/claim", json={}, headers=email_key_headers()
    )
    assert claimed.status_code == 200, claimed.text
    work = claimed.json()
    assert len(work) == 1
    assert work[0]["id"] == delivery_id
    assert work[0]["from_address"] == "documents@crm.test"

    # A second overlapping claim gets nothing (leased).
    again = client.post(
        "/api/v1/inbound/document-emails/claim", json={}, headers=email_key_headers()
    )
    assert again.json() == []

    # The exact immutable PDF is fetchable while claimed.
    pdf = client.get(
        f"/api/v1/inbound/document-emails/{delivery_id}/pdf", headers=email_key_headers()
    )
    assert pdf.status_code == 200

    submitted = client.post(
        "/api/v1/inbound/document-emails/report",
        json={"delivery_id": delivery_id, "outcome": "submitted", "provider_message_id": "m1"},
        headers=email_key_headers(),
    )
    assert submitted.json()["status"] == "submitted"

    # Submission is NOT delivery; a trusted callback upgrades it.
    delivered = client.post(
        "/api/v1/inbound/document-emails/report",
        json={"delivery_id": delivery_id, "outcome": "delivered"},
        headers=email_key_headers(),
    )
    assert delivered.json()["status"] == "delivered"

    # Terminal: no regression to unknown/failed afterwards.
    for outcome in ("unknown", "failed"):
        refused = client.post(
            "/api/v1/inbound/document-emails/report",
            json={"delivery_id": delivery_id, "outcome": outcome},
            headers=email_key_headers(),
        )
        assert refused.status_code == 409


def test_delivered_requires_prior_submission(client, db, issued_quote):
    headers, _lead, job, quote, _version = issued_quote
    delivery_id = send_email(client, headers, job["id"], quote["id"]).json()["id"]
    client.post("/api/v1/inbound/document-emails/claim", json={}, headers=email_key_headers())
    premature = client.post(
        "/api/v1/inbound/document-emails/report",
        json={"delivery_id": delivery_id, "outcome": "delivered"},
        headers=email_key_headers(),
    )
    assert premature.status_code == 409


def test_unknown_outcome_is_not_retried_and_creates_attention(client, db, issued_quote):
    headers, _lead, job, quote, _version = issued_quote
    delivery_id = send_email(client, headers, job["id"], quote["id"]).json()["id"]
    client.post("/api/v1/inbound/document-emails/claim", json={}, headers=email_key_headers())
    client.post(
        "/api/v1/inbound/document-emails/report",
        json={"delivery_id": delivery_id, "outcome": "unknown", "failure_class": "timeout"},
        headers=email_key_headers(),
    )
    row = db.get(EmailDelivery, uuid.UUID(delivery_id))
    db.refresh(row)
    assert row.status == "unknown"

    # Not claimable again: unknown is never retried automatically.
    reclaim = client.post(
        "/api/v1/inbound/document-emails/claim", json={}, headers=email_key_headers()
    )
    assert reclaim.json() == []
    assert row in email_service.attention_deliveries(db)


def test_stale_claims_recover_before_submission(client, db, issued_quote):
    from datetime import timedelta

    from app.models import utcnow

    headers, _lead, job, quote, _version = issued_quote
    delivery_id = send_email(client, headers, job["id"], quote["id"]).json()["id"]
    client.post("/api/v1/inbound/document-emails/claim", json={}, headers=email_key_headers())

    row = db.get(EmailDelivery, uuid.UUID(delivery_id))
    db.refresh(row)
    row.claimed_at = utcnow() - timedelta(minutes=email_service.CLAIM_LEASE_MINUTES + 5)
    db.commit()

    # The next claim run recovers the abandoned lease and re-claims it once.
    reclaim = client.post(
        "/api/v1/inbound/document-emails/claim", json={}, headers=email_key_headers()
    )
    assert [item["id"] for item in reclaim.json()] == [delivery_id]
    db.refresh(row)
    assert row.attempts == 2


def test_sender_cannot_be_overridden_and_unconfigured_sender_disables(
    client, db, issued_quote, monkeypatch
):
    headers, _lead, job, quote, _version = issued_quote
    # The API schema has no from-address field at all; a smuggled one is
    # ignored by pydantic. Prove the stored record uses the configured sender.
    response = client.post(
        f"/api/v1/jobs/{job['id']}/commercial/{quote['id']}/send",
        json={
            "recipient": "pat@customer.test",
            "send_key": "override-key-01",
            "from_address": "attacker@evil.test",
            "from": "attacker@evil.test",
        },
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["from_address"] == "documents@crm.test"

    # With no verified sender configured, sending is refused with a clear
    # message and nothing falls back to an unknown address.
    settings = get_settings()
    monkeypatch.setattr(settings, "document_email_from_address", "")
    refused = send_email(client, headers, job["id"], quote["id"], key="noconf-key-1")
    assert refused.status_code == 409
    assert "not configured" in refused.json()["detail"]


def test_template_allowlist_is_enforced(client, db, issued_quote):
    with pytest.raises(email_service.EmailError, match="Unknown template variables"):
        email_service.validate_template("Hello {{customer_name}} {{__class__}}")
    with pytest.raises(email_service.EmailError):
        email_service.validate_template("{{secret_pepper}}")
    assert email_service.validate_template("Hi {{customer_name}}")

    rendered = email_service.render_template(
        "Hi {{customer_name}}, total {{document_total}}",
        {"customer_name": "<b>Pat</b>", "document_total": "12.00 USD"},
    )
    # Values are inserted as text; nothing is evaluated.
    assert rendered == "Hi <b>Pat</b>, total 12.00 USD"


def test_no_capability_or_customer_data_in_logs(client, db, caplog, issued_quote):
    headers, _lead, job, quote, version = issued_quote
    caplog.set_level(logging.DEBUG)
    _capability, raw = grant(db, version)
    client.post("/api/v1/internal/documents/info", json={"token": raw}, headers=internal_headers())
    client.post(
        "/api/v1/internal/documents/respond",
        json={"token": raw, "accept": True, "typed_name": "Pat Customer"},
        headers=internal_headers(),
    )
    send_email(client, headers, job["id"], quote["id"], key="log-key-0001")

    joined = " ".join(record.getMessage() for record in caplog.records)
    assert raw not in joined
    assert "pat@customer.test" not in joined
    assert "Pat Customer" not in joined
    assert get_settings().internal_bff_key not in joined
    assert get_settings().document_email_api_key not in joined
