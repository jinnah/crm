"""Inbound AI voice-call channel: durable call records, conservative lead
matching, automated messages, appointment tools and transcript retention.

The AI agent and n8n never touch PostgreSQL: everything arrives through the
authenticated endpoints that call into this module, `CallSid` is the
provider-level idempotency identity (an identifier, never authority), and no
raw provider payload is ever stored. Nothing here logs caller details,
transcripts or request bodies.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    VOICE_CALL_STATUSES,
    VOICE_CONSENT_RESULTS,
    VOICE_TRANSFER_OUTCOMES,
    VOICE_URGENCIES,
    Appointment,
    CommunicationSettings,
    Lead,
    User,
    VoiceCall,
    utcnow,
)
from app.security.tokens import digest_token
from app.services import appointment_notifications as notifications
from app.services import booking as booking_service
from app.services import messaging, scheduling
from app.services.inbound import _fill_missing_identity, _match_lead
from app.services.leads import LeadError, add_activity, clean_optional_email, normalize_phone

logger = logging.getLogger(__name__)


class VoiceError(LeadError):
    """Rejected voice-channel action; the message is safe for the caller."""


@dataclass
class VoiceCompletionResult:
    call: VoiceCall
    lead: Lead
    lead_created: bool
    replayed: bool


def _validate_enums(payload) -> None:
    checks = (
        (payload.call_status, VOICE_CALL_STATUSES, "call_status"),
        (payload.urgency, VOICE_URGENCIES, "urgency"),
        (payload.transfer_outcome, VOICE_TRANSFER_OUTCOMES, "transfer_outcome"),
        (payload.consent_result, VOICE_CONSENT_RESULTS, "consent_result"),
    )
    for value, allowed, field in checks:
        if value not in allowed:
            raise VoiceError(f"{field} must be one of: " + ", ".join(allowed), status_code=422)


def _metadata_bounded(metadata: dict[str, str] | None) -> dict[str, str] | None:
    if not metadata:
        return None
    if len(metadata) > 20:
        raise VoiceError("metadata may hold at most 20 keys", status_code=422)
    bounded: dict[str, str] = {}
    for key, value in metadata.items():
        if len(str(key)) > 64 or len(str(value)) > 500:
            raise VoiceError("metadata keys and values are too long", status_code=422)
        bounded[str(key)] = str(value)
    return bounded


def _conflicting_identity(call: VoiceCall, caller_phone: str | None) -> bool:
    """The caller's number is the call's immutable identity: a retry that
    disagrees about it is describing a different call."""
    return (call.caller_phone or None) != (caller_phone or None)


def process_voice_completion(db: Session, payload, settings: Settings) -> VoiceCompletionResult:
    """Store one completed call: lead, call record, activity, response clock
    and review state, durably, before any provider is contacted.

    Replays (same CallSid, same identity) return the stored result. A retry
    with the same CallSid but a different caller number is refused with 409
    and flagged for attention rather than rewriting history.
    """
    _validate_enums(payload)
    metadata = _metadata_bounded(payload.metadata)
    caller_phone = normalize_phone(payload.caller_phone)
    caller_email = clean_optional_email(payload.caller_email)
    caller_name = payload.caller_name.strip()

    for attempt in range(2):
        existing = db.scalar(select(VoiceCall).where(VoiceCall.call_sid == payload.call_sid))
        if existing is not None:
            if _conflicting_identity(existing, caller_phone):
                existing.completion_conflict = True
                lead = db.get(Lead, existing.lead_id)
                if lead is not None:
                    lead.needs_review = True
                db.commit()
                raise VoiceError(
                    "This CallSid was already recorded with a different caller identity. "
                    "The original record is preserved and flagged for review.",
                    status_code=409,
                )
            lead = db.get(Lead, existing.lead_id)
            assert lead is not None
            return VoiceCompletionResult(
                call=existing, lead=lead, lead_created=False, replayed=True
            )

        lead, lead_created, ambiguous = _resolve_voice_lead(
            db, caller_phone, caller_email, caller_name
        )

        if lead.archived_at is not None:
            lead.archived_at = None
            lead.needs_review = True
            add_activity(db, lead, "restored", "Lead restored automatically by a new voice call.")

        # Collected values never silently overwrite populated CRM fields; a
        # disagreement is kept on the call record and flagged for review.
        conflicts = []
        if caller_name and lead.name and lead.name.strip().lower() != caller_name.lower():
            conflicts.append("name")
        if caller_email and lead.email and lead.email != caller_email:
            conflicts.append("email")
        _fill_missing_identity(
            lead, name=caller_name, email=caller_email, phone=caller_phone, company=""
        )
        if conflicts or ambiguous:
            lead.needs_review = True

        settings_row = messaging.get_settings_row(db)
        retain = (
            settings_row.voice_transcript_retention_enabled and payload.consent_result == "granted"
        )
        call = VoiceCall(
            provider=payload.provider,
            call_sid=payload.call_sid,
            lead_id=lead.id,
            caller_phone=caller_phone,
            business_phone=normalize_phone(payload.business_phone),
            started_at=payload.started_at,
            answered_at=payload.answered_at,
            ended_at=payload.ended_at,
            duration_seconds=payload.duration_seconds,
            call_status=payload.call_status,
            disposition=payload.disposition.strip(),
            caller_name=caller_name,
            service_requested=payload.service_requested.strip(),
            service_address=payload.service_address.strip(),
            preferred_callback_window=payload.preferred_callback_window.strip(),
            appointment_preference=payload.appointment_preference.strip(),
            summary=payload.summary.strip(),
            urgency=payload.urgency,
            requires_human_follow_up=payload.requires_human_follow_up,
            transfer_outcome=payload.transfer_outcome,
            disclosure_version=payload.disclosure_version.strip(),
            consent_result=payload.consent_result,
            # Without granted consent nothing sensitive is retained, even
            # when the global setting is on.
            recording_sid=payload.recording_sid if retain else None,
            transcript_text=payload.transcript_text if retain else None,
            retention_expires_at=(
                utcnow() + timedelta(days=settings_row.voice_transcript_retention_days)
                if retain and (payload.transcript_text or payload.recording_sid)
                else None
            ),
            meta={**(metadata or {}), **({"identity_conflicts": conflicts} if conflicts else {})}
            or None,
        )
        db.add(call)
        # The unique CallSid violation surfaces on the flush, not only at
        # commit — a losing concurrent duplicate must reach the replay path
        # either way.
        try:
            db.flush()

            activity = add_activity(
                db,
                lead,
                "voice_call",
                _activity_content(call),
                channel="voice_call",
                direction="inbound",
                provider=payload.provider,
                external_event_id=payload.call_sid,
                occurred_at=payload.started_at or utcnow(),
                meta={"voice_call_id": str(call.id)},
            )
            call.activity_id = activity.id

            messaging.start_response_clock(db, lead, settings_row)
            db.commit()
        except IntegrityError:
            # A concurrent duplicate CallSid won; replay its result.
            db.rollback()
            if attempt == 0:
                continue
            raise VoiceError(
                "Concurrent call completion could not be reconciled.", status_code=409
            ) from None
        return VoiceCompletionResult(
            call=call, lead=lead, lead_created=lead_created, replayed=False
        )
    raise VoiceError(  # pragma: no cover - loop always returns or raises
        "Concurrent call completion could not be reconciled.", status_code=409
    )


def _resolve_voice_lead(
    db: Session, phone: str | None, email: str | None, name: str
) -> tuple[Lead, bool, bool]:
    """Conservative matching, same policy as every other inbound channel."""
    lead, ambiguous = (None, False)
    if phone or email:
        lead, ambiguous = _match_lead(db, email, phone)
    if lead is not None:
        return lead, False, False
    new_lead = Lead(
        name=name,
        email=email,
        phone=phone,
        status="new",
        source="voice_call",
        needs_review=ambiguous or not (phone or email),
    )
    db.add(new_lead)
    db.flush()
    return new_lead, True, ambiguous


def _activity_content(call: VoiceCall) -> str:
    """Compact timeline text — the summary, never a transcript."""
    parts = [f"Voice call ({call.call_status.replace('_', ' ')})"]
    if call.service_requested:
        parts.append(f"Service: {call.service_requested}")
    if call.summary:
        parts.append(call.summary)
    return "\n".join(parts)


# --- Automated messages ---------------------------------------------------


def send_voice_messages(db: Session, call: VoiceCall, settings: Settings, sender) -> None:
    """Customer acknowledgment and staff alerts for one stored call.

    Called only after the completion transaction has committed. Deduplicated
    by call, purpose and recipient through the outbound idempotency key, so a
    replayed completion enqueues nothing new. Missing destinations become
    controlled states on the call record, never silent skips.
    """
    settings_row = messaging.get_settings_row(db)
    lead = db.get(Lead, call.lead_id)
    assert lead is not None

    # Customer acknowledgment.
    if not settings_row.voice_ack_enabled:
        call.ack_state = "skipped"
    elif not (call.caller_phone and call.caller_phone.startswith("+")):
        call.ack_state = "no_destination"
    else:
        message = messaging.create_and_send(
            db,
            lead,
            purpose="auto_acknowledgment",
            to_phone=call.caller_phone,
            body=_render_voice(settings_row.voice_ack_template, call, lead, settings_row, None),
            idempotency_key=f"voice:{call.call_sid}:ack",
            settings=settings,
            sender=sender,
        )
        call.ack_state = "sent" if message.status in ("submitted", "delivered") else message.status

    # Staff alerts, per the configured recipients.
    if not settings_row.voice_alert_enabled:
        call.alert_state = "skipped"
    else:
        recipients: list[tuple[str, str, User | None]] = []
        wants_business = settings_row.voice_alert_recipients in ("business", "both")
        wants_assigned = settings_row.voice_alert_recipients in ("assigned", "both")
        assignee = db.get(User, lead.assigned_to) if lead.assigned_to else None
        if wants_business and settings_row.alert_destination_phone:
            recipients.append(("business", settings_row.alert_destination_phone, None))
        if wants_assigned and assignee is not None and assignee.notification_phone:
            recipients.append(("assigned", assignee.notification_phone, assignee))
        assigned_unreachable = wants_assigned and (
            assignee is None or not assignee.notification_phone
        )
        if (
            assigned_unreachable
            and settings_row.voice_alert_recipients == "assigned"
            and settings_row.alert_destination_phone
            and not recipients
        ):
            # Controlled fallback: the business number stands in when the
            # assigned user has no notification phone.
            recipients.append(("business", settings_row.alert_destination_phone, None))

        if not recipients:
            call.alert_state = "no_destination"
        else:
            states = []
            for kind, phone, staff in recipients:
                message = messaging.create_and_send(
                    db,
                    lead,
                    purpose="staff_alert",
                    to_phone=phone,
                    body=_render_voice(
                        settings_row.voice_alert_template, call, lead, settings_row, staff
                    ),
                    idempotency_key=f"voice:{call.call_sid}:alert:{kind}",
                    settings=settings,
                    sender=sender,
                )
                states.append(
                    "sent" if message.status in ("submitted", "delivered") else message.status
                )
            # The worst outcome wins, so an unknown or failed alert surfaces.
            for candidate in ("failed", "unknown", "sent"):
                if candidate in states:
                    call.alert_state = candidate
                    break
    db.commit()


VOICE_TEMPLATE_VARIABLES = (
    "lead_name",
    "business_name",
    "service_requested",
    "call_summary",
    "callback_window",
    "assigned_staff",
    "lead_id",
)


def validate_voice_template(template: str) -> str:
    unknown = set(messaging._VARIABLE_PATTERN.findall(template)) - set(VOICE_TEMPLATE_VARIABLES)
    if unknown:
        raise VoiceError(
            "Unknown template variables: "
            + ", ".join(sorted(unknown))
            + ". Allowed: "
            + ", ".join(VOICE_TEMPLATE_VARIABLES)
        )
    if not template.strip():
        raise VoiceError("Template cannot be empty.")
    if len(template) > messaging.MAX_SMS_LENGTH:
        raise VoiceError(f"Template must be at most {messaging.MAX_SMS_LENGTH} characters.")
    return template


def _render_voice(
    template: str,
    call: VoiceCall,
    lead: Lead,
    settings_row: CommunicationSettings,
    staff: User | None,
) -> str:
    staff_name = ""
    if staff is not None:
        staff_name = staff.display_name or staff.email.split("@")[0]
    values = {
        "lead_name": lead.name or "there",
        "business_name": settings_row.business_name,
        "service_requested": call.service_requested or "a service request",
        "call_summary": call.summary[:300],
        "callback_window": call.preferred_callback_window or "any time",
        "assigned_staff": staff_name or "our team",
        "lead_id": str(lead.id),
    }
    return messaging.render_with(template, values)


# --- Appointment tools for the voice workflow -----------------------------


def voice_booking_staff(
    db: Session, lead: Lead, settings_row: CommunicationSettings
) -> User | None:
    """The staff member voice bookings go to: the lead's active assignee,
    else the owner-configured default. None means a human must follow up."""
    if lead.assigned_to is not None:
        assignee = db.get(User, lead.assigned_to)
        if assignee is not None and assignee.is_active:
            return assignee
    if settings_row.voice_default_staff_id is not None:
        fallback = db.get(User, settings_row.voice_default_staff_id)
        if fallback is not None and fallback.is_active:
            return fallback
    return None


def resolve_call(db: Session, call_sid: str) -> VoiceCall:
    call = db.scalar(select(VoiceCall).where(VoiceCall.call_sid == call_sid))
    if call is None:
        raise VoiceError("Unknown CallSid.", status_code=404)
    return call


def voice_book(
    db: Session, call: VoiceCall, start_at, settings: Settings
) -> tuple[Appointment, bool]:
    """Book an exact offered slot for the call's lead. Returns (appt, replayed).

    Idempotent on the CallSid: a replay returns the same appointment. Uses
    the same staff lock, central slot proof, exclusion backstop and durable
    notifications as public booking; no provider call happens here.
    """
    settings_row = messaging.get_settings_row(db)
    lead = db.get(Lead, call.lead_id)
    assert lead is not None
    staff = voice_booking_staff(db, lead, settings_row)
    if staff is None:
        raise VoiceError("No active staff member is available for booking.", status_code=409)

    booking_digest = digest_token(f"voice-book:{call.call_sid}", settings.session_token_pepper)
    scheduling.lock_staff_calendar(db, staff.id)
    existing = db.scalar(
        select(Appointment).where(Appointment.booking_key_digest == booking_digest)
    )
    if existing is not None:
        db.commit()
        return existing, True

    scheduling.assert_bookable_slot(
        db, settings_row, staff.id, start_at, settings_row.appointment_duration_minutes
    )
    appointment = scheduling.create_appointment(
        db,
        None,
        lead,
        settings_row,
        start_at=start_at,
        duration_minutes=settings_row.appointment_duration_minutes,
        subject=(call.service_requested or "Appointment")[:200],
        staff_id=staff.id,
        origin="voice",
        booking_reference=booking_service.new_booking_reference(),
    )
    appointment.booking_key_digest = booking_digest
    db.flush()
    call.appointment_id = appointment.id
    notifications.schedule_for_appointment(db, appointment, settings_row, settings)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = db.scalar(
            select(Appointment).where(Appointment.booking_key_digest == booking_digest)
        )
        if replay is not None:
            return replay, True
        raise scheduling.SlotUnavailableError() from None
    return appointment, False


# --- Transcript and recording retention -----------------------------------


def purge_expired(db: Session) -> tuple[int, int]:
    """Remove expired or consent-less transcript text and recording
    references while preserving the call's summary, outcome and audit trail.
    Returns (purged_transcripts, purged_recordings)."""
    now = utcnow()
    candidates = db.scalars(
        select(VoiceCall).where(
            VoiceCall.purged_at.is_(None),
            (VoiceCall.transcript_text.is_not(None)) | (VoiceCall.recording_sid.is_not(None)),
        )
    )
    transcripts = recordings = 0
    for call in candidates:
        expired = call.retention_expires_at is not None and call.retention_expires_at <= now
        consentless = call.consent_result != "granted"
        if not (expired or consentless):
            continue
        if call.transcript_text is not None:
            call.transcript_text = None
            transcripts += 1
        if call.recording_sid is not None:
            call.recording_sid = None
            recordings += 1
        call.purged_at = now
    db.commit()
    return transcripts, recordings


def call_attention_reason(call: VoiceCall) -> str | None:
    """The concise, single reason a call appears in the attention queue."""
    if call.completion_conflict:
        return "Conflicting completion for this call — review the record"
    if call.transfer_outcome == "failed":
        return "Transfer to a person was requested but failed"
    if call.urgency == "urgent":
        return "Marked urgent by the caller"
    if call.requires_human_follow_up:
        return "Caller asked for a person to follow up"
    if not call.caller_phone:
        return "No callback number was captured"
    if call.ack_state in ("failed", "unknown", "no_destination"):
        return f"Customer confirmation {call.ack_state.replace('_', ' ')}"
    if call.alert_state in ("failed", "unknown", "no_destination"):
        return f"Staff alert {call.alert_state.replace('_', ' ')}"
    return None
