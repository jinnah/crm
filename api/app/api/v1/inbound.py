from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from app.api.v1.deps import DbDep, SettingsDep
from app.api.v1.schemas import InboundEventRequest, InboundEventResponse
from app.security.tokens import constant_time_equals
from app.services.inbound import process_inbound_event

router = APIRouter(prefix="/inbound", tags=["inbound"])

MAX_BODY_BYTES = 64 * 1024


def _require_inbound_key(request: Request, settings) -> None:
    """Server-to-server auth for n8n. The key never reaches a browser; a
    missing configuration rejects everything (fail closed) with the same
    generic error as a wrong key."""
    provided = request.headers.get("X-API-Key") or ""
    configured = settings.inbound_api_key
    if not configured or not provided or not constant_time_equals(provided, configured):
        raise HTTPException(status_code=401, detail="Invalid API key.")


@router.post("/events", response_model=InboundEventResponse)
def create_inbound_event(
    body: InboundEventRequest,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> InboundEventResponse:
    _require_inbound_key(request, settings)
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large.")
    result = process_inbound_event(db, body, idempotency_key, settings)
    return InboundEventResponse(
        lead_id=result.lead.id,
        activity_id=result.activity.id,
        lead_created=result.lead_created,
        replayed=result.replayed,
    )
