import uuid
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import (
    DbDep,
    FullyAuthedUserDep,
    SettingsDep,
    check_csrf,
    check_origin,
    get_sms_sender,
)
from app.api.v1.schemas import (
    AppointmentDispositionRequest,
    AppointmentNotificationOut,
    AppointmentOut,
    AvailabilityOut,
    BookingLinkOut,
    CreateAppointmentRequest,
    CreateBookingLinkRequest,
    RescheduleAppointmentRequest,
)
from app.models import Appointment, AppointmentNotification, BookingLink, Lead, User
from app.services import appointment_notifications as notifications
from app.services import booking, calendar_file, messaging, scheduling
from app.services.leads import LeadError, can_manage_leads, get_visible_lead

router = APIRouter(
    prefix="/appointments",
    tags=["appointments"],
    dependencies=[Depends(check_origin), Depends(check_csrf)],
)

# Lead-scoped appointment routes live under /leads to match the existing shape.
lead_router = APIRouter(
    prefix="/leads",
    tags=["appointments"],
    dependencies=[Depends(check_origin), Depends(check_csrf)],
)


def _http(error: LeadError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


def serialize(db: Session, appointment: Appointment) -> AppointmentOut:
    assignee_email = None
    if appointment.assigned_to is not None:
        staff = db.get(User, appointment.assigned_to)
        assignee_email = staff.email if staff is not None else None
    lead = db.get(Lead, appointment.lead_id)
    return AppointmentOut(
        id=appointment.id,
        lead_id=appointment.lead_id,
        lead_name=(lead.name or lead.email or lead.phone) if lead is not None else None,
        assigned_to=appointment.assigned_to,
        assignee_email=assignee_email,
        subject=appointment.subject,
        notes=appointment.notes,
        start_at=appointment.start_at,
        end_at=appointment.end_at,
        timezone=appointment.timezone,
        status=appointment.status,
        origin=appointment.origin,
        booking_reference=appointment.booking_reference,
        cancellation_reason=appointment.cancellation_reason,
        created_at=appointment.created_at,
        updated_at=appointment.updated_at,
    )


def _serialize_link(link: BookingLink, url: str | None = None) -> BookingLinkOut:
    return BookingLinkOut(
        id=link.id,
        lead_id=link.lead_id,
        assigned_to=link.assigned_to,
        expires_at=link.expires_at,
        revoked_at=link.revoked_at,
        duration_minutes=link.duration_minutes,
        created_at=link.created_at,
        last_used_at=link.last_used_at,
        url=url,
    )


@router.get("", response_model=list[AppointmentOut])
def list_appointments(
    user: FullyAuthedUserDep,
    db: DbDep,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    staff_id: uuid.UUID | None = None,
    status: str | None = None,
) -> list[AppointmentOut]:
    """Calendar feed, scoped to what the user may see."""
    query = scheduling.visible_appointments_query(user)
    if start is not None:
        query = query.where(Appointment.end_at > start)
    if end is not None:
        query = query.where(Appointment.start_at < end)
    if status:
        query = query.where(Appointment.status == status)
    if staff_id is not None:
        if not can_manage_leads(user) and staff_id != user.id:
            raise HTTPException(status_code=403, detail="You cannot view another user's calendar.")
        query = query.where(Appointment.assigned_to == staff_id)
    rows = db.scalars(query.order_by(Appointment.start_at).limit(500)).unique()
    return [serialize(db, row) for row in rows]


@router.get("/availability", response_model=AvailabilityOut)
def staff_availability(
    user: FullyAuthedUserDep,
    db: DbDep,
    day: Annotated[str, Query(description="YYYY-MM-DD in the business time zone")],
    staff_id: uuid.UUID | None = None,
    duration_minutes: Annotated[int | None, Query(ge=1, le=720)] = None,
    exclude_appointment_id: uuid.UUID | None = None,
) -> AvailabilityOut:
    settings_row = messaging.get_settings_row(db)
    db.commit()
    try:
        target = date.fromisoformat(day)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Use a YYYY-MM-DD date.") from error
    if staff_id is not None and not can_manage_leads(user) and staff_id != user.id:
        raise HTTPException(status_code=403, detail="You cannot view another user's calendar.")
    exclude_id = None
    if exclude_appointment_id is not None:
        # Rescheduling should be able to reuse the time it already occupies —
        # but only for an appointment this user is allowed to see.
        try:
            exclude_id = scheduling.get_visible_appointment(db, user, exclude_appointment_id).id
        except LeadError as error:
            raise _http(error) from error
    slots = scheduling.available_slots(
        db, settings_row, staff_id, target, duration_minutes=duration_minutes, exclude_id=exclude_id
    )
    return AvailabilityOut(
        date=day,
        timezone=settings_row.business_timezone,
        duration_minutes=duration_minutes or settings_row.appointment_duration_minutes,
        slots=slots,
    )


@lead_router.get("/{lead_id}/appointments", response_model=list[AppointmentOut])
def lead_appointments(
    lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep
) -> list[AppointmentOut]:
    """Full history for one lead, newest first. Readable even when archived."""
    try:
        lead = get_visible_lead(db, user, lead_id)
    except LeadError as error:
        raise _http(error) from error
    rows = db.scalars(
        select(Appointment)
        .where(Appointment.lead_id == lead.id)
        .order_by(Appointment.start_at.desc())
    )
    return [serialize(db, row) for row in rows]


@lead_router.post("/{lead_id}/appointments", response_model=AppointmentOut, status_code=201)
def create_appointment(
    lead_id: uuid.UUID,
    body: CreateAppointmentRequest,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
    settings: SettingsDep,
) -> AppointmentOut:
    try:
        lead = get_visible_lead(db, user, lead_id)
        settings_row = messaging.get_settings_row(db)
        staff_id = (
            body.assigned_to
            if body.assigned_to is not None
            else (None if can_manage_leads(user) else user.id)
        )
        # Serialize this staff member's calendar for the conflict check.
        scheduling.lock_staff_calendar(db, staff_id)
        appointment = scheduling.create_appointment(
            db,
            user,
            lead,
            settings_row,
            start_at=body.start_at,
            duration_minutes=body.duration_minutes,
            subject=body.subject,
            notes=body.notes,
            staff_id=staff_id,
            origin="staff",
            booking_reference=booking.new_booking_reference(),
        )
        notifications.schedule_for_appointment(db, appointment, settings_row, settings)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()  # the appointment is durable before any message is sent

    _dispatch_immediate(db, settings, request)
    return serialize(db, appointment)


def _dispatch_immediate(db: Session, settings, request: Request) -> None:
    """Send anything already due (the confirmation) after the commit."""
    try:
        notifications.dispatch_due(db, settings, get_sms_sender(request), limit=5)
    except Exception:  # never let messaging undo a stored appointment
        db.rollback()


@router.get("/{appointment_id}", response_model=AppointmentOut)
def get_appointment(
    appointment_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep
) -> AppointmentOut:
    try:
        appointment = scheduling.get_visible_appointment(db, user, appointment_id)
    except LeadError as error:
        raise _http(error) from error
    return serialize(db, appointment)


@router.post("/{appointment_id}/reschedule", response_model=AppointmentOut)
def reschedule(
    appointment_id: uuid.UUID,
    body: RescheduleAppointmentRequest,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
    settings: SettingsDep,
) -> AppointmentOut:
    try:
        appointment = scheduling.get_visible_appointment(db, user, appointment_id)
        settings_row = messaging.get_settings_row(db)
        scheduling.lock_staff_calendar(db, appointment.assigned_to)
        generation = str(int(appointment.updated_at.timestamp()))
        scheduling.reschedule_appointment(
            db,
            user,
            appointment,
            settings_row,
            start_at=body.start_at,
            duration_minutes=body.duration_minutes,
        )
        # Obsolete reminders never go out; a fresh set is queued for the new time.
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

    _dispatch_immediate(db, settings, request)
    return serialize(db, appointment)


@router.post("/{appointment_id}/disposition", response_model=AppointmentOut)
def set_disposition(
    appointment_id: uuid.UUID,
    body: AppointmentDispositionRequest,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
    settings: SettingsDep,
) -> AppointmentOut:
    """Cancel, complete or mark no-show."""
    try:
        appointment = scheduling.get_visible_appointment(db, user, appointment_id)
        settings_row = messaging.get_settings_row(db)
        scheduling.set_disposition(db, user, appointment, body.status, body.reason)
        if body.status == "canceled":
            notifications.suppress_pending(db, appointment.id, ("reminder", "confirmation"))
            if settings_row.appointment_confirmation_enabled:
                notifications.queue_immediate(
                    db, appointment, settings, type_="canceled", occurrence="1"
                )
        else:
            notifications.suppress_pending(db, appointment.id, ("reminder",))
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()

    _dispatch_immediate(db, settings, request)
    return serialize(db, appointment)


@router.get("/{appointment_id}/calendar.ics")
def download_ics(appointment_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> Response:
    try:
        appointment = scheduling.get_visible_appointment(db, user, appointment_id)
    except LeadError as error:
        raise _http(error) from error
    settings_row = messaging.get_settings_row(db)
    db.commit()
    content = calendar_file.build_ics(appointment, settings_row)
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="appointment-{appointment.id}.ics"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{appointment_id}/notifications", response_model=list[AppointmentNotificationOut])
def appointment_notifications_list(
    appointment_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep
) -> list[AppointmentNotificationOut]:
    try:
        appointment = scheduling.get_visible_appointment(db, user, appointment_id)
    except LeadError as error:
        raise _http(error) from error
    rows = db.scalars(
        select(AppointmentNotification)
        .where(AppointmentNotification.appointment_id == appointment.id)
        .order_by(AppointmentNotification.scheduled_at)
    )
    return [AppointmentNotificationOut.model_validate(row) for row in rows]


# --- Booking links -------------------------------------------------------


@lead_router.get("/{lead_id}/booking-link", response_model=BookingLinkOut | None)
def get_booking_link(
    lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep
) -> BookingLinkOut | None:
    try:
        lead = get_visible_lead(db, user, lead_id)
    except LeadError as error:
        raise _http(error) from error
    link = booking.active_link_for(db, lead.id)
    return _serialize_link(link) if link is not None else None


@lead_router.post("/{lead_id}/booking-link", response_model=BookingLinkOut, status_code=201)
def create_booking_link(
    lead_id: uuid.UUID,
    body: CreateBookingLinkRequest,
    user: FullyAuthedUserDep,
    db: DbDep,
    settings: SettingsDep,
) -> BookingLinkOut:
    """Create (or regenerate) the lead's booking link. The raw token is
    returned exactly once, in the URL."""
    try:
        lead = get_visible_lead(db, user, lead_id)
        link, raw = booking.create_link(
            db,
            user,
            lead,
            settings,
            staff_id=body.assigned_to,
            duration_minutes=body.duration_minutes,
            ttl_days=body.ttl_days,
        )
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    url = f"{settings.frontend_url.rstrip('/')}/book/{raw}"
    return _serialize_link(link, url=url)


@lead_router.post("/{lead_id}/booking-link/revoke", response_model=BookingLinkOut | None)
def revoke_booking_link(
    lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep
) -> BookingLinkOut | None:
    try:
        lead = get_visible_lead(db, user, lead_id)
        link = booking.active_link_for(db, lead.id)
        if link is None:
            return None
        booking.revoke_link(db, user, lead, link)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    return _serialize_link(link)


# --- Attention-queue helpers --------------------------------------------


def appointment_attention(db: Session, user: User, settings_row) -> dict[str, list]:
    """Scheduling states that need someone to act."""
    from app.models import utcnow

    now = utcnow()
    base = scheduling.visible_appointments_query(user)
    overdue = list(
        db.scalars(
            base.where(Appointment.status == "scheduled", Appointment.end_at < now).order_by(
                Appointment.start_at
            )
        ).unique()
    )
    horizon = now + timedelta(hours=settings_row.upcoming_window_hours)
    upcoming = list(
        db.scalars(
            base.where(
                Appointment.status == "scheduled",
                Appointment.start_at >= now,
                Appointment.start_at <= horizon,
            ).order_by(Appointment.start_at)
        ).unique()
    )

    visible_ids = [row.id for row in db.scalars(base).unique()]
    failed_notifications: list[AppointmentNotification] = []
    unknown_notifications: list[AppointmentNotification] = []
    if visible_ids:
        rows = db.scalars(
            select(AppointmentNotification).where(
                AppointmentNotification.appointment_id.in_(visible_ids),
                AppointmentNotification.state.in_(("failed", "unknown")),
            )
        )
        for row in rows:
            (failed_notifications if row.state == "failed" else unknown_notifications).append(row)
    return {
        "overdue": overdue,
        "upcoming": upcoming,
        "failed_notifications": failed_notifications,
        "unknown_notifications": unknown_notifications,
    }
