"""Appointment scheduling: availability, conflict prevention, lifecycle.

Every stored timestamp is UTC. The business time zone decides how those
instants are presented and how business hours are interpreted, and each
appointment keeps a snapshot of the zone it was scheduled under.
"""

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    APPOINTMENT_STATUSES,
    Appointment,
    CommunicationSettings,
    Lead,
    User,
    utcnow,
)
from app.services.leads import LeadError, add_activity, can_manage_leads

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_BUSINESS_HOURS = {
    "mon": [["09:00", "17:00"]],
    "tue": [["09:00", "17:00"]],
    "wed": [["09:00", "17:00"]],
    "thu": [["09:00", "17:00"]],
    "fri": [["09:00", "17:00"]],
    "sat": [],
    "sun": [],
}
_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Slot granularity when offering availability.
SLOT_STEP_MINUTES = 15


class SchedulingError(LeadError):
    """Rejected scheduling action; message is safe to return to the client."""


class SlotUnavailableError(SchedulingError):
    """The requested time is not bookable — distinct from a validation error."""

    def __init__(self, message: str = "That time is no longer available.") -> None:
        super().__init__(message, status_code=409)


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
        raise SchedulingError(f"Unknown time zone: {name!r}. Use an IANA name.") from error
    return name


def validate_business_hours(hours: dict | None) -> dict:
    """Normalize weekday windows, rejecting malformed or inverted ranges."""
    if hours is None:
        return dict(DEFAULT_BUSINESS_HOURS)
    if not isinstance(hours, dict):
        raise SchedulingError("Business hours must be a weekday mapping.")
    unknown = set(hours) - set(WEEKDAY_KEYS)
    if unknown:
        raise SchedulingError("Unknown weekday keys: " + ", ".join(sorted(unknown)))

    normalized: dict[str, list[list[str]]] = {}
    for key in WEEKDAY_KEYS:
        windows = hours.get(key, [])
        if not isinstance(windows, list):
            raise SchedulingError(f"Business hours for {key} must be a list of windows.")
        cleaned: list[list[str]] = []
        for window in windows:
            if not isinstance(window, list | tuple) or len(window) != 2:
                raise SchedulingError(f"Each {key} window needs a start and an end time.")
            start, end = str(window[0]), str(window[1])
            if not _TIME_PATTERN.match(start) or not _TIME_PATTERN.match(end):
                raise SchedulingError(f"Times for {key} must look like 09:00.")
            if start >= end:
                raise SchedulingError(f"The {key} window {start}-{end} ends before it starts.")
            cleaned.append([start, end])
        cleaned.sort()
        for earlier, later in zip(cleaned, cleaned[1:], strict=False):
            if later[0] < earlier[1]:
                raise SchedulingError(f"Overlapping {key} windows are not allowed.")
        normalized[key] = cleaned
    return normalized


def business_hours_of(settings_row: CommunicationSettings) -> dict:
    return settings_row.business_hours or dict(DEFAULT_BUSINESS_HOURS)


def local_to_utc(local_naive: datetime, zone: ZoneInfo) -> datetime | None:
    """Convert a naive business-local datetime to UTC.

    Returns None when the local time does not exist (the spring-forward gap).
    Ambiguous times during the autumn overlap resolve deterministically to the
    first (pre-transition, fold=0) occurrence.
    """
    aware = local_naive.replace(tzinfo=zone, fold=0)
    # A time inside the DST gap is normalized by zoneinfo to a different local
    # time; detecting that round-trip mismatch is how we identify the gap.
    round_trip = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != local_naive:
        return None
    return aware.astimezone(UTC)


def to_local(moment: datetime, timezone_name: str) -> datetime:
    return moment.astimezone(ZoneInfo(timezone_name))


def _blocked_range(
    appointment: Appointment, buffer_before: int, buffer_after: int
) -> tuple[datetime, datetime]:
    return (
        appointment.start_at - timedelta(minutes=buffer_before),
        appointment.end_at + timedelta(minutes=buffer_after),
    )


def scheduled_appointments(
    db: Session,
    staff_id: uuid.UUID | None,
    window_start: datetime,
    window_end: datetime,
    exclude_id: uuid.UUID | None = None,
) -> list[Appointment]:
    """Appointments that still occupy time. Canceled ones release their slot;
    completed and no-show ones remain historical but no longer block."""
    query = select(Appointment).where(
        Appointment.status == "scheduled",
        Appointment.start_at < window_end,
        Appointment.end_at > window_start,
    )
    query = query.where(
        Appointment.assigned_to == staff_id
        if staff_id is not None
        else Appointment.assigned_to.is_(None)
    )
    if exclude_id is not None:
        query = query.where(Appointment.id != exclude_id)
    return list(db.scalars(query))


def available_slots(
    db: Session,
    settings_row: CommunicationSettings,
    staff_id: uuid.UUID | None,
    day: date,
    duration_minutes: int | None = None,
    exclude_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> list[datetime]:
    """Bookable start instants (UTC) for one business-local day."""
    zone = ZoneInfo(settings_row.business_timezone)
    duration = duration_minutes or settings_row.appointment_duration_minutes
    now = now or utcnow()
    earliest = now + timedelta(minutes=settings_row.min_booking_notice_minutes)
    latest = now + timedelta(days=settings_row.max_booking_days_ahead)

    windows = business_hours_of(settings_row).get(WEEKDAY_KEYS[day.weekday()], [])
    if not windows:
        return []

    # Look at a generous span so appointments either side of midnight and
    # their buffers are considered.
    span_start = datetime.combine(day, time.min).replace(tzinfo=zone).astimezone(UTC) - timedelta(
        days=1
    )
    span_end = span_start + timedelta(days=3)
    existing = scheduled_appointments(db, staff_id, span_start, span_end, exclude_id)
    blocked = [
        _blocked_range(item, settings_row.buffer_before_minutes, settings_row.buffer_after_minutes)
        for item in existing
    ]

    slots: list[datetime] = []
    for window_start, window_end in windows:
        start_h, start_m = (int(part) for part in window_start.split(":"))
        end_h, end_m = (int(part) for part in window_end.split(":"))
        cursor = datetime.combine(day, time(start_h, start_m))
        window_close = datetime.combine(day, time(end_h, end_m))
        while cursor + timedelta(minutes=duration) <= window_close:
            start_utc = local_to_utc(cursor, zone)
            cursor += timedelta(minutes=SLOT_STEP_MINUTES)
            if start_utc is None:
                continue  # local time does not exist on this DST day
            end_utc = start_utc + timedelta(minutes=duration)
            if start_utc < earliest or start_utc > latest:
                continue
            candidate = (
                start_utc - timedelta(minutes=settings_row.buffer_before_minutes),
                end_utc + timedelta(minutes=settings_row.buffer_after_minutes),
            )
            if any(
                candidate[0] < busy_end and candidate[1] > busy_start
                for busy_start, busy_end in blocked
            ):
                continue
            slots.append(start_utc)
    return sorted(set(slots))


def lock_staff_calendar(db: Session, staff_id: uuid.UUID | None) -> None:
    """Serialize booking for one staff member.

    A transaction-scoped advisory lock keyed on the staff id means two
    concurrent bookings for the same person are evaluated one after the
    other, so the buffer-aware overlap check below cannot be raced. The lock
    is released by the transaction that follows — never while contacting a
    provider.
    """
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return  # SQLite unit tests; PostgreSQL behaviour is covered separately
    key = int(staff_id.int % (2**31)) if staff_id is not None else 0
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def assert_slot_free(
    db: Session,
    settings_row: CommunicationSettings,
    staff_id: uuid.UUID | None,
    start_at: datetime,
    end_at: datetime,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Buffer-aware overlap check. Call while holding the calendar lock."""
    before = timedelta(minutes=settings_row.buffer_before_minutes)
    after = timedelta(minutes=settings_row.buffer_after_minutes)
    conflicts = scheduled_appointments(
        db, staff_id, start_at - before - after, end_at + before + after, exclude_id
    )
    for existing in conflicts:
        busy_start, busy_end = _blocked_range(
            existing, settings_row.buffer_before_minutes, settings_row.buffer_after_minutes
        )
        if start_at - before < busy_end and end_at + after > busy_start:
            raise SlotUnavailableError()


def validate_window(
    settings_row: CommunicationSettings,
    start_at: datetime,
    end_at: datetime,
    now: datetime | None = None,
    enforce_notice: bool = True,
) -> None:
    now = now or utcnow()
    if end_at <= start_at:
        raise SchedulingError("The appointment must end after it starts.")
    if (end_at - start_at) > timedelta(hours=12):
        raise SchedulingError("Appointments longer than 12 hours are not supported.")
    if enforce_notice:
        earliest = now + timedelta(minutes=settings_row.min_booking_notice_minutes)
        if start_at < earliest:
            raise SchedulingError(
                f"Bookings need at least {settings_row.min_booking_notice_minutes} "
                "minutes of notice."
            )
        if start_at > now + timedelta(days=settings_row.max_booking_days_ahead):
            raise SchedulingError(
                f"Bookings can be made at most {settings_row.max_booking_days_ahead} days ahead."
            )


def _assert_staff_assignable(db: Session, acting_user: User, staff_id: uuid.UUID | None) -> None:
    if staff_id is None:
        return
    staff = db.get(User, staff_id)
    if staff is None or not staff.is_active:
        raise SchedulingError("Appointments can only be assigned to an active user.")
    if not can_manage_leads(acting_user) and staff_id != acting_user.id:
        raise SchedulingError("You can only schedule appointments for yourself.", status_code=403)


def assert_can_manage_appointments(acting_user: User, lead: Lead) -> None:
    """Team members may only touch appointments for their own leads."""
    if can_manage_leads(acting_user):
        return
    if lead.assigned_to != acting_user.id:
        raise SchedulingError(
            "You can only manage appointments for leads assigned to you.", status_code=403
        )


def create_appointment(
    db: Session,
    acting_user: User | None,
    lead: Lead,
    settings_row: CommunicationSettings,
    *,
    start_at: datetime,
    duration_minutes: int | None = None,
    subject: str = "Appointment",
    notes: str = "",
    staff_id: uuid.UUID | None = None,
    origin: str = "staff",
    booking_reference: str | None = None,
    manage_token_digest: str | None = None,
    enforce_notice: bool = True,
) -> Appointment:
    """Create a scheduled appointment, refusing to double-book.

    The caller must already hold the staff calendar lock for `staff_id`.
    """
    if lead.archived_at is not None:
        raise SchedulingError(
            "Restore this lead before scheduling an appointment.", status_code=409
        )
    if acting_user is not None:
        assert_can_manage_appointments(acting_user, lead)
        _assert_staff_assignable(db, acting_user, staff_id)
    duration = duration_minutes or settings_row.appointment_duration_minutes
    if duration <= 0 or duration > 12 * 60:
        raise SchedulingError("Appointment duration must be between 1 and 720 minutes.")
    end_at = start_at + timedelta(minutes=duration)
    validate_window(settings_row, start_at, end_at, enforce_notice=enforce_notice)
    assert_slot_free(db, settings_row, staff_id, start_at, end_at)

    appointment = Appointment(
        lead_id=lead.id,
        assigned_to=staff_id,
        subject=(subject or "Appointment").strip()[:200],
        notes=(notes or "").strip()[:2000],
        start_at=start_at,
        end_at=end_at,
        timezone=settings_row.business_timezone,
        status="scheduled",
        origin=origin,
        booking_reference=booking_reference,
        manage_token_digest=manage_token_digest,
        created_by=acting_user.id if acting_user is not None else None,
        updated_by=acting_user.id if acting_user is not None else None,
    )
    db.add(appointment)
    db.flush()
    add_activity(
        db,
        lead,
        "appointment_scheduled",
        f"Appointment scheduled: {appointment.subject} on {describe_when(appointment)}.",
        acting_user=acting_user,
        meta={"appointment_id": str(appointment.id), "origin": origin},
    )
    return appointment


def describe_when(appointment: Appointment) -> str:
    local = to_local(appointment.start_at, appointment.timezone)
    return local.strftime("%Y-%m-%d %H:%M %Z")


def reschedule_appointment(
    db: Session,
    acting_user: User | None,
    appointment: Appointment,
    settings_row: CommunicationSettings,
    *,
    start_at: datetime,
    duration_minutes: int | None = None,
    enforce_notice: bool = True,
) -> Appointment:
    if appointment.status != "scheduled":
        raise SchedulingError("Only a scheduled appointment can be rescheduled.", status_code=409)
    lead = db.get(Lead, appointment.lead_id)
    assert lead is not None
    if acting_user is not None:
        assert_can_manage_appointments(acting_user, lead)
    duration = duration_minutes or int(
        (appointment.end_at - appointment.start_at).total_seconds() // 60
    )
    end_at = start_at + timedelta(minutes=duration)
    validate_window(settings_row, start_at, end_at, enforce_notice=enforce_notice)
    assert_slot_free(
        db, settings_row, appointment.assigned_to, start_at, end_at, exclude_id=appointment.id
    )

    appointment.start_at = start_at
    appointment.end_at = end_at
    appointment.timezone = settings_row.business_timezone
    appointment.updated_by = acting_user.id if acting_user is not None else None
    db.flush()
    add_activity(
        db,
        lead,
        "appointment_rescheduled",
        f"Appointment moved to {describe_when(appointment)}.",
        acting_user=acting_user,
        meta={"appointment_id": str(appointment.id)},
    )
    return appointment


def set_disposition(
    db: Session,
    acting_user: User | None,
    appointment: Appointment,
    status: str,
    reason: str | None = None,
) -> Appointment:
    """Cancel, complete or mark no-show. History is never deleted."""
    if status not in APPOINTMENT_STATUSES or status == "scheduled":
        raise SchedulingError("Invalid appointment disposition.")
    if appointment.status == status:
        return appointment
    if appointment.status != "scheduled":
        raise SchedulingError(
            f"This appointment is already {appointment.status.replace('_', ' ')}.",
            status_code=409,
        )
    lead = db.get(Lead, appointment.lead_id)
    assert lead is not None
    if acting_user is not None:
        assert_can_manage_appointments(acting_user, lead)

    now = utcnow()
    appointment.status = status
    appointment.updated_by = acting_user.id if acting_user is not None else None
    if status == "canceled":
        appointment.canceled_at = now
        appointment.cancellation_reason = (reason or "").strip()[:500] or None
    elif status == "completed":
        appointment.completed_at = now
    elif status == "no_show":
        appointment.no_show_at = now
    db.flush()

    labels = {"canceled": "canceled", "completed": "completed", "no_show": "marked no-show"}
    add_activity(
        db,
        lead,
        f"appointment_{status}",
        f"Appointment {labels[status]}: {appointment.subject} on {describe_when(appointment)}.",
        acting_user=acting_user,
        meta={"appointment_id": str(appointment.id)},
    )
    return appointment


def visible_appointments_query(user: User):
    """Appointments the user may see, following the lead-access rules."""
    query = select(Appointment).join(Lead, Lead.id == Appointment.lead_id)
    if not can_manage_leads(user):
        query = query.where(Lead.assigned_to == user.id)
    return query


def get_visible_appointment(db: Session, user: User, appointment_id: uuid.UUID) -> Appointment:
    appointment = db.scalar(
        visible_appointments_query(user).where(Appointment.id == appointment_id)
    )
    if appointment is None:
        raise SchedulingError("Appointment not found.", status_code=404)
    return appointment
