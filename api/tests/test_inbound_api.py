import uuid

from sqlalchemy import func, select

from app.models import Lead, LeadActivity
from tests.conftest import TEST_INBOUND_KEY

WEB_FORM_EVENT = {
    "channel": "web_form",
    "provider": "n8n-webform",
    "external_event_id": "form-123",
    "event_type": "form_submission",
    "sender_name": "Pat Roofer",
    "sender_email": "Pat.Roofer@Example.com",
    "sender_phone": "+1 (555) 010-2000",
    "subject": "Roof quote request",
    "content": "My roof is leaking, please call.",
    "received_at": "2026-08-08T15:00:00Z",
    "metadata": {"form": "contact-us", "page": "/roofing"},
}

CHANNEL_SAMPLES = [
    {
        "channel": "phone_call",
        "event_type": "missed_call",
        "sender_phone": "+15550103000",
        "provider": "twilio",
        "external_event_id": "CA123",
        "content": "Missed call",
    },
    {
        "channel": "sms",
        "sender_phone": "+15550104000",
        "provider": "twilio",
        "external_event_id": "SM456",
        "content": "Do you do driveways?",
    },
    {
        "channel": "whatsapp",
        "sender_phone": "+15550105000",
        "provider": "twilio",
        "external_sender_id": "wa-555",
        "content": "Hola, necesito una cotización",
    },
    {
        "channel": "facebook",
        "external_sender_id": "psid-789",
        "provider": "meta",
        "sender_name": "FB User",
        "content": "Saw your page, need HVAC help",
    },
    {
        "channel": "email",
        "sender_email": "mail@example.com",
        "provider": "gmail",
        "subject": "Quote",
        "content": "Please quote my project",
    },
]


def post_event(client, payload, key="evt-key-000001", api_key=TEST_INBOUND_KEY):
    headers = {"Idempotency-Key": key}
    if api_key is not None:
        headers["X-API-Key"] = api_key
    return client.post("/api/v1/inbound/events", json=payload, headers=headers)


def test_rejects_missing_and_wrong_api_key(client) -> None:
    missing = post_event(client, WEB_FORM_EVENT, api_key=None)
    wrong = post_event(client, WEB_FORM_EVENT, api_key="not-the-key-at-all-0123456789")
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()  # generic, identical error


def test_requires_idempotency_key(client) -> None:
    response = client.post(
        "/api/v1/inbound/events", json=WEB_FORM_EVENT, headers={"X-API-Key": TEST_INBOUND_KEY}
    )
    assert response.status_code == 422


def test_creates_lead_and_activity_with_channel_preserved(client, db) -> None:
    response = post_event(client, WEB_FORM_EVENT)
    assert response.status_code == 200
    body = response.json()
    assert body["lead_created"] is True
    assert body["replayed"] is False

    lead = db.get(Lead, uuid.UUID(body["lead_id"]))
    assert lead.name == "Pat Roofer"
    assert lead.email == "pat.roofer@example.com"  # normalized
    assert lead.phone == "+15550102000"  # E.164 normalized
    assert lead.source == "web_form"
    assert lead.status == "new"
    assert lead.needs_review is False

    activity = db.get(LeadActivity, uuid.UUID(body["activity_id"]))
    assert activity.type == "inbound_request"
    assert activity.channel == "web_form"
    assert activity.direction == "inbound"
    assert activity.provider == "n8n-webform"
    assert activity.external_event_id == "form-123"
    assert activity.occurred_at is not None
    assert "Roof quote request" in activity.content
    assert activity.meta["form"] == "contact-us"
    assert activity.meta["event_type"] == "form_submission"


def test_retry_replays_original_result_without_duplicates(client, db) -> None:
    first = post_event(client, WEB_FORM_EVENT, key="evt-retry-01").json()
    second = post_event(client, WEB_FORM_EVENT, key="evt-retry-01").json()
    assert second["lead_id"] == first["lead_id"]
    assert second["activity_id"] == first["activity_id"]
    assert second["replayed"] is True
    assert db.scalar(select(func.count()).select_from(Lead)) == 1
    assert db.scalar(select(func.count()).select_from(LeadActivity)) == 1


def test_all_channel_samples_accepted(client) -> None:
    for index, sample in enumerate(CHANNEL_SAMPLES):
        response = post_event(client, sample, key=f"evt-chan-{index:04d}")
        assert response.status_code == 200, sample["channel"]


def test_invalid_channel_rejected(client) -> None:
    payload = dict(WEB_FORM_EVENT, channel="carrier_pigeon")
    assert post_event(client, payload).status_code == 422


def test_exact_email_match_attaches_and_fills_missing_phone(client, db, make_user) -> None:
    owner = make_user(email="owner@example.com")
    lead = Lead(name="Existing", email="pat.roofer@example.com", source="manual")
    db.add(lead)
    db.commit()
    _ = owner

    response = post_event(client, WEB_FORM_EVENT, key="evt-match-1").json()
    assert response["lead_created"] is False
    assert response["lead_id"] == str(lead.id)
    db.expire_all()
    assert lead.phone == "+15550102000"  # filled from the event
    assert lead.name == "Existing"  # populated field not overwritten


def test_exact_phone_match(client, db) -> None:
    lead = Lead(name="Phone Person", phone="+15550102000", source="manual")
    db.add(lead)
    db.commit()
    response = post_event(client, WEB_FORM_EVENT, key="evt-match-2").json()
    assert response["lead_created"] is False
    assert response["lead_id"] == str(lead.id)


def test_conflicting_identifiers_create_needs_review_lead(client, db) -> None:
    db.add(Lead(name="Email Lead", email="pat.roofer@example.com", source="manual"))
    db.add(Lead(name="Phone Lead", phone="+15550102000", source="manual"))
    db.commit()
    response = post_event(client, WEB_FORM_EVENT, key="evt-ambig-1").json()
    assert response["lead_created"] is True
    new_lead = db.get(Lead, uuid.UUID(response["lead_id"]))
    assert new_lead.needs_review is True
    # The existing leads were not merged or modified.
    assert db.scalar(select(func.count()).select_from(Lead)) == 3


def test_duplicate_email_candidates_are_ambiguous(client, db) -> None:
    db.add(Lead(name="Dup A", email="pat.roofer@example.com", source="manual"))
    db.add(Lead(name="Dup B", email="pat.roofer@example.com", source="manual", needs_review=True))
    db.commit()
    response = post_event(client, WEB_FORM_EVENT, key="evt-ambig-2").json()
    assert response["lead_created"] is True
    assert db.get(Lead, uuid.UUID(response["lead_id"])).needs_review is True


def test_event_without_identity_still_captured_as_needs_review(client, db) -> None:
    payload = {"channel": "facebook", "external_sender_id": "psid-1", "content": "hi"}
    response = post_event(client, payload, key="evt-noid-1").json()
    assert response["lead_created"] is True
    lead = db.get(Lead, uuid.UUID(response["lead_id"]))
    assert lead.needs_review is True
    assert lead.source == "facebook"


def test_inbound_on_archived_lead_restores_and_flags(client, db) -> None:
    from app.models import utcnow

    lead = Lead(name="Old", email="pat.roofer@example.com", source="manual", archived_at=utcnow())
    db.add(lead)
    db.commit()
    response = post_event(client, WEB_FORM_EVENT, key="evt-arch-1").json()
    assert response["lead_id"] == str(lead.id)
    db.expire_all()
    assert lead.archived_at is None
    assert lead.needs_review is True


def test_oversized_metadata_rejected(client) -> None:
    payload = dict(WEB_FORM_EVENT, metadata={"blob": "x" * 9000})
    assert post_event(client, payload).status_code == 422


def test_phone_without_country_code_is_not_guessed(client, db) -> None:
    payload = {"channel": "sms", "sender_phone": "555-010-9999", "content": "hi"}
    response = post_event(client, payload, key="evt-phone-1").json()
    lead = db.get(Lead, uuid.UUID(response["lead_id"]))
    assert lead.phone == "5550109999"  # digits kept, no invented +1
