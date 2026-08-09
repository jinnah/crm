"""Public customer booking, reached only through an opaque capability token.

No endpoint here accepts a lead or user identifier as authority: the token in
the path is the only thing that grants access, and the response exposes just
presentation data plus free slots.
"""

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from sqlalchemy import select

from app.api.v1.deps import DbDep, SettingsDep
from app.api.v1.schemas import (
    AvailabilityOut,
    PublicAppointmentOut,
    PublicBookingInfoOut,
    PublicBookingRequest,
    PublicBookingResultOut,
    PublicRescheduleRequest,
)
from app.models import Appointment, Lead, User, utcnow
from app.security.tokens import digest_token
from app.services import appointment_notifications as notifications
from app.services import booking, messaging, scheduling
from app.services.leads import LeadError
from app.services.scheduling import SchedulingError

router = APIRouter(prefix="/public/book", tags=["public"])

# Managing an existing appointment is a separate capability, issued per
# appointment when it is booked. An appointment's UUID grants nothing here.
manage_router = APIRouter(prefix="/public/appointments", tags=["public"])

DAYS_OFFERED = 14


def _http(error: LeadError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


def _offered_days(db, settings_row, staff_id, duration, exclude_id=None) -> list[AvailabilityOut]:
    today = scheduling.to_local(utcnow(), settings_row.business_timezone).date()
    days: list[AvailabilityOut] = []
    for offset in range(DAYS_OFFERED):
        day = today + timedelta(days=offset)
        slots = scheduling.available_slots(
            db, settings_row, staff_id, day, duration_minutes=duration, exclude_id=exclude_id
        )
        if slots:
            days.append(
                AvailabilityOut(
                    date=day.isoformat(),
                    timezone=settings_row.business_timezone,
                    duration_minutes=duration,
                    slots=slots,
                )
            )
    return days


def _staff_display_name(db: DbDep, staff_id: uuid.UUID | None) -> str | None:
    """Only a display name — never a staff email address."""
    if staff_id is None:
        return None
    staff = db.get(User, staff_id)
    if staff is None:
        return None
    return staff.email.split("@")[0].replace(".", " ").title()


@router.get("/{token}", response_model=PublicBookingInfoOut)
def booking_info(
    token: Annotated[str, Path(min_length=16, max_length=200)],
    db: DbDep,
    settings: SettingsDep,
) -> PublicBookingInfoOut:
    try:
        link = booking.resolve_token(db, token, settings)
    except LeadError as error:
        raise _http(error) from error

    settings_row = messaging.get_settings_row(db)
    if not settings_row.self_booking_enabled:
        raise HTTPException(status_code=403, detail="Online booking is not available.")
    duration = link.duration_minutes or settings_row.appointment_duration_minutes
    days = _offered_days(db, settings_row, link.assigned_to, duration)
    db.commit()
    return PublicBookingInfoOut(
        business_name=settings_row.business_name,
        intro=settings_row.form_intro,
        staff_display_name=_staff_display_name(db, link.assigned_to),
        duration_minutes=duration,
        timezone=settings_row.business_timezone,
        days=days,
    )


@router.post("/{token}", response_model=PublicBookingResultOut)
def confirm_booking(
    token: Annotated[str, Path(min_length=16, max_length=200)],
    body: PublicBookingRequest,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
) -> PublicBookingResultOut:
    """Book a slot. Availability is re-checked inside the transaction, and a
    repeated booking key returns the original appointment."""
    if body.website:
        # Honeypot: acknowledge without creating anything.
        raise HTTPException(status_code=422, detail="Unable to book that time.")
    try:
        link = booking.resolve_token(db, token, settings)
        settings_row = messaging.get_settings_row(db)
        if not settings_row.self_booking_enabled:
            raise SchedulingError("Online booking is not available.", status_code=403)

        lead = db.get(Lead, link.lead_id)
        if lead is None:
            raise SchedulingError("This booking link is not valid.", status_code=404)

        # Same booking key: return the original result, create nothing new.
        reference_digest = digest_token(
            f"booking:{link.id}:{body.booking_key}", settings.session_token_pepper
        )
        existing = db.scalar(
            select(Appointment).where(Appointment.booking_key_digest == reference_digest)
        )
        if existing is not None:
            return PublicBookingResultOut(
                booking_reference=existing.booking_reference or "",
                start_at=existing.start_at,
                end_at=existing.end_at,
                timezone=existing.timezone,
                duplicate=True,
            )

        duration = link.duration_minutes or settings_row.appointment_duration_minutes
        # Serialize this staff calendar, then re-check availability for real.
        scheduling.lock_staff_calendar(db, link.assigned_to)
        manage_raw, manage_digest = booking.issue_manage_token(settings)
        appointment = scheduling.create_appointment(
            db,
            None,
            lead,
            settings_row,
            start_at=body.start_at,
            duration_minutes=duration,
            subject="Booked appointment",
            staff_id=link.assigned_to,
            origin="customer",
            booking_reference=booking.new_booking_reference(),
            manage_token_digest=manage_digest,
        )
        # Bind the idempotency key so a repeated submission is detected.
        appointment.booking_key_digest = reference_digest
        db.flush()
        link.last_used_at = utcnow()
        notifications.schedule_for_appointment(db, appointment, settings_row, settings)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()  # durable before any confirmation is sent

    try:
        from app.api.v1.deps import get_sms_sender

        notifications.dispatch_due(db, settings, get_sms_sender(request), limit=5)
    except Exception:  # a failed confirmation never undoes the booking
        db.rollback()

    return PublicBookingResultOut(
        booking_reference=appointment.booking_reference or "",
        start_at=appointment.start_at,
        end_at=appointment.end_at,
        timezone=appointment.timezone,
        manage_token=manage_raw,
    )


# --- Customer-side management -------------------------------------------
#
# Every route below is reached only with the capability issued when the
# appointment was booked. Nothing here accepts an appointment id, a lead id
# or a user id, so knowing a UUID grants no access at all.


def _dispatch_after_commit(db, settings, request: Request) -> None:
    try:
        from app.api.v1.deps import get_sms_sender

        notifications.dispatch_due(db, settings, get_sms_sender(request), limit=5)
    except Exception:  # a failed message never undoes a stored change
        db.rollback()


def _serialize_public(
    db, appointment: Appointment, settings_row, days: list[AvailabilityOut]
) -> PublicAppointmentOut:
    return PublicAppointmentOut(
        business_name=settings_row.business_name,
        staff_display_name=_staff_display_name(db, appointment.assigned_to),
        booking_reference=appointment.booking_reference or "",
        start_at=appointment.start_at,
        end_at=appointment.end_at,
        timezone=appointment.timezone,
        status=appointment.status,
        can_change=appointment.status == "scheduled",
        days=days,
    )


def _resolve_for_management(db, token: str, settings) -> Appointment:
    try:
        return booking.resolve_manage_token(db, token, settings)
    except LeadError as error:
        raise _http(error) from error


@manage_router.get("/{token}", response_model=PublicAppointmentOut)
def appointment_info(
    token: Annotated[str, Path(min_length=16, max_length=200)],
    db: DbDep,
    settings: SettingsDep,
) -> PublicAppointmentOut:
    appointment = _resolve_for_management(db, token, settings)
    settings_row = messaging.get_settings_row(db)
    duration = int((appointment.end_at - appointment.start_at).total_seconds() // 60)
    days: list[AvailabilityOut] = []
    if appointment.status == "scheduled" and settings_row.self_booking_enabled:
        days = _offered_days(
            db, settings_row, appointment.assigned_to, duration, exclude_id=appointment.id
        )
    db.commit()
    return _serialize_public(db, appointment, settings_row, days)


@manage_router.post("/{token}/cancel", response_model=PublicAppointmentOut)
def cancel_appointment(
    token: Annotated[str, Path(min_length=16, max_length=200)],
    request: Request,
    db: DbDep,
    settings: SettingsDep,
) -> PublicAppointmentOut:
    appointment = _resolve_for_management(db, token, settings)
    try:
        settings_row = messaging.get_settings_row(db)
        scheduling.set_disposition(
            db, None, appointment, "canceled", "Canceled by the customer online."
        )
        # Obsolete reminders never go out; the business is told once.
        notifications.suppress_pending(db, appointment.id, ("reminder", "confirmation"))
        if settings_row.appointment_confirmation_enabled:
            notifications.queue_immediate(
                db, appointment, settings, type_="canceled", occurrence="1"
            )
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()  # the cancellation is durable before any message is sent

    _dispatch_after_commit(db, settings, request)
    return _serialize_public(db, appointment, messaging.get_settings_row(db), [])


@manage_router.post("/{token}/reschedule", response_model=PublicAppointmentOut)
def reschedule_appointment(
    token: Annotated[str, Path(min_length=16, max_length=200)],
    body: PublicRescheduleRequest,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
) -> PublicAppointmentOut:
    if body.website:
        raise HTTPException(status_code=422, detail="Unable to move that appointment.")
    appointment = _resolve_for_management(db, token, settings)
    try:
        settings_row = messaging.get_settings_row(db)
        if not settings_row.self_booking_enabled:
            raise SchedulingError("Online booking is not available.", status_code=403)
        # Serialize this staff calendar, then re-check availability for real.
        scheduling.lock_staff_calendar(db, appointment.assigned_to)
        generation = str(int(appointment.updated_at.timestamp()))
        scheduling.reschedule_appointment(
            db, None, appointment, settings_row, start_at=body.start_at
        )
        notifications.suppress_pending(db, appointment.id, ("reminder",))
        notifications.queue_immediate(
            db, appointment, settings, type_="rescheduled", occurrence=generation
        )
        notifications.schedule_for_appointment(
            db,
            appointment,
            settings_row,
            settings,
            include_confirmation=False,
            generation=generation,
        )
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()

    _dispatch_after_commit(db, settings, request)
    return _serialize_public(db, appointment, messaging.get_settings_row(db), [])
