import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    CommunicationSettings,
    Lead,
    LeadActivity,
    OutboundMessage,
    User,
    utcnow,
)
from app.security.tokens import digest_token
from app.services.leads import LeadError, add_activity, can_manage_leads, normalize_phone

logger = logging.getLogger(__name__)

MAX_SMS_LENGTH = 1600  # Twilio's limit for concatenated SMS.

# The only variables templates may use. Unknown variables are rejected so a
# typo never ships a literal "{{whatever}}" to a customer.
TEMPLATE_VARIABLES = ("lead_name", "business_name", "source", "lead_id")

# Additional variables the appointment templates may use.
APPOINTMENT_TEMPLATE_VARIABLES = (
    "lead_name",
    "business_name",
    "appointment_date",
    "appointment_time",
    "assigned_staff",
    "appointment_subject",
    "booking_reference",
)


_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class MessagingError(LeadError):
    """Rejected messaging action; message is safe to return to the client."""


def get_settings_row(db: Session) -> CommunicationSettings:
    """Fetch the one settings row, creating defaults on first use.

    A unique singleton key makes concurrent first access safe: the loser of
    the race gets an IntegrityError and re-reads the winner's row, so two
    rival settings rows can never exist.
    """
    row = db.scalar(select(CommunicationSettings).limit(1))
    if row is not None:
        return row
    savepoint = db.begin_nested()
    try:
        row = CommunicationSettings()
        db.add(row)
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        row = db.scalar(select(CommunicationSettings).limit(1))
        assert row is not None
    db.flush()
    return row


def lock_lead(db: Session, lead_id: uuid.UUID) -> None:
    """Take a row lock on the lead, serializing everything that follows for
    that lead until the caller commits. SQLite ignores FOR UPDATE; the
    PostgreSQL behaviour is covered by the concurrency tests."""
    db.execute(select(Lead.id).where(Lead.id == lead_id).with_for_update())


# A pending outbound row older than this was almost certainly abandoned by a
# crash between the durable insert and recording the provider outcome.
PENDING_RECOVERY_MINUTES = 10


def recover_abandoned_pending(db: Session, lead_id: uuid.UUID | None = None) -> int:
    """Move long-abandoned `pending` messages to the ambiguous `unknown` state.

    Conservative on purpose: never `failed` (the provider may have accepted
    it), never resent automatically, and always left visible with the
    ambiguous-outcome warning. Without this a crashed send would block the
    lead's pending-send guard forever.
    """
    cutoff = utcnow() - timedelta(minutes=PENDING_RECOVERY_MINUTES)
    if lead_id is not None:
        # Serialize with any concurrent send/recovery for this lead so two
        # callers cannot both transition the same row and write duplicate
        # explanatory activities.
        lock_lead(db, lead_id)
    query = select(OutboundMessage).where(
        OutboundMessage.status == "pending", OutboundMessage.created_at < cutoff
    )
    if lead_id is not None:
        query = query.where(OutboundMessage.lead_id == lead_id)

    recovered = 0
    for message in db.scalars(query):
        message.status = "unknown"
        message.error_code = "abandoned"
        message.error_message = (
            "The send was interrupted before the provider confirmed it. "
            "It may or may not have been delivered."
        )
        lead = db.get(Lead, message.lead_id)
        if lead is not None:
            add_activity(
                db,
                lead,
                "message_status",
                f"{PURPOSE_LABELS[message.purpose]} outcome unconfirmed: the send was "
                "interrupted before the provider responded.",
                channel="sms",
                direction="outbound",
                meta={"message_id": str(message.id), "status": "unknown"},
            )
        recovered += 1
    if recovered:
        db.flush()
    return recovered


def validate_template(template: str) -> str:
    unknown = {name for name in _VARIABLE_PATTERN.findall(template)} - set(TEMPLATE_VARIABLES)
    if unknown:
        raise MessagingError(
            "Unknown template variables: "
            + ", ".join(sorted(unknown))
            + ". Allowed: "
            + ", ".join(TEMPLATE_VARIABLES)
        )
    if not template.strip():
        raise MessagingError("Template cannot be empty.")
    if len(template) > MAX_SMS_LENGTH:
        raise MessagingError(f"Template must be at most {MAX_SMS_LENGTH} characters.")
    return template


def validate_appointment_template(template: str) -> str:
    unknown = set(_VARIABLE_PATTERN.findall(template)) - set(APPOINTMENT_TEMPLATE_VARIABLES)
    if unknown:
        raise MessagingError(
            "Unknown template variables: "
            + ", ".join(sorted(unknown))
            + ". Allowed: "
            + ", ".join(APPOINTMENT_TEMPLATE_VARIABLES)
        )
    if not template.strip():
        raise MessagingError("Template cannot be empty.")
    if len(template) > MAX_SMS_LENGTH:
        raise MessagingError(f"Template must be at most {MAX_SMS_LENGTH} characters.")
    return template


def render_with(template: str, values: dict[str, str]) -> str:
    """Substitute a caller-supplied variable map (appointment templates)."""
    return _VARIABLE_PATTERN.sub(lambda match: values.get(match.group(1), ""), template)


def render_template(template: str, lead: Lead, settings_row: CommunicationSettings) -> str:
    values = {
        "lead_name": lead.name or "there",
        "business_name": settings_row.business_name,
        "source": lead.source.replace("_", " "),
        "lead_id": str(lead.id),
    }
    return _VARIABLE_PATTERN.sub(lambda match: values.get(match.group(1), ""), template)


def validate_phone(raw: str | None) -> str | None:
    """Conservative validation: accept a normalized phone, never invent a
    country code."""
    if raw is None or raw.strip() == "":
        return None
    normalized = normalize_phone(raw)
    if normalized is None or not normalized.startswith("+") or len(normalized) < 8:
        raise MessagingError(
            "Enter the phone number in international format, for example +15555550123."
        )
    return normalized


@dataclass
class SendOutcome:
    status: str  # submitted | failed | unknown
    provider_sid: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class N8nSmsSender:
    """Calls the authenticated n8n send workflow, which holds the Twilio
    credentials. Never sees or logs provider secrets itself."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, message: OutboundMessage) -> SendOutcome:
        settings = self._settings
        if not settings.n8n_send_url or not settings.n8n_send_secret:
            return SendOutcome(
                status="failed",
                error_code="not_configured",
                error_message="Outbound SMS is not configured.",
            )
        try:
            response = httpx.post(
                settings.n8n_send_url,
                headers={"X-Send-Secret": settings.n8n_send_secret},
                json={
                    "message_id": str(message.id),
                    "to": message.to_phone,
                    "body": message.body,
                },
                timeout=20.0,
            )
        except httpx.TimeoutException:
            # Ambiguous: the provider may or may not have accepted it. Never
            # auto-resend; a human decides after seeing the unknown state.
            return SendOutcome(
                status="unknown",
                error_code="timeout",
                error_message="No confirmation received from the messaging service.",
            )
        except httpx.HTTPError as error:
            # A transport failure is ambiguous: the request may have reached
            # the provider before the connection broke.
            return SendOutcome(
                status="unknown",
                error_code="transport_error",
                error_message=type(error).__name__,
            )

        if response.status_code >= 500:
            return SendOutcome(
                status="unknown",
                error_code=str(response.status_code),
                error_message="Messaging service error.",
            )
        try:
            data = response.json()
        except ValueError:
            return SendOutcome(
                status="unknown", error_code="bad_response", error_message="Unreadable response."
            )

        # The send workflow classifies the provider outcome; trust it verbatim
        # so an ambiguous result is never upgraded to a definite one.
        reported = str(data.get("status") or "")
        error_code = str(data.get("error_code") or response.status_code)[:32]
        error_message = str(data.get("error_message") or "Send rejected.")[:500]
        if response.status_code >= 400 or reported == "failed":
            return SendOutcome(status="failed", error_code=error_code, error_message=error_message)
        if reported == "submitted" and data.get("sid"):
            return SendOutcome(status="submitted", provider_sid=str(data["sid"])[:64])
        # Nominal success without a usable SID, or an explicit "unknown".
        return SendOutcome(
            status="unknown",
            error_code=error_code if reported == "unknown" else "no_sid",
            error_message=(
                error_message
                if reported == "unknown"
                else "The provider did not confirm a message id."
            ),
        )


def _apply_outcome(db: Session, message: OutboundMessage, outcome: SendOutcome) -> None:
    now = utcnow()
    message.status = outcome.status
    message.error_code = outcome.error_code
    message.error_message = outcome.error_message
    if outcome.provider_sid:
        message.provider_sid = outcome.provider_sid
    if outcome.status == "submitted":
        message.submitted_at = now
    elif outcome.status == "failed":
        message.failed_at = now
    db.flush()


PURPOSE_LABELS = {
    "human_reply": "SMS sent",
    "auto_acknowledgment": "Automated acknowledgment",
    "staff_alert": "New-lead alert",
    "appointment": "Appointment message",
}


def create_and_send(
    db: Session,
    lead: Lead,
    *,
    purpose: str,
    to_phone: str,
    body: str,
    idempotency_key: str,
    settings: Settings,
    sender,
    acting_user: User | None = None,
    related_activity_id: uuid.UUID | None = None,
    serialize_on_lead: bool = False,
) -> OutboundMessage:
    """Persist the outbound record, commit it, then contact the provider.

    The durable record always exists before the provider is called, so an
    interrupted send is visible rather than lost. Duplicate idempotency keys
    return the existing record without sending again.
    """
    digest = digest_token(idempotency_key, settings.session_token_pepper)
    existing = db.scalar(
        select(OutboundMessage).where(OutboundMessage.idempotency_key_digest == digest)
    )
    if existing is not None:
        return existing

    if serialize_on_lead:
        # Hold the lead lock across the pending check and the durable insert
        # so a concurrent request with a different key cannot slip past the
        # guard. The lock is released by the commit below, well before the
        # provider is contacted.
        lock_lead(db, lead.id)
        # Re-check the key under the lock: a concurrent request with the SAME
        # key may have committed its record while we waited, and that must
        # still be idempotent rather than a conflict.
        existing = db.scalar(
            select(OutboundMessage).where(OutboundMessage.idempotency_key_digest == digest)
        )
        if existing is not None:
            db.commit()
            return existing
        recover_abandoned_pending(db, lead.id)
        pending = db.scalar(
            select(OutboundMessage).where(
                OutboundMessage.lead_id == lead.id, OutboundMessage.status == "pending"
            )
        )
        if pending is not None:
            db.commit()
            raise MessagingError("A message is already being sent for this lead.", status_code=409)

    message = OutboundMessage(
        lead_id=lead.id,
        purpose=purpose,
        to_phone=to_phone,
        from_phone=settings.twilio_from_number or None,
        body=body,
        status="pending",
        idempotency_key_digest=digest,
        created_by=acting_user.id if acting_user is not None else None,
        related_activity_id=related_activity_id,
    )
    db.add(message)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request with the same key won; return that record and
        # never send a second copy.
        db.rollback()
        duplicate = db.scalar(
            select(OutboundMessage).where(OutboundMessage.idempotency_key_digest == digest)
        )
        if duplicate is not None:
            return duplicate
        raise

    outcome = sender.send(message)
    _apply_outcome(db, message, outcome)

    if outcome.status in ("submitted", "unknown"):
        add_activity(
            db,
            lead,
            "outbound_message",
            f"{PURPOSE_LABELS[purpose]} to {to_phone}: {body}",
            acting_user=acting_user,
            channel="sms",
            direction="outbound",
            provider="twilio",
            external_event_id=message.provider_sid,
            meta={"purpose": purpose, "message_id": str(message.id), "status": outcome.status},
        )
        if purpose == "human_reply" and outcome.status == "submitted":
            record_human_response(db, lead, at=utcnow())
    else:
        add_activity(
            db,
            lead,
            "message_status",
            f"{PURPOSE_LABELS[purpose]} failed: {outcome.error_message or 'unknown error'}",
            acting_user=acting_user,
            channel="sms",
            direction="outbound",
            meta={"purpose": purpose, "message_id": str(message.id), "status": "failed"},
        )
    db.commit()
    return message


def start_response_clock(db: Session, lead: Lead, settings_row: CommunicationSettings) -> None:
    """Begin first-response tracking on the first inbound request only."""
    if lead.first_inbound_at is not None:
        return
    now = utcnow()
    lead.first_inbound_at = now
    lead.response_due_at = now + timedelta(minutes=settings_row.response_target_minutes)
    db.flush()


def record_human_response(db: Session, lead: Lead, at) -> None:
    """Record the first genuine human response. Automated acknowledgments and
    staff alerts never call this."""
    if lead.first_response_at is not None or lead.first_inbound_at is None:
        return
    lead.first_response_at = at
    lead.first_response_seconds = int((at - lead.first_inbound_at).total_seconds())
    lead.response_target_met = lead.response_due_at is None or at <= lead.response_due_at
    db.flush()


def mark_contacted_outside_crm(db: Session, acting_user: User, lead: Lead) -> Lead:
    """Deliberate action for phone calls and other off-platform contact."""
    if lead.archived_at is not None:
        raise MessagingError("Archived leads must be restored first.", status_code=409)
    if lead.first_response_at is not None:
        raise MessagingError("A first response is already recorded.", status_code=409)
    if lead.first_inbound_at is None:
        raise MessagingError("This lead has no inbound request to respond to.")
    now = utcnow()
    record_human_response(db, lead, at=now)
    lead.last_contacted_at = now
    add_activity(
        db,
        lead,
        "contacted_outside_crm",
        f"Marked contacted outside the CRM by {acting_user.email}.",
        acting_user=acting_user,
    )
    db.flush()
    return lead


def send_lead_sms(
    db: Session,
    acting_user: User,
    lead: Lead,
    body: str,
    idempotency_key: str,
    settings: Settings,
    sender,
) -> OutboundMessage:
    """Staff-originated SMS. Authorization is asserted here, not only in the route."""
    if not can_manage_leads(acting_user) and lead.assigned_to != acting_user.id:
        raise MessagingError("You can only message leads assigned to you.", status_code=403)
    if lead.archived_at is not None:
        raise MessagingError("Restore this lead before sending messages.", status_code=409)
    text = body.strip()
    if not text:
        raise MessagingError("Message content is required.")
    if len(text) > MAX_SMS_LENGTH:
        raise MessagingError(f"Message must be at most {MAX_SMS_LENGTH} characters.")
    if not lead.phone or not lead.phone.startswith("+"):
        raise MessagingError("This lead has no phone number in international format.")

    # Recovery, the pending check and the durable insert all happen under a
    # per-lead row lock inside create_and_send.
    return create_and_send(
        db,
        lead,
        purpose="human_reply",
        to_phone=lead.phone,
        body=text,
        idempotency_key=idempotency_key,
        settings=settings,
        sender=sender,
        acting_user=acting_user,
        serialize_on_lead=True,
    )


def send_automated_messages(
    db: Session,
    lead: Lead,
    activity: LeadActivity,
    settings: Settings,
    sender,
) -> list[OutboundMessage]:
    """Acknowledgment and staff alert for a newly stored web-form event.

    Called only after the inbound write has committed. Deduplicated by the
    inbound activity id plus purpose, so provider or browser retries never
    send a second copy. Failures are recorded and never roll back the lead.
    """
    settings_row = get_settings_row(db)
    sent: list[OutboundMessage] = []

    if settings_row.acknowledgment_enabled and lead.phone and lead.phone.startswith("+"):
        sent.append(
            create_and_send(
                db,
                lead,
                purpose="auto_acknowledgment",
                to_phone=lead.phone,
                body=render_template(settings_row.acknowledgment_template, lead, settings_row),
                idempotency_key=f"auto_acknowledgment:{activity.id}",
                settings=settings,
                sender=sender,
                related_activity_id=activity.id,
            )
        )

    if settings_row.alert_enabled and settings_row.alert_destination_phone:
        # Minimum useful information only — never the full request content.
        sent.append(
            create_and_send(
                db,
                lead,
                purpose="staff_alert",
                to_phone=settings_row.alert_destination_phone,
                body=render_template(settings_row.alert_template, lead, settings_row),
                idempotency_key=f"staff_alert:{activity.id}",
                settings=settings,
                sender=sender,
                related_activity_id=activity.id,
            )
        )
    return sent


# Twilio message statuses mapped onto our own delivery states.
PROVIDER_STATUS_MAP = {
    "queued": "submitted",
    "accepted": "submitted",
    "scheduled": "submitted",
    "sending": "submitted",
    "sent": "submitted",
    "delivered": "delivered",
    "undelivered": "failed",
    "failed": "failed",
    "canceled": "failed",
}

# The only permitted delivery-state transitions. Callbacks arrive out of
# order and more than once, so the state machine is deliberately monotonic:
# `delivered` and `failed` are terminal, and an ambiguous `unknown` may still
# be resolved by a definitive provider outcome.
ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"submitted", "delivered", "failed", "unknown"}),
    "submitted": frozenset({"delivered", "failed"}),
    "unknown": frozenset({"submitted", "delivered", "failed"}),
    "delivered": frozenset(),  # terminal
    "failed": frozenset(),  # terminal
}


def transition_allowed(current: str, proposed: str) -> bool:
    """Central definition of permitted delivery-state moves."""
    if current == proposed:
        return False  # repeated callback: nothing to change
    return proposed in ALLOWED_STATUS_TRANSITIONS.get(current, frozenset())


def apply_delivery_status(
    db: Session, provider_sid: str, status: str, error_code: str | None, error_message: str | None
) -> OutboundMessage | None:
    """Idempotently apply a Twilio status callback. Unknown SIDs are ignored —
    they never create leads or records."""
    message = db.scalar(select(OutboundMessage).where(OutboundMessage.provider_sid == provider_sid))
    if message is None:
        return None

    new_status = PROVIDER_STATUS_MAP.get(status)
    if new_status is None or not transition_allowed(message.status, new_status):
        # Unrecognised, repeated, out-of-order or terminal-state callback.
        return message

    now = utcnow()
    message.status = new_status
    if new_status == "delivered":
        message.delivered_at = now
    elif new_status == "failed":
        message.failed_at = now
        message.error_code = (error_code or None) and str(error_code)[:32]
        message.error_message = (error_message or None) and str(error_message)[:500]
    else:
        message.submitted_at = message.submitted_at or now

    lead = db.get(Lead, message.lead_id)
    if lead is not None and new_status in ("delivered", "failed"):
        add_activity(
            db,
            lead,
            "message_status",
            f"{PURPOSE_LABELS[message.purpose]} {new_status}"
            + (f" ({message.error_message})" if message.error_message else "")
            + ".",
            channel="sms",
            direction="outbound",
            provider="twilio",
            external_event_id=provider_sid,
            meta={"message_id": str(message.id), "status": new_status},
        )
    db.flush()
    return message
