"""Internal capability endpoints for customer self-service booking.

These are reachable only by the Next.js BFF, which presents the server-only
internal credential. Paths are fixed and capabilities travel in request
bodies, so neither application nor proxy access logs ever see a token. No
endpoint accepts a lead, user or appointment identifier as authority — the
capability in the body is the only thing that grants access.

Nothing in this module logs request content: tokens, chosen times, phone
numbers and booking keys stay out of every log record.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1.deps import DbDep, SettingsDep, get_sms_sender, require_internal_key
from app.api.v1.schemas import (
    AvailabilityOut,
    InternalBookingConfirmRequest,
    InternalBookingInfoRequest,
    InternalManageRequest,
    InternalManageRescheduleRequest,
    PublicAppointmentOut,
    PublicBookingInfoOut,
    PublicBookingResultOut,
)
from app.models import Appointment, Lead, User, utcnow
from app.security.tokens import digest_token
from app.services import appointment_notifications as notifications
from app.services import booking, messaging, scheduling
from app.services.leads import LeadError
from app.services.scheduling import SchedulingError, SlotUnavailableError

router = APIRouter(
    prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_key)]
)


def _http(error: LeadError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


def _staff_display_name(db: DbDep, staff_id: uuid.UUID | None) -> str | None:
    """A display name only — never a staff email address."""
    if staff_id is None:
        return None
    staff = db.get(User, staff_id)
    if staff is None:
        return None
    if staff.display_name:
        return staff.display_name
    return staff.email.split("@")[0].replace(".", " ").title()


def _paged_days(db, settings_row, staff_id, duration, *, start_day: str | None, day_count: int):
    first = date.fromisoformat(start_day) if start_day else None
    pages, next_start = scheduling.offered_days(
        db,
        settings_row,
        staff_id,
        duration,
        start_day=first,
        day_count=day_count,
    )
    days = [
        AvailabilityOut(
            date=day.isoformat(),
            timezone=settings_row.business_timezone,
            duration_minutes=duration,
            slots=slots,
        )
        for day, slots in pages
    ]
    return days, (next_start.isoformat() if next_start else None)


@router.post("/booking/info", response_model=PublicBookingInfoOut)
def booking_info(
    body: InternalBookingInfoRequest, db: DbDep, settings: SettingsDep
) -> PublicBookingInfoOut:
    try:
        link = booking.resolve_token(db, body.token, settings)
    except LeadError as error:
        raise _http(error) from error

    settings_row = messaging.get_settings_row(db)
    if not settings_row.self_booking_enabled:
        raise HTTPException(status_code=403, detail="Online booking is not available.")
    duration = link.duration_minutes or settings_row.appointment_duration_minutes
    days, next_start_day = _paged_days(
        db,
        settings_row,
        link.assigned_to,
        duration,
        start_day=body.start_day,
        day_count=body.days,
    )
    db.commit()
    return PublicBookingInfoOut(
        business_name=settings_row.business_name,
        intro=settings_row.form_intro,
        staff_display_name=_staff_display_name(db, link.assigned_to),
        duration_minutes=duration,
        timezone=settings_row.business_timezone,
        days=days,
        window_days=settings_row.max_booking_days_ahead,
        next_start_day=next_start_day,
    )


def _booking_replay(
    db, settings, link, booking_key: str, reference_digest: str
) -> PublicBookingResultOut | None:
    """Return the original result for a booking key we have seen before.

    The manage capability is deterministic (link + booking key under the
    server pepper), so a replay hands back the SAME working capability even
    when the first response was lost — provided the digest still matches the
    appointment, which proves the derivation inputs are the original ones.
    """
    existing = db.scalar(
        select(Appointment).where(Appointment.booking_key_digest == reference_digest)
    )
    if existing is None:
        return None
    manage_raw, manage_digest = booking.derive_manage_token(settings, link.id, booking_key)
    return PublicBookingResultOut(
        booking_reference=existing.booking_reference or "",
        start_at=existing.start_at,
        end_at=existing.end_at,
        timezone=existing.timezone,
        manage_token=manage_raw if manage_digest == existing.manage_token_digest else None,
        duplicate=True,
    )


@router.post("/booking/confirm", response_model=PublicBookingResultOut)
def confirm_booking(
    body: InternalBookingConfirmRequest, request: Request, db: DbDep, settings: SettingsDep
) -> PublicBookingResultOut:
    """Book a slot.

    Order of proof: resolve the capability, serialize the staff calendar,
    re-check the booking key INSIDE the serialized section, then prove the
    requested instant is still an exactly offered slot before writing. A
    unique-constraint race resolves to the first winner's result.
    """
    if body.website:
        # Honeypot: acknowledge without creating anything.
        raise HTTPException(status_code=422, detail="Unable to book that time.")
    try:
        link = booking.resolve_token(db, body.token, settings)
        settings_row = messaging.get_settings_row(db)
        if not settings_row.self_booking_enabled:
            raise SchedulingError("Online booking is not available.", status_code=403)

        lead = db.get(Lead, link.lead_id)
        if lead is None:
            raise SchedulingError("This booking link is not valid.", status_code=404)

        reference_digest = digest_token(
            f"booking:{link.id}:{body.booking_key}", settings.session_token_pepper
        )

        # Serialize the staff calendar FIRST, then re-check the key: a
        # concurrent same-key request that won the race is now visible.
        scheduling.lock_staff_calendar(db, link.assigned_to)
        replay = _booking_replay(db, settings, link, body.booking_key, reference_digest)
        if replay is not None:
            db.commit()
            return replay

        duration = link.duration_minutes or settings_row.appointment_duration_minutes
        # The one central proof that this instant is still an offered slot.
        scheduling.assert_bookable_slot(db, settings_row, link.assigned_to, body.start_at, duration)
        manage_raw, manage_digest = booking.derive_manage_token(settings, link.id, body.booking_key)
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
        appointment.booking_key_digest = reference_digest
        db.flush()
        link.last_used_at = utcnow()
        notifications.schedule_for_appointment(db, appointment, settings_row, settings)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error

    try:
        db.commit()  # durable before any confirmation is sent
    except IntegrityError:
        # Lost a unique-constraint race (same booking key, same manage
        # capability, or the exclusion backstop). Re-read and answer with the
        # winner's result rather than an error.
        db.rollback()
        replay = _booking_replay(db, settings, link, body.booking_key, reference_digest)
        if replay is not None:
            return replay
        raise SlotUnavailableError() from None

    _dispatch_quietly(db, settings, request)
    return PublicBookingResultOut(
        booking_reference=appointment.booking_reference or "",
        start_at=appointment.start_at,
        end_at=appointment.end_at,
        timezone=appointment.timezone,
        manage_token=manage_raw,
    )


def _dispatch_quietly(db, settings, request: Request) -> None:
    """Send whatever became due (the confirmation). Never undoes the booking."""
    try:
        notifications.dispatch_due(db, settings, get_sms_sender(request), limit=5)
    except Exception:
        db.rollback()


# --- Customer-side appointment management --------------------------------


def _resolve_manage(db, token: str, settings):
    try:
        return booking.resolve_manage_token(db, token, settings)
    except LeadError as error:
        raise _http(error) from error


def _serialize_public(
    db, appointment: Appointment, settings_row, days, next_start_day
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
        revision=appointment.revision,
        days=days,
        next_start_day=next_start_day,
    )


@router.post("/appointments/info", response_model=PublicAppointmentOut)
def appointment_info(
    body: InternalManageRequest, db: DbDep, settings: SettingsDep
) -> PublicAppointmentOut:
    appointment = _resolve_manage(db, body.token, settings)
    settings_row = messaging.get_settings_row(db)
    duration = int((appointment.end_at - appointment.start_at).total_seconds() // 60)
    days: list[AvailabilityOut] = []
    next_start_day = None
    if appointment.status == "scheduled" and settings_row.self_booking_enabled:
        days, next_start_day = _paged_days(
            db,
            settings_row,
            appointment.assigned_to,
            duration,
            start_day=body.start_day,
            day_count=body.days,
        )
    db.commit()
    return _serialize_public(db, appointment, settings_row, days, next_start_day)


@router.post("/appointments/cancel", response_model=PublicAppointmentOut)
def cancel_appointment(
    body: InternalManageRequest, request: Request, db: DbDep, settings: SettingsDep
) -> PublicAppointmentOut:
    appointment = _resolve_manage(db, body.token, settings)
    try:
        settings_row = messaging.get_settings_row(db)
        # Lock order: appointment row first; no staff lock needed to cancel.
        appointment = scheduling.lock_appointment(db, appointment.id)
        appointment, changed = scheduling.set_disposition(
            db, None, appointment, "canceled", "Canceled by the customer online."
        )
        if changed:
            notifications.suppress_pending(db, appointment.id, ("reminder", "confirmation"))
            if settings_row.appointment_confirmation_enabled:
                notifications.queue_immediate(
                    db, appointment, settings, type_="canceled", occurrence="1"
                )
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()  # the cancellation is durable before any message is sent

    _dispatch_quietly(db, settings, request)
    return _serialize_public(db, appointment, messaging.get_settings_row(db), [], None)


@router.post("/appointments/reschedule", response_model=PublicAppointmentOut)
def reschedule_appointment(
    body: InternalManageRescheduleRequest, request: Request, db: DbDep, settings: SettingsDep
) -> PublicAppointmentOut:
    if body.website:
        raise HTTPException(status_code=422, detail="Unable to move that appointment.")
    appointment = _resolve_manage(db, body.token, settings)
    try:
        settings_row = messaging.get_settings_row(db)
        if not settings_row.self_booking_enabled:
            raise SchedulingError("Online booking is not available.", status_code=403)
        # Documented lock order: (1) appointment row, (2) staff calendar.
        appointment = scheduling.lock_appointment(db, appointment.id)
        scheduling.lock_staff_calendar(db, appointment.assigned_to)
        duration = int((appointment.end_at - appointment.start_at).total_seconds() // 60)
        if appointment.status == "scheduled" and not (
            appointment.revision != body.expected_revision and appointment.start_at == body.start_at
        ):
            # Prove the new instant is still an offered slot before moving.
            scheduling.assert_bookable_slot(
                db,
                settings_row,
                appointment.assigned_to,
                body.start_at,
                duration,
                exclude_id=appointment.id,
            )
        appointment, changed = scheduling.reschedule_appointment(
            db,
            None,
            appointment,
            settings_row,
            start_at=body.start_at,
            expected_revision=body.expected_revision,
        )
        if changed:
            # Obsolete reminders never go out; a fresh set covers the new time.
            notifications.suppress_pending(db, appointment.id, ("reminder",))
            notifications.queue_immediate(
                db,
                appointment,
                settings,
                type_="rescheduled",
                occurrence=f"r{appointment.revision}",
            )
            notifications.schedule_for_appointment(
                db, appointment, settings_row, settings, include_confirmation=False
            )
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()

    _dispatch_quietly(db, settings, request)
    return _serialize_public(db, appointment, messaging.get_settings_row(db), [], None)


# Re-exported for main.py wiring compatibility.
manage_router = router
