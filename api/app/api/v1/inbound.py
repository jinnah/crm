import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from app.api.v1.deps import DbDep, SettingsDep, get_sms_sender
from app.api.v1.schemas import (
    InboundEventRequest,
    InboundEventResponse,
    MessageStatusRequest,
    MessageStatusResponse,
)
from app.security.tokens import constant_time_equals
from app.services import messaging
from app.services.inbound import process_inbound_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inbound", tags=["inbound"])

# Enforced pre-parse by BodyLimitMiddleware in app.main for /api/v1/inbound.
MAX_BODY_BYTES = 64 * 1024


@router.post("/events", response_model=InboundEventResponse)
def create_inbound_event(
    body: InboundEventRequest,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> InboundEventResponse:
    _require_inbound_key(request, settings)
    result = process_inbound_event(db, body, idempotency_key, settings)

    # The lead and its inbound activity are committed at this point. Response
    # tracking and automated messages happen after, and never roll the lead
    # back if messaging fails.
    settings_row = messaging.get_settings_row(db)
    messaging.start_response_clock(db, result.lead, settings_row)
    db.commit()

    if body.channel == "web_form" and not result.replayed:
        try:
            messaging.send_automated_messages(
                db, result.lead, result.activity, settings, get_sms_sender(request)
            )
        except Exception as error:  # never lose a stored lead over a failed SMS
            db.rollback()
            logger.warning("Automated messaging failed: %s", type(error).__name__)

    return InboundEventResponse(
        lead_id=result.lead.id,
        activity_id=result.activity.id,
        lead_created=result.lead_created,
        replayed=result.replayed,
    )


@router.post("/message-status", response_model=MessageStatusResponse)
def update_message_status(
    body: MessageStatusRequest, request: Request, db: DbDep, settings: SettingsDep
) -> MessageStatusResponse:
    """Twilio delivery callback, relayed by the signed n8n status workflow.
    Unknown SIDs are acknowledged without creating anything."""
    _require_inbound_key(request, settings)
    message = messaging.apply_delivery_status(
        db, body.provider_sid, body.status, body.error_code, body.error_message
    )
    db.commit()
    if message is None:
        return MessageStatusResponse(matched=False, status=None)
    return MessageStatusResponse(matched=True, status=message.status)


def _require_inbound_key(request: Request, settings) -> None:
    """Server-to-server auth for n8n. The key never reaches a browser; a
    missing configuration rejects everything (fail closed) with the same
    generic error as a wrong key."""
    provided = request.headers.get("X-API-Key") or ""
    configured = settings.inbound_api_key
    if not configured or not provided or not constant_time_equals(provided, configured):
        raise HTTPException(status_code=401, detail="Invalid API key.")
