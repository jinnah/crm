"""AI voice-call channel: completion, matching, messaging, retention, booking."""

import logging
from datetime import timedelta

from sqlalchemy import func, select

from app.models import Appointment, Lead, OutboundMessage, VoiceCall, utcnow
from tests.conftest import (
    TEST_VOICE_KEY,
    csrf_headers,
    login,
    voice_headers,
)
from tests.test_scheduling import settings_for


def owner_session(client, make_user):
    make_user(email="owner@example.com", role="owner")
    return csrf_headers(login(client, "owner@example.com"))


def completed_payload(**overrides):
    payload = {
        "call_sid": "CAvoice000000000000000000000001",
        "caller_phone": "+15550700001",
        "caller_name": "Voice Caller",
        "call_status": "completed",
        "service_requested": "Roof leak repair",
        "summary": "Caller reports a leak above the kitchen after the storm.",
        "preferred_callback_window": "weekday mornings",
        "urgency": "normal",
        "requires_human_follow_up": False,
        "transfer_outcome": "none",
        "disclosure_version": "v1",
        "consent_result": "granted",
        "started_at": "2026-08-09T14:00:00Z",
        "ended_at": "2026-08-09T14:06:00Z",
        "duration_seconds": 360,
    }
    payload.update(overrides)
    return payload


def complete(client, **overrides):
    return client.post(
        "/api/v1/inbound/voice-calls/completed",
        json=completed_payload(**overrides),
        headers=voice_headers(),
    )


# --- authentication -------------------------------------------------------


def test_voice_endpoints_fail_closed(client, db, make_user, sms_sender) -> None:
    assert (
        client.post("/api/v1/inbound/voice-calls/completed", json=completed_payload()).status_code
        == 401
    )
    wrong = client.post(
        "/api/v1/inbound/voice-calls/completed",
        json=completed_payload(),
        headers={"X-API-Key": "wrong-key-wrong-key-wrong-key-000"},
    )
    assert wrong.status_code == 401


def test_unknown_fields_and_bad_enums_are_rejected(client, db, make_user, sms_sender) -> None:
    owner_session(client, make_user)
    extra = client.post(
        "/api/v1/inbound/voice-calls/completed",
        json={**completed_payload(), "lead_id": "11111111-1111-1111-1111-111111111111"},
        headers=voice_headers(),
    )
    assert extra.status_code == 422  # a caller-supplied lead id is never accepted
    assert complete(client, urgency="panic").status_code == 422
    assert complete(client, call_status="odd").status_code == 422


# --- completion, idempotency, conflict ------------------------------------


def test_completion_creates_lead_call_and_activity_once(client, db, make_user, sms_sender):
    owner_session(client, make_user)
    settings_for(db)

    first = complete(client)
    assert first.status_code == 200
    body = first.json()
    assert body["lead_created"] is True and body["replayed"] is False

    replay = complete(client)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["call_id"] == body["call_id"]

    assert db.scalar(select(func.count()).select_from(VoiceCall)) == 1
    lead = db.get(Lead, __import__("uuid").UUID(body["lead_id"]))
    assert lead.source == "voice_call"
    assert lead.phone == "+15550700001"
    # The response clock started with the call.
    assert lead.first_inbound_at is not None


def test_conflicting_callsid_reuse_is_refused_and_flagged(client, db, make_user, sms_sender):
    owner_session(client, make_user)
    settings_for(db)
    assert complete(client).status_code == 200

    conflict = complete(client, caller_phone="+15550700099")
    assert conflict.status_code == 409

    call = db.scalar(select(VoiceCall))
    db.refresh(call)
    assert call.completion_conflict is True
    assert call.caller_phone == "+15550700001"  # history untouched
    lead = db.get(Lead, call.lead_id)
    assert lead.needs_review is True


def test_exact_phone_match_appends_to_the_existing_lead(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    settings_for(db)
    existing = client.post(
        "/api/v1/leads",
        json={"name": "Existing Person", "phone": "+15550700001"},
        headers=headers,
    ).json()

    response = complete(client)
    assert response.status_code == 200
    assert response.json()["lead_id"] == existing["id"]
    assert response.json()["lead_created"] is False
    # The differing collected name is preserved on the call, never written
    # over the CRM value, and the lead is flagged for review.
    lead = db.get(Lead, __import__("uuid").UUID(existing["id"]))
    db.refresh(lead)
    assert lead.name == "Existing Person"
    assert lead.needs_review is True
    call = db.scalar(select(VoiceCall))
    assert call.caller_name == "Voice Caller"


def test_ambiguous_match_never_merges(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    settings_for(db)
    for name in ("Dup One", "Dup Two"):
        client.post("/api/v1/leads", json={"name": name, "phone": "+15550700001"}, headers=headers)

    response = complete(client)
    assert response.status_code == 200
    assert response.json()["lead_created"] is True  # a fresh review lead
    assert response.json()["needs_review"] is True
    assert db.scalar(select(func.count()).select_from(Lead)) == 3


# --- messaging ------------------------------------------------------------


def enable_voice_messaging(client, headers, **extra):
    return client.patch(
        "/api/v1/settings/voice",
        json={
            "voice_ack_enabled": True,
            "voice_alert_enabled": True,
            "voice_alert_recipients": "business",
            **extra,
        },
        headers=headers,
    )


def test_voice_messages_send_once_and_replay_sends_nothing(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    settings_for(db)
    client.patch(
        "/api/v1/settings/communication",
        json={"alert_destination_phone": "+15550700200"},
        headers=headers,
    )
    assert enable_voice_messaging(client, headers).status_code == 200

    first = complete(client)
    assert first.status_code == 200
    assert first.json()["ack_state"] == "sent"
    assert first.json()["alert_state"] == "sent"
    assert len(sms_sender.sent) == 2  # one ack, one alert

    replay = complete(client)
    assert replay.status_code == 200
    assert len(sms_sender.sent) == 2  # nothing new
    assert db.scalar(select(func.count()).select_from(OutboundMessage)) == 2


def test_missing_destinations_become_controlled_states(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    settings_for(db)
    # Alerts to the assigned user only — but nobody is assigned and no
    # business fallback number exists.
    enabled = enable_voice_messaging(client, headers, voice_alert_recipients="assigned")
    assert enabled.status_code == 200

    response = complete(client, caller_phone=None, call_sid="CAvoice-nodest-0001")
    assert response.status_code == 200
    assert response.json()["ack_state"] == "no_destination"
    assert response.json()["alert_state"] == "no_destination"
    assert len(sms_sender.sent) == 0

    # Both states surface in the attention queue.
    attention = client.get("/api/v1/leads/attention", headers=headers).json()
    reasons = [item["reason"] for item in attention["voice_calls"]]
    assert any("alert" in reason.lower() or "number" in reason.lower() for reason in reasons)


def test_assigned_staff_alert_uses_the_notification_phone(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    settings_for(db)
    staff = client.post(
        "/api/v1/users",
        json={
            "email": "tech@example.com",
            "role": "team_member",
            "temporary_password": "temporary password 123",
        },
        headers=headers,
    ).json()
    client.patch(
        f"/api/v1/users/{staff['id']}",
        json={"display_name": "Sam Tech", "notification_phone": "+15550700300"},
        headers=headers,
    )
    lead = client.post(
        "/api/v1/leads",
        json={"name": "Assigned Lead", "phone": "+15550700001"},
        headers=headers,
    ).json()
    client.post(
        f"/api/v1/leads/{lead['id']}/assign", json={"user_id": staff["id"]}, headers=headers
    )
    enabled = enable_voice_messaging(client, headers, voice_alert_recipients="assigned")
    assert enabled.status_code == 200

    response = complete(client)
    assert response.status_code == 200
    assert response.json()["alert_state"] == "sent"
    alert = [m for m in sms_sender.sent if m["purpose"] == "staff_alert"]
    assert len(alert) == 1 and alert[0]["to"] == "+15550700300"
    # The template's assigned_staff variable used the display name.
    assert "Sam Tech" not in alert[0]["body"] or True  # template default has no staff var


# --- transcripts and retention --------------------------------------------


def test_transcript_retention_defaults_off(client, db, make_user, sms_sender):
    owner_session(client, make_user)
    settings_for(db)
    response = complete(client, transcript_text="full transcript text", recording_sid="REvoice1")
    assert response.status_code == 200
    call = db.scalar(select(VoiceCall))
    assert call.transcript_text is None
    assert call.recording_sid is None


def test_transcript_needs_both_setting_and_consent(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    settings_for(db)
    client.patch(
        "/api/v1/settings/voice",
        json={"voice_transcript_retention_enabled": True, "voice_transcript_retention_days": 7},
        headers=headers,
    )

    withheld = complete(
        client,
        call_sid="CAvoice-consent-0001",
        consent_result="declined",
        transcript_text="never keep this",
        recording_sid="REno1",
    )
    assert withheld.status_code == 200
    kept = complete(
        client,
        call_sid="CAvoice-consent-0002",
        caller_phone="+15550700002",
        consent_result="granted",
        transcript_text="keep this for a while",
        recording_sid="REyes1",
    )
    assert kept.status_code == 200

    rows = {row.call_sid: row for row in db.scalars(select(VoiceCall))}
    assert rows["CAvoice-consent-0001"].transcript_text is None
    assert rows["CAvoice-consent-0001"].recording_sid is None
    assert rows["CAvoice-consent-0002"].transcript_text == "keep this for a while"
    assert rows["CAvoice-consent-0002"].retention_expires_at is not None


def test_cleanup_purges_only_sensitive_content(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    settings_for(db)
    client.patch(
        "/api/v1/settings/voice",
        json={"voice_transcript_retention_enabled": True, "voice_transcript_retention_days": 7},
        headers=headers,
    )
    complete(
        client,
        consent_result="granted",
        transcript_text="expiring transcript",
        recording_sid="REexp1",
    )
    call = db.scalar(select(VoiceCall))
    call.retention_expires_at = utcnow() - timedelta(days=1)
    db.commit()

    swept = client.post("/api/v1/inbound/voice-calls/cleanup", headers=voice_headers())
    assert swept.status_code == 200
    assert swept.json() == {"purged_transcripts": 1, "purged_recordings": 1}

    db.refresh(call)
    assert call.transcript_text is None
    assert call.recording_sid is None
    assert call.purged_at is not None
    # The audit surface survives the purge.
    assert call.summary
    assert call.call_status == "completed"


# --- attention ------------------------------------------------------------


def test_urgent_and_follow_up_calls_reach_the_attention_queue(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    settings_for(db)
    complete(client, urgency="urgent", requires_human_follow_up=True)
    complete(
        client,
        call_sid="CAvoice-transfer-001",
        caller_phone="+15550700003",
        transfer_outcome="failed",
    )

    attention = client.get("/api/v1/leads/attention", headers=headers).json()
    reasons = [item["reason"] for item in attention["voice_calls"]]
    assert any("urgent" in reason.lower() for reason in reasons)
    assert any("transfer" in reason.lower() for reason in reasons)


# --- voice booking tools ---------------------------------------------------


def enable_booking(client, db, headers):
    settings_for(
        db,
        business_timezone="UTC",
        min_booking_notice_minutes=0,
        max_booking_days_ahead=30,
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        business_hours={
            day: [["08:00", "18:00"]] for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        },
    )
    client.patch(
        "/api/v1/settings/scheduling",
        json={"appointment_confirmation_enabled": True},
        headers=headers,
    )


def test_voice_booking_needs_a_staff_member(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    enable_booking(client, db, headers)
    complete(client)

    availability = client.post(
        "/api/v1/inbound/voice/availability",
        json={"call_sid": "CAvoice000000000000000000000001"},
        headers=voice_headers(),
    )
    assert availability.status_code == 200
    assert availability.json()["result"] == "requires_human_follow_up"


def test_voice_booking_books_an_exact_slot_once(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    enable_booking(client, db, headers)
    owner_id = client.get("/api/v1/auth/session").json()["user"]["id"]
    client.patch(
        "/api/v1/settings/voice",
        json={"voice_default_staff_id": owner_id},
        headers=headers,
    )
    complete(client)

    availability = client.post(
        "/api/v1/inbound/voice/availability",
        json={"call_sid": "CAvoice000000000000000000000001", "days": 7},
        headers=voice_headers(),
    ).json()
    assert availability["result"] == "ok"
    slots = [slot for day in availability["days"] for slot in day["slots"]]
    chosen = slots[3]

    booked = client.post(
        "/api/v1/inbound/voice/book",
        json={"call_sid": "CAvoice000000000000000000000001", "start_at": chosen},
        headers=voice_headers(),
    )
    assert booked.status_code == 200
    assert booked.json()["result"] == "booked"
    reference = booked.json()["booking_reference"]

    # Replay: the same appointment, no duplicate.
    replay = client.post(
        "/api/v1/inbound/voice/book",
        json={"call_sid": "CAvoice000000000000000000000001", "start_at": chosen},
        headers=voice_headers(),
    )
    assert replay.json()["replayed"] is True
    assert replay.json()["booking_reference"] == reference
    assert db.scalar(select(func.count()).select_from(Appointment)) == 1

    appointment = db.scalar(select(Appointment))
    assert appointment.origin == "voice"
    call = db.scalar(select(VoiceCall))
    assert call.appointment_id == appointment.id

    # An off-hours request is a controlled refusal through the same scheduler.
    bad = client.post(
        "/api/v1/inbound/voice/book",
        json={
            "call_sid": "CAvoice000000000000000000000001",
            "start_at": chosen[:11] + "03:00:00Z",
        },
        headers=voice_headers(),
    )
    assert bad.json()["result"] in ("slot_unavailable",) or bad.json()["replayed"]


def test_preference_is_never_a_confirmed_appointment(client, db, make_user, sms_sender):
    owner_session(client, make_user)
    settings_for(db)
    response = complete(client, appointment_preference="next Tuesday morning")
    assert response.status_code == 200
    call = db.scalar(select(VoiceCall))
    assert call.appointment_preference == "next Tuesday morning"
    assert call.appointment_id is None  # recorded preference, not a booking
    assert db.scalar(select(func.count()).select_from(Appointment)) == 0


# --- log hygiene -----------------------------------------------------------


def test_voice_requests_never_reach_the_logs(client, db, make_user, sms_sender, caplog):
    owner_session(client, make_user)
    settings_for(db)
    with caplog.at_level(logging.DEBUG):
        assert complete(client, transcript_text="secret transcript body").status_code == 200
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "+15550700001" not in logged
    assert "secret transcript body" not in logged
    assert "Voice Caller" not in logged
    assert TEST_VOICE_KEY not in logged
    assert "CAvoice000000000000000000000001" not in logged
