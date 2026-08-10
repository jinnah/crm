"""The central bookable-slot proof: everything not exactly offered is refused."""

import logging
from datetime import timedelta

from sqlalchemy import func, select

from app.models import Appointment, utcnow
from tests.conftest import TEST_INTERNAL_KEY, csrf_headers, internal_headers, login
from tests.test_scheduling import settings_for


def owner_session(client, make_user):
    make_user(email="owner@example.com", role="owner")
    return csrf_headers(login(client, "owner@example.com"))


def setup_link(client, db, headers, phone="+15550600010"):
    settings_for(
        db,
        business_timezone="UTC",
        min_booking_notice_minutes=120,
        max_booking_days_ahead=30,
        business_hours={
            "mon": [["09:00", "17:00"]],
            "tue": [["09:00", "17:00"]],
            "wed": [["09:00", "17:00"]],
            "thu": [["09:00", "17:00"]],
            "fri": [["09:00", "17:00"]],
            "sat": [],
            "sun": [],
        },
    )
    client.patch(
        "/api/v1/settings/scheduling", json={"self_booking_enabled": True}, headers=headers
    )
    lead = client.post(
        "/api/v1/leads", json={"name": "Slot Lead", "phone": phone}, headers=headers
    ).json()
    raw = (
        client.post(f"/api/v1/leads/{lead['id']}/booking-link", json={}, headers=headers)
        .json()["url"]
        .rsplit("/", 1)[-1]
    )
    return lead, raw


def confirm(client, token, start_at, key="slot-key-000001"):
    return client.post(
        "/api/v1/internal/booking/confirm",
        json={"token": token, "start_at": start_at, "booking_key": key},
        headers=internal_headers(),
    )


def offered_slots(client, token):
    info = client.post(
        "/api/v1/internal/booking/info",
        json={"token": token, "days": 14},
        headers=internal_headers(),
    ).json()
    return [slot for day in info["days"] for slot in day["slots"]]


def test_off_hours_off_grid_naive_expired_and_window_are_all_rejected(
    client, db, make_user, sms_sender
) -> None:
    headers = owner_session(client, make_user)
    _, token = setup_link(client, db, headers)
    slots = offered_slots(client, token)
    assert slots, "the fixture must offer times"
    good = slots[len(slots) // 2]

    # Naive timestamp: rejected at validation, before any calendar work.
    naive = confirm(client, token, good.replace("Z", "").replace("+00:00", ""))
    assert naive.status_code == 422

    # Off the 15-minute grid.
    base = slots[0]
    off_grid = base.replace(":00:00", ":07:00") if ":00:00" in base else base
    if off_grid != base:
        assert confirm(client, token, off_grid).status_code == 409

    # Outside business hours: 03:00 UTC is never open in the fixture.
    day = good[:10]
    assert confirm(client, token, f"{day}T03:00:00Z").status_code == 409

    # Sunday is closed.
    sunday = None
    probe = utcnow() + timedelta(days=3)
    for _ in range(8):
        if probe.weekday() == 6:
            sunday = probe.date().isoformat()
            break
        probe += timedelta(days=1)
    assert sunday is not None
    assert confirm(client, token, f"{sunday}T10:00:00Z").status_code == 409

    # Expired: a moment in the past.
    past = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%dT10:00:00Z")
    assert confirm(client, token, past).status_code == 409

    # Inside the minimum-notice window.
    soon = (utcnow() + timedelta(minutes=30)).replace(microsecond=0)
    assert confirm(client, token, soon.isoformat()).status_code == 409

    # Beyond the configured window (30 days).
    far = (utcnow() + timedelta(days=45)).strftime("%Y-%m-%dT10:00:00Z")
    assert confirm(client, token, far).status_code == 409

    # Nothing was created by any of the rejected attempts.
    assert db.scalar(select(func.count()).select_from(Appointment)) == 0

    # The exactly offered slot books.
    assert confirm(client, token, good).status_code == 200
    assert db.scalar(select(func.count()).select_from(Appointment)) == 1


def test_a_slot_stops_being_offered_once_taken(client, db, make_user, sms_sender) -> None:
    """Stale offers are refused: what one customer books, the next cannot."""
    headers = owner_session(client, make_user)
    _, token = setup_link(client, db, headers, phone="+15550600011")
    slots = offered_slots(client, token)
    chosen = slots[0]
    assert confirm(client, token, chosen, key="first-key-000001").status_code == 200

    stale = confirm(client, token, chosen, key="second-key-000001")
    assert stale.status_code == 409
    assert db.scalar(select(func.count()).select_from(Appointment)) == 1


def test_availability_pages_across_the_configured_window(client, db, make_user, sms_sender) -> None:
    headers = owner_session(client, make_user)
    _, token = setup_link(client, db, headers, phone="+15550600012")

    first = client.post(
        "/api/v1/internal/booking/info",
        json={"token": token, "days": 7},
        headers=internal_headers(),
    ).json()
    assert first["window_days"] == 30
    assert first["next_start_day"] is not None

    second = client.post(
        "/api/v1/internal/booking/info",
        json={"token": token, "start_day": first["next_start_day"], "days": 31},
        headers=internal_headers(),
    ).json()
    # The second page continues where the first stopped and, being larger
    # than the remaining window, exhausts it.
    assert second["days"], "the rest of the window has open days"
    assert second["days"][0]["date"] >= first["next_start_day"]
    assert second["next_start_day"] is None


def test_capability_requests_never_reach_the_logs(
    client, db, make_user, sms_sender, caplog
) -> None:
    """Tokens, the internal credential, chosen times and phone numbers are
    absent from every log record produced while booking."""
    headers = owner_session(client, make_user)
    phone = "+15550600013"
    _, token = setup_link(client, db, headers, phone=phone)
    slots = offered_slots(client, token)
    chosen = slots[0]

    with caplog.at_level(logging.DEBUG):
        assert confirm(client, token, chosen, key="log-proof-key-01").status_code == 200
        # A rejected attempt logs nothing sensitive either.
        confirm(client, token, chosen, key="log-proof-key-02")

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert token not in logged
    assert TEST_INTERNAL_KEY not in logged
    assert chosen not in logged
    assert phone not in logged
    assert "log-proof-key" not in logged
