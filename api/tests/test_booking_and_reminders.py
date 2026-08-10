"""Booking links, public booking, appointment notifications and .ics output."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models import (
    Appointment,
    AppointmentNotification,
    BookingLink,
    Lead,
    OutboundMessage,
    utcnow,
)
from app.services import appointment_notifications as notifications
from app.services import calendar_file
from app.services.messaging import SendOutcome
from tests.conftest import TEST_INBOUND_KEY, csrf_headers, internal_headers, login
from tests.test_scheduling import settings_for


def owner_session(client, make_user):
    make_user(email="owner@example.com", role="owner")
    return csrf_headers(login(client, "owner@example.com"))


def make_lead(client, headers, phone="+15550500001", name="Booking Lead"):
    return client.post("/api/v1/leads", json={"name": name, "phone": phone}, headers=headers).json()


def enable_scheduling(client, db, headers, **overrides):
    settings_for(db, **overrides)
    client.patch(
        "/api/v1/settings/scheduling",
        json={
            "self_booking_enabled": True,
            "appointment_confirmation_enabled": True,
            "appointment_reminder_enabled": True,
            "reminder_offset_minutes": 1440,
        },
        headers=headers,
    )


def booking_info(client, token, **extra):
    """BFF-style lookup: fixed path, token in the body, internal credential."""
    return client.post(
        "/api/v1/internal/booking/info",
        json={"token": token, **extra},
        headers=internal_headers(),
    )


def booking_confirm(client, token, start_at, booking_key, **extra):
    return client.post(
        "/api/v1/internal/booking/confirm",
        json={"token": token, "start_at": start_at, "booking_key": booking_key, **extra},
        headers=internal_headers(),
    )


def manage_info(client, token):
    return client.post(
        "/api/v1/internal/appointments/info",
        json={"token": token},
        headers=internal_headers(),
    )


def manage_cancel(client, token):
    return client.post(
        "/api/v1/internal/appointments/cancel",
        json={"token": token},
        headers=internal_headers(),
    )


def manage_reschedule(client, token, start_at, expected_revision):
    return client.post(
        "/api/v1/internal/appointments/reschedule",
        json={"token": token, "start_at": start_at, "expected_revision": expected_revision},
        headers=internal_headers(),
    )


# --- booking links -------------------------------------------------------


def test_booking_link_hashes_token_and_is_revocable(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)

    created = client.post(
        f"/api/v1/leads/{lead['id']}/booking-link", json={"ttl_days": 7}, headers=headers
    )
    assert created.status_code == 201
    url = created.json()["url"]
    raw = url.rsplit("/", 1)[-1]
    assert len(raw) >= 32

    # Only the digest is stored; the raw token appears nowhere in the table.
    row = db.scalar(select(BookingLink))
    assert row.token_digest != raw
    assert raw not in row.token_digest

    assert booking_info(client, raw).status_code == 200

    revoked = client.post(f"/api/v1/leads/{lead['id']}/booking-link/revoke", headers=headers)
    assert revoked.status_code == 200
    assert booking_info(client, raw).status_code == 410


def test_regenerating_a_link_invalidates_the_previous_one(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    first = (
        client.post(f"/api/v1/leads/{lead['id']}/booking-link", json={}, headers=headers)
        .json()["url"]
        .rsplit("/", 1)[-1]
    )
    second = (
        client.post(f"/api/v1/leads/{lead['id']}/booking-link", json={}, headers=headers)
        .json()["url"]
        .rsplit("/", 1)[-1]
    )

    assert first != second
    assert booking_info(client, first).status_code == 410
    assert booking_info(client, second).status_code == 200


def test_expired_link_is_rejected(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    raw = (
        client.post(f"/api/v1/leads/{lead['id']}/booking-link", json={}, headers=headers)
        .json()["url"]
        .rsplit("/", 1)[-1]
    )

    link = db.scalar(select(BookingLink))
    link.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert booking_info(client, raw).status_code == 410


def test_unknown_token_is_rejected(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    assert booking_info(client, "z" * 43).status_code == 404
    # The old token-in-URL routes no longer exist at all.
    assert client.get("/api/v1/public/book/" + "z" * 43).status_code in (404, 405)


def test_internal_endpoints_require_the_bff_credential(client, db, make_user, sms_sender) -> None:
    """Direct FastAPI access without the server-only credential fails closed,
    whatever the token is."""
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers, phone="+15550500055")
    raw = (
        client.post(f"/api/v1/leads/{lead['id']}/booking-link", json={}, headers=headers)
        .json()["url"]
        .rsplit("/", 1)[-1]
    )
    # No credential, wrong credential: both are 401 before any token check.
    for bad_headers in ({}, {"X-Internal-Key": "wrong-key-wrong-key-wrong-key-000"}):
        response = client.post(
            "/api/v1/internal/booking/info", json={"token": raw}, headers=bad_headers
        )
        assert response.status_code == 401
        response = client.post(
            "/api/v1/internal/booking/confirm",
            json={"token": raw, "start_at": "2026-09-01T14:00:00Z", "booking_key": "k" * 10},
            headers=bad_headers,
        )
        assert response.status_code == 401
    # With the credential the same token works.
    assert booking_info(client, raw).status_code == 200


def test_team_member_cannot_create_a_link_for_another_lead(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    make_user(email="member@example.com", role="team_member")
    lead = make_lead(client, headers)
    member_headers = csrf_headers(login(client, "member@example.com"))
    response = client.post(
        f"/api/v1/leads/{lead['id']}/booking-link", json={}, headers=member_headers
    )
    assert response.status_code == 404  # not even visible


# --- public booking ------------------------------------------------------


def _first_slot(client, raw):
    info = booking_info(client, raw).json()
    assert info["days"], "expected available days"
    return info["days"][0]["slots"][0]


def test_public_page_exposes_only_safe_information(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    staff = make_user(email="tech.person@example.com", role="team_member")
    lead = make_lead(client, headers, name="Private Person", phone="+15550500009")
    raw = (
        client.post(
            f"/api/v1/leads/{lead['id']}/booking-link",
            json={"assigned_to": str(staff.id)},
            headers=headers,
        )
        .json()["url"]
        .rsplit("/", 1)[-1]
    )

    response = booking_info(client, raw)
    assert response.status_code == 200
    body = response.text
    # Nothing internal may leak.
    assert lead["id"] not in body
    assert "Private Person" not in body
    assert "+15550500009" not in body
    assert "tech.person@example.com" not in body
    assert str(staff.id) not in body
    payload = response.json()
    assert payload["staff_display_name"] == "Tech Person"  # display name only
    assert set(payload) == {
        "business_name",
        "intro",
        "staff_display_name",
        "duration_minutes",
        "timezone",
        "days",
        "window_days",
        "next_start_day",
    }
    # Availability covers the configured window through bounded paging, not a
    # hard-coded fortnight.
    assert payload["window_days"] > 0


def test_customer_books_and_repeat_submission_is_idempotent(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    raw = (
        client.post(f"/api/v1/leads/{lead['id']}/booking-link", json={}, headers=headers)
        .json()["url"]
        .rsplit("/", 1)[-1]
    )
    slot = _first_slot(client, raw)

    first = booking_confirm(client, raw, slot, "cust-key-0001")
    assert first.status_code == 200, first.text
    assert first.json()["booking_reference"].startswith("APT-")
    assert first.json()["manage_token"]

    second = booking_confirm(client, raw, slot, "cust-key-0001")
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["booking_reference"] == first.json()["booking_reference"]
    # Nothing extra was created.
    assert db.scalar(select(func.count()).select_from(Appointment)) == 1
    confirmations = db.scalars(
        select(AppointmentNotification).where(AppointmentNotification.type == "confirmation")
    ).all()
    assert len(confirmations) == 1


def test_booking_a_taken_slot_is_a_conflict(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    staff = make_user(email="tech@example.com", role="team_member")
    lead = make_lead(client, headers)
    raw = (
        client.post(
            f"/api/v1/leads/{lead['id']}/booking-link",
            json={"assigned_to": str(staff.id)},
            headers=headers,
        )
        .json()["url"]
        .rsplit("/", 1)[-1]
    )
    slot = _first_slot(client, raw)

    assert booking_confirm(client, raw, slot, "key-a-000001").status_code == 200
    clash = booking_confirm(client, raw, slot, "key-b-000001")
    assert clash.status_code == 409


def test_honeypot_submission_creates_nothing(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    raw = (
        client.post(f"/api/v1/leads/{lead['id']}/booking-link", json={}, headers=headers)
        .json()["url"]
        .rsplit("/", 1)[-1]
    )
    slot = _first_slot(client, raw)
    response = booking_confirm(client, raw, slot, "bot-key-0001", website="http://spam")
    assert response.status_code == 422
    assert db.scalar(select(func.count()).select_from(Appointment)) == 0


def test_self_booking_can_be_disabled(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    raw = (
        client.post(f"/api/v1/leads/{lead['id']}/booking-link", json={}, headers=headers)
        .json()["url"]
        .rsplit("/", 1)[-1]
    )
    client.patch(
        "/api/v1/settings/scheduling", json={"self_booking_enabled": False}, headers=headers
    )
    assert booking_info(client, raw).status_code == 403


# --- notifications -------------------------------------------------------


def _create_appointment(client, headers, lead_id, days=3, **overrides):
    payload = {
        "start_at": (utcnow() + timedelta(days=days)).replace(microsecond=0).isoformat(),
        "duration_minutes": 60,
        "subject": "Site visit",
    }
    payload.update(overrides)
    return client.post(f"/api/v1/leads/{lead_id}/appointments", json=payload, headers=headers)


def test_confirmation_is_sent_once_after_storage(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    appointment = _create_appointment(client, headers, lead["id"]).json()

    # The appointment exists and exactly one confirmation went out.
    assert db.scalar(select(func.count()).select_from(Appointment)) == 1
    sent = [entry for entry in sms_sender.sent if entry["purpose"] == "appointment"]
    assert len(sent) == 1
    confirmation = db.scalar(
        select(AppointmentNotification).where(AppointmentNotification.type == "confirmation")
    )
    assert confirmation.state == "sent"

    # Dispatching again sends nothing further.
    from app.config import get_settings

    notifications.dispatch_due(db, get_settings(), sms_sender)
    assert len([e for e in sms_sender.sent if e["purpose"] == "appointment"]) == 1
    _ = appointment


def test_appointment_messages_do_not_count_as_human_response(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    # An inbound event starts the response clock.
    client.post(
        "/api/v1/inbound/events",
        json={"channel": "web_form", "sender_phone": "+15550500055", "content": "hello"},
        headers={"X-API-Key": TEST_INBOUND_KEY, "Idempotency-Key": "appt-resp-001"},
    )
    lead = db.scalar(select(Lead))
    _create_appointment(client, headers, lead.id)
    db.expire_all()
    assert db.scalar(select(Lead)).first_response_at is None


def test_reminder_is_claimed_and_sent_once(client, db, make_user, sms_sender) -> None:
    from app.config import get_settings

    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    _create_appointment(client, headers, lead["id"])

    reminder = db.scalar(
        select(AppointmentNotification).where(AppointmentNotification.type == "reminder")
    )
    assert reminder.state == "pending"
    reminder.scheduled_at = utcnow() - timedelta(minutes=1)  # make it due
    db.commit()

    before = len(sms_sender.sent)
    first = notifications.dispatch_due(db, get_settings(), sms_sender)
    assert first["claimed"] == 1 and first["sent"] == 1
    assert len(sms_sender.sent) == before + 1

    # A second scheduler run finds nothing left to claim.
    second = notifications.dispatch_due(db, get_settings(), sms_sender)
    assert second["claimed"] == 0
    assert len(sms_sender.sent) == before + 1


def test_ambiguous_provider_outcome_is_unknown_and_not_resent(client, db, make_user, sms_sender):
    from app.config import get_settings

    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    sms_sender.outcome = SendOutcome(
        status="unknown", error_code="timeout", error_message="No confirmation."
    )
    _create_appointment(client, headers, lead["id"])

    confirmation = db.scalar(
        select(AppointmentNotification).where(AppointmentNotification.type == "confirmation")
    )
    db.refresh(confirmation)
    assert confirmation.state == "unknown"
    before = len(sms_sender.sent)
    notifications.dispatch_due(db, get_settings(), sms_sender)
    assert len(sms_sender.sent) == before  # never automatically resent


def test_crash_after_claiming_recovers_to_unknown(client, db, make_user, sms_sender) -> None:
    from app.config import get_settings

    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    _create_appointment(client, headers, lead["id"])

    reminder = db.scalar(
        select(AppointmentNotification).where(AppointmentNotification.type == "reminder")
    )
    # Simulate a crash between claiming and recording the outcome.
    reminder.state = "claimed"
    reminder.claimed_at = utcnow() - timedelta(minutes=notifications.CLAIM_RECOVERY_MINUTES + 1)
    db.commit()

    before = len(sms_sender.sent)
    counts = notifications.dispatch_due(db, get_settings(), sms_sender)
    assert counts["recovered"] == 1
    db.refresh(reminder)
    assert reminder.state == "unknown"
    assert reminder.failure_code == "abandoned"
    assert len(sms_sender.sent) == before  # no automatic duplicate


def test_reschedule_suppresses_obsolete_reminders(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    appointment = _create_appointment(client, headers, lead["id"]).json()

    original = db.scalar(
        select(AppointmentNotification).where(AppointmentNotification.type == "reminder")
    )
    new_start = (utcnow() + timedelta(days=5)).replace(microsecond=0).isoformat()
    response = client.post(
        f"/api/v1/appointments/{appointment['id']}/reschedule",
        json={"start_at": new_start, "expected_revision": appointment["revision"]},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["revision"] == appointment["revision"] + 1
    db.refresh(original)
    assert original.state == "suppressed"

    # A fresh reminder exists for the new time, plus a rescheduled notice.
    reminders = db.scalars(
        select(AppointmentNotification).where(
            AppointmentNotification.type == "reminder",
            AppointmentNotification.state == "pending",
        )
    ).all()
    assert len(reminders) == 1
    assert (
        db.scalar(
            select(AppointmentNotification).where(AppointmentNotification.type == "rescheduled")
        )
        is not None
    )


def test_cancellation_suppresses_reminders_and_notifies(client, db, make_user, sms_sender):
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    appointment = _create_appointment(client, headers, lead["id"]).json()
    reminder = db.scalar(
        select(AppointmentNotification).where(AppointmentNotification.type == "reminder")
    )

    response = client.post(
        f"/api/v1/appointments/{appointment['id']}/disposition",
        json={
            "status": "canceled",
            "reason": "customer rescheduled by phone",
            "expected_revision": appointment["revision"],
        },
        headers=headers,
    )
    assert response.status_code == 200
    db.refresh(reminder)
    assert reminder.state == "suppressed"
    canceled = db.scalar(
        select(AppointmentNotification).where(AppointmentNotification.type == "canceled")
    )
    assert canceled is not None and canceled.state == "sent"


def test_failed_appointment_message_never_removes_the_appointment(
    client, db, make_user, sms_sender
) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    sms_sender.raise_error = True
    created = _create_appointment(client, headers, lead["id"])
    assert created.status_code == 201
    assert db.scalar(select(func.count()).select_from(Appointment)) == 1


def test_dispatch_endpoint_requires_the_inbound_key(client) -> None:
    assert client.post("/api/v1/inbound/appointment-notifications/dispatch").status_code == 401
    ok = client.post(
        "/api/v1/inbound/appointment-notifications/dispatch",
        headers={"X-API-Key": TEST_INBOUND_KEY},
    )
    assert ok.status_code == 200
    assert set(ok.json()) == {"claimed", "sent", "failed", "unknown", "suppressed", "recovered"}


# --- calendar file -------------------------------------------------------


def parse_ics(content: str) -> dict:
    """Minimal RFC 5545 parser: unfold, then split property/value."""
    unfolded = content.replace("\r\n ", "").replace("\r\n\t", "")
    properties: dict[str, str] = {}
    for line in unfolded.split("\r\n"):
        if not line:
            continue
        name, _, value = line.partition(":")
        properties[name.split(";")[0]] = value
    return properties


def test_ics_parses_and_omits_private_data(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers, name="Ics Person", phone="+15550500077")
    appointment = _create_appointment(
        client,
        headers,
        lead["id"],
        subject="Roof; inspection, urgent",
        notes="Gate code 1234 - private",
    ).json()

    response = client.get(f"/api/v1/appointments/{appointment['id']}/calendar.ics", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    content = response.text
    assert content.startswith("BEGIN:VCALENDAR\r\n")
    assert content.rstrip("\r\n").endswith("END:VCALENDAR")

    parsed = parse_ics(content)
    assert parsed["VERSION"] == "2.0"
    assert parsed["UID"] == f"appointment-{appointment['id']}@service-crm"
    assert parsed["STATUS"] == "CONFIRMED"
    # Escaped per RFC 5545 and UTC-stamped.
    assert parsed["SUMMARY"] == "Roof\\; inspection\\, urgent"
    assert parsed["DTSTART"].endswith("Z") and len(parsed["DTSTART"]) == 16
    assert parsed["DTEND"].endswith("Z")
    # Private data is absent.
    assert "1234" not in content
    assert "+15550500077" not in content
    assert "Ics Person" not in content


def test_ics_marks_cancellation(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    appointment = _create_appointment(client, headers, lead["id"]).json()
    canceled = client.post(
        f"/api/v1/appointments/{appointment['id']}/disposition",
        json={"status": "canceled", "expected_revision": appointment["revision"]},
        headers=headers,
    )
    assert canceled.status_code == 200
    content = client.get(
        f"/api/v1/appointments/{appointment['id']}/calendar.ics", headers=headers
    ).text
    assert parse_ics(content)["STATUS"] == "CANCELLED"


def test_ics_folds_long_lines_at_75_octets(db) -> None:
    from app.models import CommunicationSettings

    appointment = Appointment(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        subject="A" * 200,
        start_at=datetime(2026, 9, 1, 14, 0, tzinfo=UTC),
        end_at=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
        timezone="UTC",
        status="scheduled",
        origin="staff",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    content = calendar_file.build_ics(appointment, CommunicationSettings(business_name="Acme"))
    for line in content.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, line[:40]
    assert parse_ics(content)["SUMMARY"] == "A" * 200


# --- attention queue -----------------------------------------------------


def test_attention_queue_includes_scheduling_states(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    lead = make_lead(client, headers)
    appointment = _create_appointment(client, headers, lead["id"], days=1).json()

    queue = client.get("/api/v1/leads/attention", headers=headers).json()
    assert [item["id"] for item in queue["appointments_upcoming"]] == [appointment["id"]]
    assert queue["appointments_overdue"] == []

    # Push it into the past without a disposition.
    row = db.scalar(select(Appointment))
    row.start_at = utcnow() - timedelta(hours=3)
    row.end_at = utcnow() - timedelta(hours=2)
    db.commit()
    queue = client.get("/api/v1/leads/attention", headers=headers).json()
    assert [item["id"] for item in queue["appointments_overdue"]] == [appointment["id"]]

    # A failed notification surfaces too.
    notification = db.scalar(select(AppointmentNotification))
    notification.state = "failed"
    notification.failure_message = "Unsubscribed recipient"
    db.commit()
    queue = client.get("/api/v1/leads/attention", headers=headers).json()
    assert queue["appointment_messages_failed"]
    assert "failed" in queue["appointment_messages_failed"][0]["detail"]


def test_scheduling_settings_are_owner_only(client, db, make_user, sms_sender) -> None:
    owner_session(client, make_user)
    for email, role in (("manager@example.com", "manager"), ("member@example.com", "team_member")):
        make_user(email=email, role=role)
        headers = csrf_headers(login(client, email))
        assert client.get("/api/v1/settings/scheduling", headers=headers).status_code == 403
        assert (
            client.patch(
                "/api/v1/settings/scheduling",
                json={"business_timezone": "UTC"},
                headers=headers,
            ).status_code
            == 403
        )
        client.post("/api/v1/auth/logout", headers=headers)


def test_scheduling_settings_validation(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    bad_zone = client.patch(
        "/api/v1/settings/scheduling", json={"business_timezone": "Mars/Olympus"}, headers=headers
    )
    assert bad_zone.status_code == 400

    bad_template = client.patch(
        "/api/v1/settings/scheduling",
        json={"reminder_template": "See you {{secret_field}}"},
        headers=headers,
    )
    assert bad_template.status_code == 400
    assert "secret_field" in bad_template.json()["detail"]

    bad_hours = client.patch(
        "/api/v1/settings/scheduling",
        json={"business_hours": {"mon": [["17:00", "09:00"]]}},
        headers=headers,
    )
    assert bad_hours.status_code == 400

    bad_duration = client.patch(
        "/api/v1/settings/scheduling", json={"appointment_duration_minutes": 0}, headers=headers
    )
    assert bad_duration.status_code == 422

    good = client.patch(
        "/api/v1/settings/scheduling",
        json={
            "business_timezone": "America/Chicago",
            "reminder_template": "Hi {{lead_name}}, see you {{appointment_date}}.",
            "business_hours": {"mon": [["08:00", "12:00"]]},
            "appointment_duration_minutes": 45,
        },
        headers=headers,
    )
    assert good.status_code == 200
    assert good.json()["business_timezone"] == "America/Chicago"
    assert good.json()["business_hours"]["mon"] == [["08:00", "12:00"]]
    _ = OutboundMessage


# --- staff-facing scheduling configuration -------------------------------


def test_scheduling_basics_are_readable_by_anyone_who_can_schedule(
    client, db, make_user, sms_sender
) -> None:
    """A team member has to know the business zone and duration to schedule,
    but must not see templates or notification configuration."""
    owner = owner_session(client, make_user)
    enable_scheduling(client, db, owner, business_timezone="America/New_York")
    make_user(email="tech@example.com", role="team_member")
    member = csrf_headers(login(client, "tech@example.com"))

    basics = client.get("/api/v1/settings/scheduling-basics", headers=member)
    assert basics.status_code == 200
    body = basics.json()
    assert body["business_timezone"] == "America/New_York"
    assert body["appointment_duration_minutes"] > 0
    assert set(body) == {
        "business_timezone",
        "appointment_duration_minutes",
        "min_booking_notice_minutes",
        "max_booking_days_ahead",
        "self_booking_enabled",
        "business_hours",
    }

    # The full settings, including templates, stay owner-only.
    assert client.get("/api/v1/settings/scheduling", headers=member).status_code == 403


def test_availability_can_exclude_the_appointment_being_moved(
    client, db, make_user, sms_sender
) -> None:
    """Rescheduling may reuse the time the appointment already occupies, but
    the exclusion only applies to an appointment the user is allowed to see."""
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers, buffer_before_minutes=0, buffer_after_minutes=0)
    lead = make_lead(client, headers, phone="+15550500090")
    owner_id = client.get("/api/v1/auth/session").json()["user"]["id"]

    day = (utcnow() + timedelta(days=3)).date().isoformat()
    free = client.get(
        f"/api/v1/appointments/availability?day={day}&staff_id={owner_id}", headers=headers
    ).json()["slots"]
    assert free, "the fixture day must offer times"
    chosen = free[len(free) // 2]

    created = client.post(
        f"/api/v1/leads/{lead['id']}/appointments",
        json={"start_at": chosen, "duration_minutes": 60, "assigned_to": owner_id},
        headers=headers,
    )
    assert created.status_code == 201
    appointment_id = created.json()["id"]

    after = client.get(
        f"/api/v1/appointments/availability?day={day}&staff_id={owner_id}", headers=headers
    ).json()["slots"]
    assert chosen not in after, "the booked time is no longer offered"

    while_moving = client.get(
        f"/api/v1/appointments/availability?day={day}&staff_id={owner_id}"
        f"&exclude_appointment_id={appointment_id}",
        headers=headers,
    ).json()["slots"]
    assert chosen in while_moving, "its own time is not a conflict when rescheduling"

    # An unknown id is refused rather than silently ignored.
    unknown = client.get(
        f"/api/v1/appointments/availability?day={day}&exclude_appointment_id={uuid.uuid4()}",
        headers=headers,
    )
    assert unknown.status_code == 404


# --- customer-side management capability ---------------------------------


def book_with_manage_token(client, db, headers, phone="+15550500070", day_index=0):
    """Book a slot as a customer would, returning the manage capability.

    `day_index` picks how far ahead to book: a later day leaves room for a
    24-hour reminder to actually be queued.
    """
    lead = make_lead(client, headers, phone=phone)
    raw = (
        client.post(f"/api/v1/leads/{lead['id']}/booking-link", json={}, headers=headers)
        .json()["url"]
        .rsplit("/", 1)[-1]
    )
    info = booking_info(client, raw).json()
    slot = info["days"][day_index]["slots"][0]
    result = client.post(
        "/api/v1/internal/booking/confirm",
        json={"token": raw, "start_at": slot, "booking_key": f"key-{phone}"},
        headers=internal_headers(),
    )
    assert result.status_code == 200, result.text
    return lead, result.json()


def test_knowing_the_appointment_uuid_grants_no_customer_access(
    client, db, make_user, sms_sender
) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    _, booked = book_with_manage_token(client, db, headers)

    appointment = db.scalar(select(Appointment))
    # The UUID is not a capability, and neither is the quotable reference.
    assert manage_info(client, str(appointment.id)).status_code == 404
    assert manage_cancel(client, str(appointment.id)).status_code == 404
    reference = booked["booking_reference"]
    assert manage_info(client, f"{reference}xxxxxxxxxxxxxx").status_code == 404

    # Only the issued capability opens it, and it is stored as a digest.
    token = booked["manage_token"]
    assert token
    assert appointment.manage_token_digest != token
    view = manage_info(client, token)
    assert view.status_code == 200
    body = view.json()
    assert body["booking_reference"] == reference
    assert body["can_change"] is True
    # Nothing about the lead or the CRM record leaks through.
    assert "lead_id" not in body and "notes" not in body and "subject" not in body
    assert "15550500070" not in view.text


def test_customer_cancellation_suppresses_reminders_and_frees_the_slot(
    client, db, make_user, sms_sender
) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    _, booked = book_with_manage_token(client, db, headers, phone="+15550500071")
    token = booked["manage_token"]

    canceled = manage_cancel(client, token)
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    assert canceled.json()["can_change"] is False

    appointment = db.scalar(select(Appointment))
    db.refresh(appointment)
    assert appointment.status == "canceled"
    assert appointment.cancellation_reason == "Canceled by the customer online."

    # No unsent reminder survives, and the history is kept rather than deleted.
    pending = db.scalars(
        select(AppointmentNotification).where(AppointmentNotification.state == "pending")
    ).all()
    assert pending == []
    assert db.scalar(select(func.count(AppointmentNotification.id))) > 0

    # The time is bookable again.
    day = appointment.start_at.date().isoformat()
    slots = client.get(f"/api/v1/appointments/availability?day={day}", headers=headers).json()[
        "slots"
    ]
    assert booked["start_at"].replace("+00:00", "Z").rstrip("Z") in "".join(slots).replace(
        "+00:00", ""
    ) or any(slot.startswith(booked["start_at"][:16]) for slot in slots)

    # A second cancellation changes nothing.
    again = manage_cancel(client, token)
    assert again.status_code in (200, 409)


def test_customer_reschedule_moves_the_time_and_replaces_reminders(
    client, db, make_user, sms_sender
) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    _, booked = book_with_manage_token(client, db, headers, phone="+15550500072", day_index=-1)
    token = booked["manage_token"]
    assert db.scalars(
        select(AppointmentNotification).where(
            AppointmentNotification.type == "reminder",
            AppointmentNotification.state == "pending",
        )
    ).all(), "a reminder must be waiting before the move, or the test proves nothing"

    offered = manage_info(client, token).json()["days"]
    later = [
        slot
        for day in offered[-1:]
        for slot in day["slots"]
        if slot[:19] != booked["start_at"][:19]
    ]
    assert later, "the customer must be offered an alternative time"

    revision = manage_info(client, token).json()["revision"]
    moved = manage_reschedule(client, token, later[0], revision)
    assert moved.status_code == 200
    assert moved.json()["start_at"][:19] == later[0][:19]

    # Replaying the exact same move with the OLD revision is idempotent: the
    # appointment stays put and no second rescheduled notice is queued.
    replay = manage_reschedule(client, token, later[0], revision)
    assert replay.status_code == 200
    rescheduled_count = len(
        db.scalars(
            select(AppointmentNotification).where(AppointmentNotification.type == "rescheduled")
        ).all()
    )
    assert rescheduled_count == 1

    # A genuinely stale different move is refused.
    other_slot = [slot for day in offered[-1:] for slot in day["slots"]][-1]
    stale = manage_reschedule(client, token, other_slot, revision)
    assert stale.status_code == 409

    # The customer is told, once, that the time changed.
    rescheduled = db.scalars(
        select(AppointmentNotification).where(AppointmentNotification.type == "rescheduled")
    ).all()
    assert len(rescheduled) == 1
    # Obsolete reminders for the old time are suppressed, never sent.
    suppressed = db.scalars(
        select(AppointmentNotification).where(
            AppointmentNotification.type == "reminder",
            AppointmentNotification.state == "suppressed",
        )
    ).all()
    assert suppressed

    # The honeypot is refused without touching the appointment.
    trapped = client.post(
        "/api/v1/internal/appointments/reschedule",
        json={
            "token": token,
            "start_at": later[0],
            "expected_revision": 99,
            "website": "http://spam.example",
        },
        headers=internal_headers(),
    )
    assert trapped.status_code == 422


def test_customer_cannot_reschedule_into_a_taken_slot(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    enable_scheduling(client, db, headers)
    _, booked = book_with_manage_token(client, db, headers, phone="+15550500073")
    token = booked["manage_token"]

    appointment = db.scalar(select(Appointment))
    original_start = appointment.start_at

    # Take one of the times the customer is being offered, from under them.
    offered = manage_info(client, token).json()["days"]
    candidates = [
        slot for day in offered for slot in day["slots"] if slot[:19] != booked["start_at"][:19]
    ]
    assert candidates
    taken = candidates[-1]
    other = make_lead(client, headers, phone="+15550500074", name="Other Lead")
    assert (
        client.post(
            f"/api/v1/leads/{other['id']}/appointments",
            json={"start_at": taken},
            headers=headers,
        ).status_code
        == 201
    )

    revision = manage_info(client, token).json()["revision"]
    refused = manage_reschedule(client, token, taken, revision)
    assert refused.status_code == 409
    db.refresh(appointment)
    assert appointment.start_at == original_start, "a refused move must change nothing"
