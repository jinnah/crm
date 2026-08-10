"""Appointment SMS: scheduling, atomic claiming and conservative outcomes.

Notifications are durable rows created inside the appointment transaction.
A scheduled n8n workflow calls the CRM to dispatch what is due; the CRM claims
each row atomically so overlapping scheduler runs cannot send twice, and an
ambiguous provider outcome is recorded as `unknown` and never auto-resent.
"""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Appointment,
    AppointmentNotification,
    CommunicationSettings,
    Lead,
    utcnow,
)
from app.security.tokens import digest_token
from app.services import messaging
from app.services.scheduling import to_local

logger = logging.getLogger(__name__)

# A claimed row that never reached an outcome (process crash) becomes visible
# and ambiguous after this long, rather than blocking or silently resending.
CLAIM_RECOVERY_MINUTES = 15

TEMPLATE_FIELDS = {
    "confirmation": "confirmation_template",
    "reminder": "reminder_template",
    "canceled": "appointment_canceled_template",
    "rescheduled": "appointment_rescheduled_template",
}


def render_appointment_template(
    template: str,
    appointment: Appointment,
    lead: Lead,
    settings_row: CommunicationSettings,
    staff_name: str | None,
) -> str:
    local = to_local(appointment.start_at, appointment.timezone)
    values = {
        "lead_name": lead.name or "there",
        "business_name": settings_row.business_name,
        "appointment_date": local.strftime("%A %d %B %Y"),
        "appointment_time": local.strftime("%H:%M %Z"),
        "assigned_staff": staff_name or "our team",
        "appointment_subject": appointment.subject,
        "booking_reference": appointment.booking_reference or "",
    }
    return messaging.render_with(template, values)


def _create(
    db: Session,
    appointment: Appointment,
    settings: Settings,
    *,
    type_: str,
    occurrence: str,
    scheduled_at,
) -> AppointmentNotification | None:
    """Insert one notification occurrence, ignoring an existing duplicate."""
    key = f"appointment:{appointment.id}:{type_}:{occurrence}"
    digest = digest_token(key, settings.session_token_pepper)
    savepoint = db.begin_nested()
    try:
        notification = AppointmentNotification(
            appointment_id=appointment.id,
            type=type_,
            occurrence=occurrence,
            schedule_revision=appointment.revision,
            scheduled_at=scheduled_at,
            state="pending",
            idempotency_key_digest=digest,
        )
        db.add(notification)
        savepoint.commit()
        return notification
    except IntegrityError:
        # This occurrence already exists — the unique constraint is the
        # deduplication mechanism, so a retry adds nothing.
        savepoint.rollback()
        return None


def schedule_for_appointment(
    db: Session,
    appointment: Appointment,
    settings_row: CommunicationSettings,
    settings: Settings,
    *,
    include_confirmation: bool = True,
) -> list[AppointmentNotification]:
    """Queue the confirmation and reminders for a scheduled appointment.

    Occurrences are keyed by the durable schedule revision, so a reschedule
    produces a fresh reminder set while an identical replay collides with the
    unique constraint and adds nothing.
    """
    created: list[AppointmentNotification] = []
    if include_confirmation and settings_row.appointment_confirmation_enabled:
        row = _create(
            db,
            appointment,
            settings,
            type_="confirmation",
            occurrence="1",
            scheduled_at=utcnow(),
        )
        if row is not None:
            created.append(row)

    if settings_row.appointment_reminder_enabled:
        offsets = [settings_row.reminder_offset_minutes]
        if settings_row.second_reminder_offset_minutes:
            offsets.append(settings_row.second_reminder_offset_minutes)
        for index, offset in enumerate(offsets, start=1):
            due = appointment.start_at - timedelta(minutes=offset)
            if due <= utcnow():
                continue  # the moment has already passed; do not backfill
            row = _create(
                db,
                appointment,
                settings,
                type_="reminder",
                occurrence=f"r{appointment.revision}:{index}",
                scheduled_at=due,
            )
            if row is not None:
                created.append(row)
    return created


def suppress_pending(
    db: Session, appointment_id: uuid.UUID, types: tuple[str, ...] = ("reminder",)
) -> int:
    """Stop obsolete notifications whose provider submission has not begun.

    Covers `pending` rows and rows a scheduler has claimed but not yet
    started sending (state=claimed with no attempted_at). The guard is
    row-atomic against deliver(): delivery flips attempted_at only while the
    state is still `claimed`, so exactly one side wins and a message that
    genuinely started sending is never rewritten.
    """
    result = db.execute(
        update(AppointmentNotification)
        .where(
            AppointmentNotification.appointment_id == appointment_id,
            AppointmentNotification.type.in_(types),
            AppointmentNotification.state.in_(("pending", "claimed")),
            AppointmentNotification.attempted_at.is_(None),
        )
        .values(state="suppressed", updated_at=utcnow())
    )
    db.flush()
    return int(result.rowcount or 0)


def queue_immediate(
    db: Session,
    appointment: Appointment,
    settings: Settings,
    *,
    type_: str,
    occurrence: str,
) -> AppointmentNotification | None:
    return _create(
        db, appointment, settings, type_=type_, occurrence=occurrence, scheduled_at=utcnow()
    )


def recover_stale_claims(db: Session) -> int:
    """A crash after claiming leaves a row in limbo; make it visibly ambiguous
    rather than sending it again."""
    cutoff = utcnow() - timedelta(minutes=CLAIM_RECOVERY_MINUTES)
    result = db.execute(
        update(AppointmentNotification)
        .where(
            AppointmentNotification.state == "claimed",
            AppointmentNotification.claimed_at < cutoff,
        )
        .values(
            state="unknown",
            failure_code="abandoned",
            failure_message=(
                "The reminder was interrupted before the provider confirmed it. "
                "It may or may not have been delivered."
            ),
            completed_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db.flush()
    return int(result.rowcount or 0)


def claim_due(db: Session, limit: int = 25) -> list[AppointmentNotification]:
    """Atomically take ownership of the notifications that are due.

    SELECT ... FOR UPDATE SKIP LOCKED means two overlapping scheduler runs
    never claim the same row, so a reminder cannot be sent twice.
    """
    now = utcnow()
    candidates = (
        select(AppointmentNotification.id)
        .where(
            AppointmentNotification.state == "pending",
            AppointmentNotification.scheduled_at <= now,
        )
        .order_by(AppointmentNotification.scheduled_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    ids = list(db.scalars(candidates))
    if not ids:
        return []
    db.execute(
        update(AppointmentNotification)
        .where(AppointmentNotification.id.in_(ids))
        .values(state="claimed", claimed_at=now, updated_at=now)
    )
    db.commit()  # the claim is durable before any provider contact
    return list(
        db.scalars(select(AppointmentNotification).where(AppointmentNotification.id.in_(ids)))
    )


def _finish(
    db: Session,
    notification: AppointmentNotification,
    state: str,
    *,
    message_id: uuid.UUID | None = None,
    code: str | None = None,
    detail: str | None = None,
) -> None:
    now = utcnow()
    notification.state = state
    notification.attempted_at = notification.attempted_at or now
    notification.completed_at = now
    notification.outbound_message_id = message_id
    notification.failure_code = code
    notification.failure_message = detail
    db.flush()


def deliver(
    db: Session,
    notification: AppointmentNotification,
    settings: Settings,
    sender,
) -> str:
    """Send one claimed notification and record a conservative outcome."""
    appointment = db.get(Appointment, notification.appointment_id)
    if appointment is None:  # pragma: no cover - defensive
        _finish(db, notification, "failed", code="missing", detail="Appointment not found.")
        return "failed"

    # A cancellation or reschedule may have landed after this row was claimed.
    if notification.type == "reminder" and appointment.status != "scheduled":
        _finish(db, notification, "suppressed", detail="Appointment is no longer scheduled.")
        db.commit()
        return "suppressed"
    if notification.type == "reminder" and appointment.revision != notification.schedule_revision:
        # The appointment moved after this reminder was written: its content
        # describes an old time and must never reach the customer.
        _finish(
            db,
            notification,
            "suppressed",
            detail="The appointment was rescheduled after this reminder was queued.",
        )
        db.commit()
        return "suppressed"

    lead = db.get(Lead, appointment.lead_id)
    settings_row = messaging.get_settings_row(db)
    if lead is None or not lead.phone or not lead.phone.startswith("+"):
        _finish(
            db,
            notification,
            "failed",
            code="no_phone",
            detail="The lead has no phone number in international format.",
        )
        return "failed"

    staff_name = None
    if appointment.assigned_to is not None:
        from app.models import User

        staff = db.get(User, appointment.assigned_to)
        staff_name = staff.email.split("@")[0] if staff is not None else None

    template = getattr(settings_row, TEMPLATE_FIELDS[notification.type])
    body = render_appointment_template(template, appointment, lead, settings_row, staff_name)

    # Marking the attempt is a guarded, row-atomic update: it succeeds only
    # while the row is still `claimed`. If a concurrent cancel or reschedule
    # suppressed the row after we loaded it, rowcount is 0 and nothing is
    # sent. Once this commits, provider submission has genuinely begun and an
    # ambiguous outcome stays `unknown` forever — never auto-retried.
    started = db.execute(
        update(AppointmentNotification)
        .where(
            AppointmentNotification.id == notification.id,
            AppointmentNotification.state == "claimed",
            AppointmentNotification.attempted_at.is_(None),
        )
        .values(attempted_at=utcnow(), updated_at=utcnow())
    )
    db.commit()  # never hold a transaction open across the provider call
    if int(started.rowcount or 0) == 0:
        db.refresh(notification)
        return notification.state if notification.state == "suppressed" else "suppressed"
    db.refresh(notification)

    message = messaging.create_and_send(
        db,
        lead,
        purpose="appointment",
        to_phone=lead.phone,
        body=body,
        idempotency_key=f"appointment:{appointment.id}:{notification.type}:{notification.occurrence}",
        settings=settings,
        sender=sender,
    )
    outcome = {
        "submitted": "sent",
        "delivered": "sent",
        "failed": "failed",
        "unknown": "unknown",
        "pending": "unknown",
    }.get(message.status, "unknown")
    _finish(
        db,
        notification,
        outcome,
        message_id=message.id,
        code=message.error_code,
        detail=message.error_message,
    )
    db.commit()
    return outcome


def dispatch_due(db: Session, settings: Settings, sender, limit: int = 25) -> dict[str, int]:
    """Entry point for the scheduled n8n workflow."""
    recovered = recover_stale_claims(db)
    db.commit()
    counts = {"claimed": 0, "sent": 0, "failed": 0, "unknown": 0, "suppressed": 0}
    counts["recovered"] = recovered
    for notification in claim_due(db, limit=limit):
        counts["claimed"] += 1
        try:
            outcome = deliver(db, notification, settings, sender)
        except Exception as error:  # never lose the appointment over a send
            db.rollback()
            logger.warning("Appointment notification failed: %s", type(error).__name__)
            outcome = "unknown"
            _finish(
                db,
                notification,
                "unknown",
                code="exception",
                detail="The send was interrupted before the provider confirmed it.",
            )
            db.commit()
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts
