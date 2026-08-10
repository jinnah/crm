"""Availability, time zones, DST and appointment authorization."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models import Appointment, CommunicationSettings, Lead, utcnow
from app.services import scheduling
from app.services.scheduling import SchedulingError, SlotUnavailableError
from tests.conftest import csrf_headers, login

NY = "America/New_York"


def settings_for(db, **overrides) -> CommunicationSettings:
    from app.services.messaging import get_settings_row

    row = get_settings_row(db)
    row.business_timezone = overrides.pop("business_timezone", NY)
    row.appointment_duration_minutes = overrides.pop("appointment_duration_minutes", 60)
    row.min_booking_notice_minutes = overrides.pop("min_booking_notice_minutes", 0)
    row.max_booking_days_ahead = overrides.pop("max_booking_days_ahead", 365)
    row.buffer_before_minutes = overrides.pop("buffer_before_minutes", 0)
    row.buffer_after_minutes = overrides.pop("buffer_after_minutes", 0)
    row.business_hours = overrides.pop(
        "business_hours", {key: [["09:00", "17:00"]] for key in scheduling.WEEKDAY_KEYS}
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    db.commit()
    return row


def make_lead(db, **overrides) -> Lead:
    lead = Lead(name="Sched Lead", phone="+15550400001", source="manual", **overrides)
    db.add(lead)
    db.commit()
    return lead


# --- time zone and DST ---------------------------------------------------


def test_timezone_validation() -> None:
    assert scheduling.validate_timezone("America/New_York") == "America/New_York"
    assert scheduling.validate_timezone("UTC") == "UTC"
    for bad in ("Mars/Olympus", "EST5EDT-nonsense", "", "not a zone"):
        with pytest.raises(SchedulingError):
            scheduling.validate_timezone(bad)


def test_local_times_inside_the_spring_gap_do_not_exist() -> None:
    zone = ZoneInfo(NY)
    # 2026-03-08 02:30 America/New_York never happens (clocks jump 02:00->03:00).
    assert scheduling.local_to_utc(datetime(2026, 3, 8, 2, 30), zone) is None
    # The hour either side does exist.
    assert scheduling.local_to_utc(datetime(2026, 3, 8, 1, 30), zone) is not None
    assert scheduling.local_to_utc(datetime(2026, 3, 8, 3, 30), zone) is not None


def test_repeated_autumn_local_time_resolves_deterministically() -> None:
    zone = ZoneInfo(NY)
    # 2026-11-01 01:30 happens twice; we always take the first occurrence.
    first = scheduling.local_to_utc(datetime(2026, 11, 1, 1, 30), zone)
    again = scheduling.local_to_utc(datetime(2026, 11, 1, 1, 30), zone)
    assert first == again
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # EDT, the earlier one


def test_availability_skips_the_dst_gap(db) -> None:
    row = settings_for(
        db, business_hours={key: [["00:00", "06:00"]] for key in scheduling.WEEKDAY_KEYS}
    )
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    slots = scheduling.available_slots(
        db, row, None, date(2026, 3, 8), duration_minutes=30, now=now
    )
    local_times = {scheduling.to_local(slot, NY).strftime("%H:%M") for slot in slots}
    # No slot may start in the 02:00-02:59 window that does not exist.
    assert not any(value.startswith("02:") for value in local_times)
    assert "01:00" in local_times and "03:00" in local_times


def test_appointment_keeps_its_timezone_snapshot(db, make_user) -> None:
    row = settings_for(db)
    lead = make_lead(db)
    start = utcnow() + timedelta(days=2)
    appointment = scheduling.create_appointment(
        db, None, lead, row, start_at=start.replace(microsecond=0)
    )
    db.commit()
    assert appointment.timezone == NY
    # Changing the business zone later does not rewrite history.
    row.business_timezone = "UTC"
    db.commit()
    db.refresh(appointment)
    assert appointment.timezone == NY


# --- availability and buffers -------------------------------------------


def test_business_hours_validation() -> None:
    good = scheduling.validate_business_hours({"mon": [["09:00", "12:00"], ["13:00", "17:00"]]})
    assert good["mon"] == [["09:00", "12:00"], ["13:00", "17:00"]]
    assert good["sun"] == []
    for bad in (
        {"mon": [["17:00", "09:00"]]},
        {"mon": [["9:00", "17:00"]]},
        {"funday": [["09:00", "10:00"]]},
        {"mon": [["09:00", "12:00"], ["11:00", "13:00"]]},
        {"mon": ["09:00"]},
    ):
        with pytest.raises(SchedulingError):
            scheduling.validate_business_hours(bad)


def test_slots_respect_business_hours_and_notice(db) -> None:
    row = settings_for(
        db,
        min_booking_notice_minutes=120,
        business_hours={
            "mon": [["09:00", "11:00"]],
            "tue": [],
            "wed": [],
            "thu": [],
            "fri": [],
            "sat": [],
            "sun": [],
        },
    )
    monday = date(2026, 6, 8)  # a Monday
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    slots = scheduling.available_slots(db, row, None, monday, duration_minutes=60, now=now)
    local = [scheduling.to_local(slot, NY).strftime("%H:%M") for slot in slots]
    assert local == ["09:00", "09:15", "09:30", "09:45", "10:00"]
    # A closed weekday offers nothing.
    assert scheduling.available_slots(db, row, None, date(2026, 6, 9), now=now) == []


def test_buffers_block_back_to_back_slots(db, make_user) -> None:
    row = settings_for(db, buffer_after_minutes=30)
    staff = make_user(email="tech@example.com", role="team_member")
    lead = make_lead(db)
    monday = date(2026, 6, 8)
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    booked_start = scheduling.local_to_utc(datetime(2026, 6, 8, 10, 0), ZoneInfo(NY))
    scheduling.create_appointment(
        db,
        None,
        lead,
        row,
        start_at=booked_start,
        duration_minutes=60,
        staff_id=staff.id,
        enforce_notice=False,  # seeding a fixed calendar date
    )
    db.commit()

    slots = scheduling.available_slots(db, row, staff.id, monday, duration_minutes=60, now=now)
    local = {scheduling.to_local(slot, NY).strftime("%H:%M") for slot in slots}
    # 10:00-11:00 is taken and the 30 minute buffer pushes the next start to 11:30.
    assert "10:00" not in local and "10:30" not in local and "11:00" not in local
    assert "11:30" in local
    # A different staff member is unaffected.
    other = scheduling.available_slots(db, row, None, monday, duration_minutes=60, now=now)
    assert scheduling.to_local(other[0], NY).strftime("%H:%M") == "09:00"


def test_canceled_appointment_releases_its_slot(db, make_user) -> None:
    row = settings_for(db)
    staff = make_user(email="tech@example.com", role="team_member")
    lead = make_lead(db)
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    start = scheduling.local_to_utc(datetime(2026, 6, 8, 10, 0), ZoneInfo(NY))
    appointment = scheduling.create_appointment(
        db,
        None,
        lead,
        row,
        start_at=start,
        duration_minutes=60,
        staff_id=staff.id,
        enforce_notice=False,  # seeding a fixed calendar date
    )
    db.commit()

    taken = scheduling.available_slots(db, row, staff.id, date(2026, 6, 8), 60, now=now)
    assert start not in taken

    scheduling.set_disposition(db, None, appointment, "canceled", "customer called")
    db.commit()
    released = scheduling.available_slots(db, row, staff.id, date(2026, 6, 8), 60, now=now)
    assert start in released


def test_completed_and_no_show_remain_historical(db, make_user) -> None:
    row = settings_for(db)
    staff = make_user(email="tech@example.com", role="team_member")
    lead = make_lead(db)
    start = scheduling.local_to_utc(datetime(2026, 6, 8, 10, 0), ZoneInfo(NY))
    appointment = scheduling.create_appointment(
        db,
        None,
        lead,
        row,
        start_at=start,
        duration_minutes=60,
        staff_id=staff.id,
        enforce_notice=False,  # seeding a fixed calendar date
    )
    db.commit()
    scheduling.set_disposition(db, None, appointment, "completed")
    db.commit()
    # Still stored, and it no longer blocks the calendar.
    assert db.scalar(select(Appointment).where(Appointment.id == appointment.id)) is not None
    assert appointment.completed_at is not None
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    assert start in scheduling.available_slots(db, row, staff.id, date(2026, 6, 8), 60, now=now)


def test_overlapping_booking_is_rejected(db, make_user) -> None:
    row = settings_for(db)
    staff = make_user(email="tech@example.com", role="team_member")
    lead = make_lead(db)
    start = utcnow() + timedelta(days=3)
    scheduling.create_appointment(
        db, None, lead, row, start_at=start, duration_minutes=60, staff_id=staff.id
    )
    db.commit()
    with pytest.raises(SlotUnavailableError) as error:
        scheduling.create_appointment(
            db,
            None,
            lead,
            row,
            start_at=start + timedelta(minutes=30),
            duration_minutes=60,
            staff_id=staff.id,
        )
    assert error.value.status_code == 409


def test_validation_errors_are_distinct_from_unavailability(db) -> None:
    row = settings_for(db, min_booking_notice_minutes=120)
    lead = make_lead(db)
    # In the past / inside the notice window: a validation error, not a conflict.
    with pytest.raises(SchedulingError) as error:
        scheduling.create_appointment(db, None, lead, row, start_at=utcnow() + timedelta(minutes=5))
    assert error.value.status_code == 400
    with pytest.raises(SchedulingError) as far:
        scheduling.create_appointment(db, None, lead, row, start_at=utcnow() + timedelta(days=400))
    assert far.value.status_code == 400


def test_archived_lead_cannot_be_scheduled(db) -> None:
    row = settings_for(db)
    lead = make_lead(db, archived_at=utcnow())
    with pytest.raises(SchedulingError) as error:
        scheduling.create_appointment(db, None, lead, row, start_at=utcnow() + timedelta(days=2))
    assert error.value.status_code == 409


# --- authorization -------------------------------------------------------


def appointment_payload(**overrides) -> dict:
    payload = {
        "start_at": (utcnow() + timedelta(days=3)).replace(microsecond=0).isoformat(),
        "duration_minutes": 60,
        "subject": "Site visit",
    }
    payload.update(overrides)
    return payload


def test_owner_creates_and_team_member_is_isolated(client, db, make_user, sms_sender) -> None:
    make_user(email="owner@example.com", role="owner")
    owner = csrf_headers(login(client, "owner@example.com"))
    settings_for(db)
    member = make_user(email="member@example.com", role="team_member")
    make_user(email="other@example.com", role="team_member")

    mine = client.post(
        "/api/v1/leads",
        json={"name": "Mine", "phone": "+15550400002"},
        headers=owner,
    ).json()
    theirs = client.post(
        "/api/v1/leads", json={"name": "Theirs", "phone": "+15550400003"}, headers=owner
    ).json()
    client.post(
        f"/api/v1/leads/{mine['id']}/assign", json={"user_id": str(member.id)}, headers=owner
    )

    created = client.post(
        f"/api/v1/leads/{mine['id']}/appointments",
        json=appointment_payload(assigned_to=str(member.id)),
        headers=owner,
    )
    assert created.status_code == 201, created.text
    assert created.json()["assignee_email"] == "member@example.com"
    appointment_id = created.json()["id"]

    member_headers = csrf_headers(login(client, "member@example.com"))
    # Their own lead's appointment is visible…
    assert (
        client.get(f"/api/v1/appointments/{appointment_id}", headers=member_headers).status_code
        == 200
    )
    # …but another user's lead is not.
    blocked = client.post(
        f"/api/v1/leads/{theirs['id']}/appointments",
        json=appointment_payload(),
        headers=member_headers,
    )
    assert blocked.status_code == 404
    assert (
        client.get(f"/api/v1/leads/{theirs['id']}/appointments", headers=member_headers).status_code
        == 404
    )


def test_team_member_cannot_schedule_another_staff_member(client, db, make_user, sms_sender):
    make_user(email="owner@example.com", role="owner")
    owner = csrf_headers(login(client, "owner@example.com"))
    settings_for(db)
    member = make_user(email="member@example.com", role="team_member")
    other = make_user(email="other@example.com", role="team_member")
    lead = client.post(
        "/api/v1/leads", json={"name": "Mine", "phone": "+15550400004"}, headers=owner
    ).json()
    client.post(
        f"/api/v1/leads/{lead['id']}/assign", json={"user_id": str(member.id)}, headers=owner
    )

    member_headers = csrf_headers(login(client, "member@example.com"))
    response = client.post(
        f"/api/v1/leads/{lead['id']}/appointments",
        json=appointment_payload(assigned_to=str(other.id)),
        headers=member_headers,
    )
    assert response.status_code == 403


def test_inactive_staff_cannot_be_assigned(client, db, make_user, sms_sender) -> None:
    make_user(email="owner@example.com", role="owner")
    owner = csrf_headers(login(client, "owner@example.com"))
    settings_for(db)
    inactive = make_user(email="gone@example.com", role="team_member", is_active=False)
    lead = client.post(
        "/api/v1/leads", json={"name": "Lead", "phone": "+15550400005"}, headers=owner
    ).json()
    response = client.post(
        f"/api/v1/leads/{lead['id']}/appointments",
        json=appointment_payload(assigned_to=str(inactive.id)),
        headers=owner,
    )
    assert response.status_code == 400


def test_lifecycle_and_history_on_archived_lead(client, db, make_user, sms_sender) -> None:
    make_user(email="owner@example.com", role="owner")
    owner = csrf_headers(login(client, "owner@example.com"))
    settings_for(db)
    lead = client.post(
        "/api/v1/leads", json={"name": "Lead", "phone": "+15550400006"}, headers=owner
    ).json()
    appointment = client.post(
        f"/api/v1/leads/{lead['id']}/appointments", json=appointment_payload(), headers=owner
    ).json()

    done = client.post(
        f"/api/v1/appointments/{appointment['id']}/disposition",
        json={"status": "completed", "expected_revision": appointment["revision"]},
        headers=owner,
    )
    assert done.status_code == 200 and done.json()["status"] == "completed"
    # A second, different disposition on a settled appointment is refused —
    # whether it carries the new revision (transition table) or a stale one.
    assert (
        client.post(
            f"/api/v1/appointments/{appointment['id']}/disposition",
            json={"status": "no_show", "expected_revision": done.json()["revision"]},
            headers=owner,
        ).status_code
        == 409
    )
    # Repeating the SAME disposition is an idempotent replay, not an error.
    replay = client.post(
        f"/api/v1/appointments/{appointment['id']}/disposition",
        json={"status": "completed", "expected_revision": done.json()["revision"]},
        headers=owner,
    )
    assert replay.status_code == 200 and replay.json()["revision"] == done.json()["revision"]

    # History stays readable once the lead is archived.
    client.post(f"/api/v1/leads/{lead['id']}/archive", headers=owner)
    history = client.get(f"/api/v1/leads/{lead['id']}/appointments", headers=owner)
    assert history.status_code == 200 and len(history.json()) == 1
    # But a new appointment needs the lead restored first.
    blocked = client.post(
        f"/api/v1/leads/{lead['id']}/appointments", json=appointment_payload(), headers=owner
    )
    assert blocked.status_code == 409
