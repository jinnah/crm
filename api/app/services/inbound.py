import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import InboundEvent, Lead, LeadActivity
from app.security.tokens import digest_token
from app.services.leads import add_activity, clean_optional_email, normalize_phone

logger = logging.getLogger(__name__)


@dataclass
class InboundResult:
    lead: Lead
    activity: LeadActivity
    lead_created: bool
    replayed: bool


class InboundConflictError(Exception):
    """Raised when a concurrent request with the same idempotency key won."""


def _find_replay(db: Session, digest: str) -> InboundResult | None:
    event = db.scalar(select(InboundEvent).where(InboundEvent.idempotency_key_digest == digest))
    if event is None:
        return None
    lead = db.get(Lead, event.lead_id)
    activity = db.get(LeadActivity, event.activity_id)
    assert lead is not None and activity is not None
    return InboundResult(
        lead=lead, activity=activity, lead_created=event.lead_created, replayed=True
    )


def _match_lead(db: Session, email: str | None, phone: str | None) -> tuple[Lead | None, bool]:
    """Conservative matching: exact normalized email or phone only.

    Returns (lead, ambiguous). Multiple candidates for one identifier, or
    identifiers pointing at different leads, are ambiguous — the caller then
    creates a fresh needs_review lead instead of silently merging.
    """
    email_matches: list[Lead] = []
    phone_matches: list[Lead] = []
    if email:
        email_matches = list(db.scalars(select(Lead).where(Lead.email == email)))
    if phone:
        phone_matches = list(db.scalars(select(Lead).where(Lead.phone == phone)))

    if len(email_matches) > 1 or len(phone_matches) > 1:
        return None, True
    email_match = email_matches[0] if email_matches else None
    phone_match = phone_matches[0] if phone_matches else None
    if email_match is not None and phone_match is not None and email_match.id != phone_match.id:
        return None, True
    return email_match or phone_match, False


def _fill_missing_identity(
    lead: Lead, *, name: str, email: str | None, phone: str | None, company: str
) -> None:
    """Fill blanks from the inbound event; never overwrite populated fields."""
    if not lead.name and name:
        lead.name = name
    if lead.email is None and email:
        lead.email = email
    if lead.phone is None and phone:
        lead.phone = phone
    if not lead.company and company:
        lead.company = company


def _build_content(payload: Any) -> str:
    parts = []
    if payload.subject:
        parts.append(payload.subject.strip())
    if payload.content:
        parts.append(payload.content.strip())
    return "\n\n".join(part for part in parts if part) or f"Inbound {payload.channel} event."


def process_inbound_event(
    db: Session, payload: Any, idempotency_key: str, settings: Settings
) -> InboundResult:
    """Match or create the lead, record the inbound activity, and persist the
    idempotency row — all in one transaction. Retries with the same key replay
    the stored result; a concurrent duplicate loses on the unique digest and
    is replayed by the caller after rollback."""
    digest = digest_token(idempotency_key, settings.session_token_pepper)
    replay = _find_replay(db, digest)
    if replay is not None:
        return replay

    email = clean_optional_email(payload.sender_email)
    phone = normalize_phone(payload.sender_phone)
    name = (payload.sender_name or "").strip()
    has_identity = email is not None or phone is not None

    lead, ambiguous = (None, False)
    if has_identity:
        lead, ambiguous = _match_lead(db, email, phone)

    lead_created = lead is None
    if lead is None:
        lead = Lead(
            name=name,
            email=email,
            phone=phone,
            status="new",
            source=payload.channel,
            needs_review=ambiguous or not has_identity,
        )
        db.add(lead)
        db.flush()
    else:
        _fill_missing_identity(lead, name=name, email=email, phone=phone, company="")
        if lead.archived_at is not None:
            # New inbound contact on an archived lead: bring it back and flag it.
            lead.archived_at = None
            lead.needs_review = True
            add_activity(
                db, lead, "restored", "Lead restored automatically by a new inbound request."
            )

    meta: dict[str, Any] = dict(payload.metadata or {})
    for key in ("event_type", "external_sender_id"):
        value = getattr(payload, key)
        if value:
            meta[key] = value

    activity = add_activity(
        db,
        lead,
        "inbound_request",
        _build_content(payload),
        channel=payload.channel,
        direction="inbound",
        provider=payload.provider,
        external_event_id=payload.external_event_id,
        occurred_at=payload.received_at,
        meta=meta or None,
    )

    event = InboundEvent(
        idempotency_key_digest=digest,
        lead_id=lead.id,
        activity_id=activity.id,
        lead_created=lead_created,
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request with the same idempotency key committed first;
        # everything from this attempt rolls back and we replay its result.
        db.rollback()
        replay = _find_replay(db, digest)
        if replay is None:  # pragma: no cover - defensive
            raise InboundConflictError("Concurrent duplicate could not be replayed") from None
        return replay
    return InboundResult(lead=lead, activity=activity, lead_created=lead_created, replayed=False)
