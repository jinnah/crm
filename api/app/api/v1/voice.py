"""Server-to-server endpoints for the AI voice-call workflow.

Fixed paths only; the CallSid and every requested time travel in JSON bodies,
never in URLs. Authentication uses the dedicated voice-ingestion key when
configured, otherwise the general inbound key, and fails closed with neither.
Nothing here logs caller details, transcripts or request bodies.
"""

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request

from app.api.v1.deps import DbDep, SettingsDep, get_sms_sender
from app.api.v1.schemas import (
    AvailabilityOut,
    VoiceAvailabilityOut,
    VoiceAvailabilityRequest,
    VoiceBookOut,
    VoiceBookRequest,
    VoiceCallCompletedOut,
    VoiceCallCompletedRequest,
    VoiceCleanupOut,
)
from app.security.tokens import constant_time_equals
from app.services import appointment_notifications as notifications
from app.services import messaging, scheduling, voice
from app.services.leads import LeadError
from app.services.scheduling import SlotUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inbound", tags=["voice"])


def _require_voice_key(request: Request, settings) -> None:
    """The dedicated voice key wins when configured; otherwise the inbound
    key. No key configured means every request is rejected (fail closed)."""
    provided = request.headers.get("X-API-Key") or ""
    configured = settings.voice_api_key or settings.inbound_api_key
    if not configured or not provided or not constant_time_equals(provided, configured):
        raise HTTPException(status_code=401, detail="Invalid API key.")


def _http(error: LeadError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


@router.post("/voice-calls/completed", response_model=VoiceCallCompletedOut)
def voice_call_completed(
    body: VoiceCallCompletedRequest, request: Request, db: DbDep, settings: SettingsDep
) -> VoiceCallCompletedOut:
    _require_voice_key(request, settings)
    try:
        result = voice.process_voice_completion(db, body, settings)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error

    # The lead, call record, activity and clocks are committed. Messaging
    # happens after and never rolls the stored call back.
    if not result.replayed:
        try:
            voice.send_voice_messages(db, result.call, settings, get_sms_sender(request))
        except Exception as error:  # pragma: no cover - defensive
            db.rollback()
            logger.warning("Voice messaging failed: %s", type(error).__name__)

    return VoiceCallCompletedOut(
        call_id=result.call.id,
        lead_id=result.lead.id,
        lead_created=result.lead_created,
        needs_review=result.lead.needs_review,
        replayed=result.replayed,
        ack_state=result.call.ack_state,
        alert_state=result.call.alert_state,
    )


@router.post("/voice/availability", response_model=VoiceAvailabilityOut)
def voice_availability(
    body: VoiceAvailabilityRequest, request: Request, db: DbDep, settings: SettingsDep
) -> VoiceAvailabilityOut:
    _require_voice_key(request, settings)
    try:
        call = voice.resolve_call(db, body.call_sid)
    except LeadError as error:
        raise _http(error) from error

    settings_row = messaging.get_settings_row(db)
    lead = call.lead
    staff = voice.voice_booking_staff(db, lead, settings_row)
    if staff is None:
        return VoiceAvailabilityOut(
            result="requires_human_follow_up",
            reason="No active staff member is configured for voice bookings.",
        )

    duration = settings_row.appointment_duration_minutes
    start_day = date.fromisoformat(body.start_day) if body.start_day else None
    pages, next_start = scheduling.offered_days(
        db, settings_row, staff.id, duration, start_day=start_day, day_count=body.days
    )
    db.commit()
    display = staff.display_name or staff.email.split("@")[0].replace(".", " ").title()
    return VoiceAvailabilityOut(
        result="ok",
        timezone=settings_row.business_timezone,
        duration_minutes=duration,
        staff_display_name=display,
        days=[
            AvailabilityOut(
                date=day.isoformat(),
                timezone=settings_row.business_timezone,
                duration_minutes=duration,
                slots=slots,
            )
            for day, slots in pages
        ],
        next_start_day=next_start.isoformat() if next_start else None,
    )


@router.post("/voice/book", response_model=VoiceBookOut)
def voice_book(
    body: VoiceBookRequest, request: Request, db: DbDep, settings: SettingsDep
) -> VoiceBookOut:
    _require_voice_key(request, settings)
    try:
        call = voice.resolve_call(db, body.call_sid)
    except LeadError as error:
        raise _http(error) from error

    try:
        appointment, replayed = voice.voice_book(db, call, body.start_at, settings)
    except SlotUnavailableError as error:
        db.rollback()
        return VoiceBookOut(result="slot_unavailable", reason=error.message)
    except voice.VoiceError as error:
        db.rollback()
        if error.status_code == 409:
            return VoiceBookOut(result="requires_human_follow_up", reason=error.message)
        raise _http(error) from error
    except LeadError as error:
        db.rollback()
        raise _http(error) from error

    if not replayed:
        # Confirmation goes out after the booking is durable, never inside it.
        try:
            notifications.dispatch_due(db, settings, get_sms_sender(request), limit=5)
        except Exception:  # pragma: no cover - defensive
            db.rollback()

    return VoiceBookOut(
        result="booked",
        booking_reference=appointment.booking_reference,
        start_at=appointment.start_at,
        end_at=appointment.end_at,
        timezone=appointment.timezone,
        replayed=replayed,
    )


@router.post("/voice-calls/cleanup", response_model=VoiceCleanupOut)
def voice_cleanup(request: Request, db: DbDep, settings: SettingsDep) -> VoiceCleanupOut:
    """Daily retention sweep, driven by n8n: purges expired or consent-less
    transcript text and recording references, preserving summaries and audit
    fields."""
    _require_voice_key(request, settings)
    transcripts, recordings = voice.purge_expired(db)
    return VoiceCleanupOut(purged_transcripts=transcripts, purged_recordings=recordings)
