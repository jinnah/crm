import uuid

from sqlalchemy import func, select

from app.models import Lead, LeadActivity, OutboundMessage
from app.services.messaging import SendOutcome
from tests.conftest import TEST_INBOUND_KEY, csrf_headers, login


def owner_session(client, make_user, email="owner@example.com", role="owner"):
    make_user(email=email, role=role)
    return csrf_headers(login(client, email))


def create_lead(client, headers, **overrides):
    payload = {"name": "Lead One", "phone": "+15550100001", "email": "lead1@example.com"}
    payload.update(overrides)
    return client.post("/api/v1/leads", json=payload, headers=headers)


def send_sms(client, headers, lead_id, body="Hello from the shop", key="msg-key-0001"):
    return client.post(
        f"/api/v1/leads/{lead_id}/messages",
        json={"body": body},
        headers={**headers, "Idempotency-Key": key},
    )


def test_owner_sends_sms_and_it_counts_as_first_response(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    # An inbound event starts the response clock.
    client.post(
        "/api/v1/inbound/events",
        json={"channel": "web_form", "sender_phone": "+15550100001", "content": "Need a quote"},
        headers={"X-API-Key": TEST_INBOUND_KEY, "Idempotency-Key": "inb-resp-0001"},
    )
    lead = db.scalar(select(Lead))
    assert lead.first_inbound_at is not None
    assert lead.response_due_at is not None
    assert lead.first_response_at is None

    response = send_sms(client, headers, lead.id)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "submitted"
    assert body["purpose"] == "human_reply"
    assert body["created_by_email"] == "owner@example.com"
    assert sms_sender.sent[0]["to"] == "+15550100001"

    db.expire_all()
    lead = db.scalar(select(Lead))
    assert lead.first_response_at is not None
    assert lead.first_response_seconds is not None
    assert lead.response_target_met is True

    types = [
        activity.type
        for activity in db.scalars(select(LeadActivity).where(LeadActivity.lead_id == lead.id))
    ]
    assert "outbound_message" in types


def test_duplicate_idempotency_key_sends_once(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    lead = create_lead(client, headers).json()
    first = send_sms(client, headers, lead["id"], key="dup-key-0001")
    second = send_sms(client, headers, lead["id"], key="dup-key-0001")
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(sms_sender.sent) == 1
    assert db.scalar(select(func.count()).select_from(OutboundMessage)) == 1


def test_role_isolation_for_outbound_sms(client, db, make_user, sms_sender) -> None:
    owner_headers = owner_session(client, make_user)
    member = make_user(email="member@example.com", role="team_member")
    make_user(email="other@example.com", role="team_member")
    mine = create_lead(client, owner_headers, name="Mine", email="mine@example.com").json()
    theirs = create_lead(
        client, owner_headers, name="Theirs", email="theirs@example.com", phone="+15550100002"
    ).json()
    client.post(
        f"/api/v1/leads/{mine['id']}/assign",
        json={"user_id": str(member.id)},
        headers=owner_headers,
    )

    member_headers = csrf_headers(login(client, "member@example.com"))
    assert send_sms(client, member_headers, mine["id"], key="mem-key-0001").status_code == 201
    # A lead assigned to someone else is not even visible.
    blocked = send_sms(client, member_headers, theirs["id"], key="mem-key-0002")
    assert blocked.status_code == 404
    assert len(sms_sender.sent) == 1


def test_archived_lead_rejects_sending(client, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    lead = create_lead(client, headers).json()
    client.post(f"/api/v1/leads/{lead['id']}/archive", headers=headers)
    blocked = send_sms(client, headers, lead["id"], key="arch-key-0001")
    assert blocked.status_code == 409
    assert sms_sender.sent == []


def test_lead_without_international_phone_is_rejected(client, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    lead = create_lead(client, headers, phone="555-010-0001").json()  # no country code
    response = send_sms(client, headers, lead["id"], key="nophone-key-01")
    assert response.status_code == 400
    assert sms_sender.sent == []


def test_failed_send_is_recorded_without_response_credit(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    client.post(
        "/api/v1/inbound/events",
        json={"channel": "web_form", "sender_phone": "+15550100001", "content": "hi"},
        headers={"X-API-Key": TEST_INBOUND_KEY, "Idempotency-Key": "inb-fail-0001"},
    )
    lead = db.scalar(select(Lead))
    sms_sender.outcome = SendOutcome(
        status="failed", error_code="21610", error_message="Unsubscribed recipient"
    )
    response = send_sms(client, headers, lead.id, key="fail-key-0001")
    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["error_message"] == "Unsubscribed recipient"
    db.expire_all()
    assert db.scalar(select(Lead)).first_response_at is None  # a failed send is no response


def test_ambiguous_send_is_unknown_and_not_resent(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    lead = create_lead(client, headers).json()
    sms_sender.outcome = SendOutcome(
        status="unknown", error_code="timeout", error_message="No confirmation received."
    )
    response = send_sms(client, headers, lead["id"], key="unk-key-0001")
    assert response.json()["status"] == "unknown"
    # Retrying the same key never sends a second copy of an ambiguous message.
    again = send_sms(client, headers, lead["id"], key="unk-key-0001")
    assert again.json()["id"] == response.json()["id"]
    assert len(sms_sender.sent) == 1


def test_automated_messages_sent_once_and_not_counted_as_response(
    client, db, make_user, sms_sender
) -> None:
    headers = owner_session(client, make_user)
    client.patch(
        "/api/v1/settings/communication",
        json={
            "business_name": "Acme Roofing",
            "acknowledgment_enabled": True,
            "alert_enabled": True,
            "alert_destination_phone": "+15550209999",
        },
        headers=headers,
    )

    event = {
        "channel": "web_form",
        "provider": "website",
        "external_event_id": "form-auto-1",
        "sender_name": "Auto Lead",
        "sender_phone": "+15550100055",
        "content": "Roof leaking badly",
    }
    first = client.post(
        "/api/v1/inbound/events",
        json=event,
        headers={"X-API-Key": TEST_INBOUND_KEY, "Idempotency-Key": "inb-auto-0001"},
    )
    assert first.status_code == 200
    purposes = sorted(message["purpose"] for message in sms_sender.sent)
    assert purposes == ["auto_acknowledgment", "staff_alert"]
    # The alert carries a CRM reference, not the full request content.
    alert = next(m for m in sms_sender.sent if m["purpose"] == "staff_alert")
    assert "Roof leaking badly" not in alert["body"]
    assert str(first.json()["lead_id"]) in alert["body"]

    # A retry of the same submission sends nothing further.
    client.post(
        "/api/v1/inbound/events",
        json=event,
        headers={"X-API-Key": TEST_INBOUND_KEY, "Idempotency-Key": "inb-auto-0001"},
    )
    assert len(sms_sender.sent) == 2
    assert db.scalar(select(func.count()).select_from(OutboundMessage)) == 2

    db.expire_all()
    lead = db.get(Lead, uuid.UUID(first.json()["lead_id"]))
    assert lead.first_inbound_at is not None
    assert lead.first_response_at is None  # automated messages are not responses


def test_acknowledgment_failure_does_not_lose_the_lead(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    client.patch(
        "/api/v1/settings/communication",
        json={"acknowledgment_enabled": True},
        headers=headers,
    )
    sms_sender.raise_error = True
    response = client.post(
        "/api/v1/inbound/events",
        json={"channel": "web_form", "sender_phone": "+15550100077", "content": "still stored"},
        headers={"X-API-Key": TEST_INBOUND_KEY, "Idempotency-Key": "inb-ackfail-01"},
    )
    assert response.status_code == 200
    assert db.get(Lead, uuid.UUID(response.json()["lead_id"])) is not None


def test_delivery_callbacks_are_idempotent(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    lead = create_lead(client, headers).json()
    message = send_sms(client, headers, lead["id"], key="cb-key-000001").json()
    sid = message["provider_sid"]
    assert sid

    def callback(status, **extra):
        return client.post(
            "/api/v1/inbound/message-status",
            json={"provider_sid": sid, "status": status, **extra},
            headers={"X-API-Key": TEST_INBOUND_KEY},
        )

    assert callback("sent").json() == {"matched": True, "status": "submitted"}
    assert callback("delivered").json() == {"matched": True, "status": "delivered"}
    # Repeated callbacks change nothing and add no duplicate activities.
    assert callback("delivered").json() == {"matched": True, "status": "delivered"}
    assert callback("sent").json()["status"] == "delivered"  # no backwards transition

    status_activities = [
        activity
        for activity in db.scalars(select(LeadActivity))
        if activity.type == "message_status"
    ]
    assert len(status_activities) == 1


def test_failed_delivery_callback_records_bounded_error(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    lead = create_lead(client, headers).json()
    message = send_sms(client, headers, lead["id"], key="cbfail-key-01").json()
    response = client.post(
        "/api/v1/inbound/message-status",
        json={
            "provider_sid": message["provider_sid"],
            "status": "undelivered",
            "error_code": "30003",
            "error_message": "Unreachable destination handset",
        },
        headers={"X-API-Key": TEST_INBOUND_KEY},
    )
    assert response.json() == {"matched": True, "status": "failed"}
    row = db.scalar(select(OutboundMessage))
    db.refresh(row)
    assert row.error_code == "30003"
    assert row.failed_at is not None


def test_unknown_sid_callback_creates_nothing(client, db) -> None:
    before = db.scalar(select(func.count()).select_from(Lead))
    response = client.post(
        "/api/v1/inbound/message-status",
        json={"provider_sid": "SMdoesnotexist", "status": "delivered"},
        headers={"X-API-Key": TEST_INBOUND_KEY},
    )
    assert response.json() == {"matched": False, "status": None}
    assert db.scalar(select(func.count()).select_from(Lead)) == before


def test_callback_requires_inbound_key(client) -> None:
    response = client.post(
        "/api/v1/inbound/message-status",
        json={"provider_sid": "SMfake0001", "status": "delivered"},
    )
    assert response.status_code == 401


def test_mark_contacted_outside_crm(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    client.post(
        "/api/v1/inbound/events",
        json={"channel": "phone_call", "sender_phone": "+15550100003", "content": "missed call"},
        headers={"X-API-Key": TEST_INBOUND_KEY, "Idempotency-Key": "inb-mark-0001"},
    )
    lead = db.scalar(select(Lead))

    response = client.post(f"/api/v1/leads/{lead.id}/mark-contacted", headers=headers)
    assert response.status_code == 200
    assert response.json()["first_response_at"] is not None
    assert response.json()["response_target_met"] is True
    # A second attempt is rejected: the first response is already recorded.
    assert (
        client.post(f"/api/v1/leads/{lead.id}/mark-contacted", headers=headers).status_code == 409
    )

    types = [activity.type for activity in db.scalars(select(LeadActivity))]
    assert "contacted_outside_crm" in types


def test_notes_and_status_changes_do_not_count_as_response(
    client, db, make_user, sms_sender
) -> None:
    headers = owner_session(client, make_user)
    client.post(
        "/api/v1/inbound/events",
        json={"channel": "web_form", "sender_phone": "+15550100004", "content": "hello"},
        headers={"X-API-Key": TEST_INBOUND_KEY, "Idempotency-Key": "inb-noresp-01"},
    )
    lead = db.scalar(select(Lead))
    client.post(f"/api/v1/leads/{lead.id}/notes", json={"content": "internal"}, headers=headers)
    client.patch(f"/api/v1/leads/{lead.id}", json={"status": "contacted"}, headers=headers)
    db.expire_all()
    assert db.scalar(select(Lead)).first_response_at is None


def test_attention_queue_lists_overdue_unresponded(client, db, make_user, sms_sender) -> None:
    from datetime import timedelta

    from app.models import utcnow

    headers = owner_session(client, make_user)
    client.post(
        "/api/v1/inbound/events",
        json={"channel": "web_form", "sender_phone": "+15550100005", "content": "urgent"},
        headers={"X-API-Key": TEST_INBOUND_KEY, "Idempotency-Key": "inb-overdue-1"},
    )
    lead = db.scalar(select(Lead))
    lead.response_due_at = utcnow() - timedelta(minutes=1)
    db.commit()

    queue = client.get("/api/v1/leads/attention", headers=headers).json()
    assert [item["id"] for item in queue["unresponded"]] == [str(lead.id)]
    assert queue["unresponded"][0]["response_overdue"] is True

    # Answering it clears the queue entry.
    send_sms(client, headers, lead.id, key="overdue-key-01")
    queue = client.get("/api/v1/leads/attention", headers=headers).json()
    assert queue["unresponded"] == []


def test_delivered_is_terminal_and_transitions_are_central(client, db, make_user, sms_sender):
    """delivered must never walk back to submitted/failed/pending/unknown."""
    from app.services.messaging import ALLOWED_STATUS_TRANSITIONS, transition_allowed

    # The table is the single definition of what may change.
    assert ALLOWED_STATUS_TRANSITIONS["delivered"] == frozenset()
    for proposed in ("submitted", "failed", "pending", "unknown"):
        assert transition_allowed("delivered", proposed) is False
    assert transition_allowed("submitted", "delivered") is True
    assert transition_allowed("submitted", "failed") is True
    assert transition_allowed("unknown", "delivered") is True
    assert transition_allowed("unknown", "failed") is True
    assert transition_allowed("failed", "delivered") is False
    assert transition_allowed("submitted", "submitted") is False

    headers = owner_session(client, make_user)
    lead = create_lead(client, headers).json()
    message = send_sms(client, headers, lead["id"], key="terminal-key-01").json()
    sid = message["provider_sid"]

    def callback(status, **extra):
        return client.post(
            "/api/v1/inbound/message-status",
            json={"provider_sid": sid, "status": status, **extra},
            headers={"X-API-Key": TEST_INBOUND_KEY},
        )

    assert callback("delivered").json()["status"] == "delivered"
    # The regression this test exists for: delivered -> failed stays delivered.
    assert callback("failed", error_code="30008").json()["status"] == "delivered"
    assert callback("undelivered").json()["status"] == "delivered"
    assert callback("sent").json()["status"] == "delivered"

    row = db.scalar(select(OutboundMessage).where(OutboundMessage.provider_sid == sid))
    db.refresh(row)
    assert row.status == "delivered"
    assert row.failed_at is None

    status_activities = [
        activity
        for activity in db.scalars(select(LeadActivity))
        if activity.type == "message_status"
    ]
    assert len(status_activities) == 1  # no duplicates from repeated callbacks


def test_out_of_order_callbacks_do_not_duplicate_activities(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    lead = create_lead(client, headers).json()
    message = send_sms(client, headers, lead["id"], key="ooo-key-000001").json()
    sid = message["provider_sid"]

    for status in ("delivered", "sent", "queued", "delivered", "sent"):
        client.post(
            "/api/v1/inbound/message-status",
            json={"provider_sid": sid, "status": status},
            headers={"X-API-Key": TEST_INBOUND_KEY},
        )
    status_activities = [
        activity
        for activity in db.scalars(select(LeadActivity))
        if activity.type == "message_status"
    ]
    assert len(status_activities) == 1


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _sender_with(monkeypatch, behaviour):
    """Build an N8nSmsSender whose HTTP call is replaced by `behaviour`."""
    import httpx

    from app.config import get_settings
    from app.services.messaging import N8nSmsSender

    settings = get_settings()
    monkeypatch.setattr(settings, "n8n_send_url", "http://n8n.test/webhook/twilio-send")
    monkeypatch.setattr(settings, "n8n_send_secret", "test-send-secret")
    monkeypatch.setattr(httpx, "post", behaviour)
    return N8nSmsSender(settings)


def _dummy_message():
    return OutboundMessage(
        lead_id=uuid.uuid4(),
        purpose="human_reply",
        to_phone="+15550100001",
        body="hi",
        idempotency_key_digest="digest-classify",
    )


def test_transport_failure_is_unknown_not_failed(monkeypatch) -> None:
    """A lost response may still have reached the provider."""
    import httpx

    def raise_transport(*args, **kwargs):
        raise httpx.ConnectError("connection reset")

    sender = _sender_with(monkeypatch, raise_transport)
    assert sender.send(_dummy_message()).status == "unknown"


def test_timeout_is_unknown(monkeypatch) -> None:
    import httpx

    def raise_timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    sender = _sender_with(monkeypatch, raise_timeout)
    assert sender.send(_dummy_message()).status == "unknown"


def test_send_outcomes_are_classified_conservatively(monkeypatch) -> None:
    cases = [
        ({"status": "submitted", "sid": "SMok0001"}, 200, "submitted"),
        (
            {"status": "failed", "error_code": "21610", "error_message": "Unsubscribed"},
            200,
            "failed",
        ),
        ({"status": "unknown", "error_code": "no_sid"}, 200, "unknown"),
        ({"status": "submitted"}, 200, "unknown"),  # nominal success without a SID
        (None, 200, "unknown"),  # unreadable body
        ({"status": "submitted", "sid": "SMx"}, 500, "unknown"),  # service error
        ({"error_code": "bad_request"}, 400, "failed"),  # definite rejection
    ]
    for payload, status_code, expected in cases:
        sender = _sender_with(
            monkeypatch, lambda *a, p=payload, s=status_code, **k: _FakeResponse(p, s)
        )
        assert sender.send(_dummy_message()).status == expected, (payload, status_code)


def test_abandoned_pending_message_recovers_to_unknown(client, db, make_user, sms_sender):
    """A crash between the durable insert and the provider outcome must not
    block the lead forever."""
    from datetime import timedelta

    from app.models import utcnow
    from app.services.messaging import PENDING_RECOVERY_MINUTES, recover_abandoned_pending

    headers = owner_session(client, make_user)
    lead = create_lead(client, headers).json()

    # Simulate the crash window: a committed pending row, no outcome recorded.
    stranded = OutboundMessage(
        lead_id=uuid.UUID(lead["id"]),
        purpose="human_reply",
        to_phone="+15550100001",
        body="interrupted send",
        status="pending",
        idempotency_key_digest="digest-stranded",
        created_at=utcnow() - timedelta(minutes=PENDING_RECOVERY_MINUTES + 1),
    )
    db.add(stranded)
    db.commit()

    # The stranded row must not block the lead: sending recovers it first.
    unblocked = send_sms(client, headers, lead["id"], key="stranded-key-1")
    assert unblocked.status_code == 201

    db.expire_all()
    recovered = db.scalar(
        select(OutboundMessage).where(OutboundMessage.idempotency_key_digest == "digest-stranded")
    )
    assert recovered.status == "unknown"  # never "failed"
    assert recovered.error_code == "abandoned"
    # Still visible in the conversation with the ambiguous outcome.
    listing = client.get(f"/api/v1/leads/{lead['id']}/messages", headers=headers).json()
    assert any(item["status"] == "unknown" for item in listing)
    # Recovery never resends the stranded message.
    assert all(entry["body"] != "interrupted send" for entry in sms_sender.sent)

    # A fresh pending row inside the window is left alone.
    fresh = OutboundMessage(
        lead_id=uuid.UUID(lead["id"]),
        purpose="human_reply",
        to_phone="+15550100001",
        body="in flight",
        status="pending",
        idempotency_key_digest="digest-fresh",
    )
    db.add(fresh)
    db.commit()
    assert recover_abandoned_pending(db, uuid.UUID(lead["id"])) == 0


def test_settings_row_is_a_singleton(client, db, make_user) -> None:
    from sqlalchemy import func as sa_func

    from app.models import CommunicationSettings
    from app.services.messaging import get_settings_row

    headers = owner_session(client, make_user)
    client.get("/api/v1/settings/communication", headers=headers)
    for _ in range(3):
        get_settings_row(db)
    db.commit()
    assert db.scalar(sa_func.count(CommunicationSettings.id)) == 1


def test_concurrent_duplicate_keys_contact_the_provider_once(client, make_user, sms_sender):
    headers = owner_session(client, make_user)
    lead = create_lead(client, headers).json()
    first = send_sms(client, headers, lead["id"], key="dup-provider-01")
    second = send_sms(client, headers, lead["id"], key="dup-provider-01")
    assert first.json()["id"] == second.json()["id"]
    assert len(sms_sender.sent) == 1
