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


def test_chunked_oversized_body_rejected_before_parsing(app, monkeypatch) -> None:
    """A chunked body with no Content-Length must be cut off while streaming:
    the endpoint and the JSON parser must never run."""
    import anyio
    import httpx

    from app.api.v1 import inbound as inbound_module

    executed = []
    original = inbound_module.process_inbound_event
    monkeypatch.setattr(
        inbound_module,
        "process_inbound_event",
        lambda *args, **kwargs: executed.append(1) or original(*args, **kwargs),
    )

    chunks_sent = []

    async def oversized_chunks():
        # 100 x 1KB chunks, streamed without Content-Length.
        for index in range(100):
            chunks_sent.append(index)
            yield b"x" * 1024

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/inbound/events",
                content=oversized_chunks(),
                headers={
                    "X-API-Key": TEST_INBOUND_KEY,
                    "Idempotency-Key": "evt-chunked-1",
                    "Content-Type": "application/json",
                },
            )

    response = anyio.run(run)
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large."}
    assert executed == []  # endpoint never ran
    # Rejected mid-stream, not after buffering everything.
    assert len(chunks_sent) < 100


def test_oversized_body_rejected_end_to_end(client) -> None:
    payload = dict(WEB_FORM_EVENT, content="x" * (65 * 1024))
    assert post_event(client, payload, key="evt-huge-1").status_code == 413


def test_external_identity_reuses_lead_across_messages(client, db) -> None:
    first = {
        "channel": "facebook",
        "provider": "meta",
        "external_sender_id": "psid-repeat",
        "sender_name": "FB Person",
        "content": "first message",
    }
    second = dict(first, content="second message")
    r1 = post_event(client, first, key="evt-fb-0001").json()
    r2 = post_event(client, second, key="evt-fb-0002").json()

    assert r1["lead_created"] is True
    assert r2["lead_created"] is False
    assert r2["lead_id"] == r1["lead_id"]
    assert db.scalar(select(func.count()).select_from(Lead)) == 1
    assert db.scalar(select(func.count()).select_from(LeadActivity)) == 2
    lead = db.get(Lead, uuid.UUID(r1["lead_id"]))
    assert lead.needs_review is True  # first provider-only contact is flagged


def test_external_identity_fills_contact_details_later(client, db) -> None:
    base = {"channel": "whatsapp", "provider": "meta", "external_sender_id": "wa-111"}
    r1 = post_event(client, dict(base, content="hola"), key="evt-wa-0001").json()
    lead = db.get(Lead, uuid.UUID(r1["lead_id"]))
    assert lead.phone is None

    r2 = post_event(
        client,
        dict(base, content="mi número", sender_phone="+15550106000", sender_name="WA Person"),
        key="evt-wa-0002",
    ).json()
    assert r2["lead_id"] == r1["lead_id"]
    db.expire_all()
    assert lead.phone == "+15550106000"
    assert lead.name == "WA Person"


def test_identity_and_contact_conflict_flags_review_without_merging(client, db) -> None:
    other = Lead(name="Existing Email Lead", email="conflict@example.com", source="manual")
    db.add(other)
    db.commit()

    base = {"channel": "facebook", "provider": "meta", "external_sender_id": "psid-conflict"}
    r1 = post_event(client, dict(base, content="hello"), key="evt-fbc-0001").json()
    identity_lead_id = r1["lead_id"]
    assert identity_lead_id != str(other.id)

    # Same provider identity now claims an email owned by a different lead.
    r2 = post_event(
        client,
        dict(base, content="mail me", sender_email="conflict@example.com"),
        key="evt-fbc-0002",
    ).json()
    assert r2["lead_id"] == identity_lead_id  # no silent merge
    identity_lead = db.get(Lead, uuid.UUID(identity_lead_id))
    db.expire_all()
    assert identity_lead.needs_review is True
    assert identity_lead.email is None  # conflicting email was not copied
    assert db.scalar(select(func.count()).select_from(Lead)) == 2


def test_contact_match_attaches_identity_for_future_events(client, db) -> None:
    existing = Lead(name="Known Person", phone="+15550107000", source="manual")
    db.add(existing)
    db.commit()

    with_phone = {
        "channel": "whatsapp",
        "provider": "meta",
        "external_sender_id": "wa-known",
        "sender_phone": "+15550107000",
        "content": "hi",
    }
    r1 = post_event(client, with_phone, key="evt-wak-0001").json()
    assert r1["lead_id"] == str(existing.id)

    # Later event carries only the provider identity; it must reuse the lead.
    only_identity = {
        "channel": "whatsapp",
        "provider": "meta",
        "external_sender_id": "wa-known",
        "content": "again",
    }
    r2 = post_event(client, only_identity, key="evt-wak-0002").json()
    assert r2["lead_id"] == str(existing.id)
    assert r2["lead_created"] is False
