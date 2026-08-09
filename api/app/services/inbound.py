import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import InboundEvent, Lead, LeadActivity, LeadExternalIdentity
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
    """Raised when concurrent duplicates cannot be reconciled after retrying."""


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


def _find_external_identity(db: Session, payload: Any) -> Lead | None:
    if not payload.external_sender_id:
        return None
    identity = db.scalar(
        select(LeadExternalIdentity).where(
            LeadExternalIdentity.channel == payload.channel,
            LeadExternalIdentity.provider == (payload.provider or ""),
            LeadExternalIdentity.external_sender_id == payload.external_sender_id,
        )
    )
    return db.get(Lead, identity.lead_id) if identity is not None else None


def _attach_external_identity(db: Session, payload: Any, lead: Lead) -> None:
    if not payload.external_sender_id:
        return
    db.add(
        LeadExternalIdentity(
            channel=payload.channel,
            provider=payload.provider or "",
            external_sender_id=payload.external_sender_id,
            lead_id=lead.id,
        )
    )
    db.flush()


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


def _resolve_lead(
    db: Session, payload: Any, email: str | None, phone: str | None, name: str
) -> tuple[Lead, bool]:
    """Find or create the lead for an inbound event.

    Priority: an existing external identity (channel + provider + sender ID)
    wins; otherwise conservative email/phone matching; otherwise a new lead.
    Conflicting identifiers never merge leads — they flag for review instead.
    """
    has_contact_identity = email is not None or phone is not None

    identity_lead = _find_external_identity(db, payload)
    if identity_lead is not None:
        if has_contact_identity:
            contact_match, ambiguous = _match_lead(db, email, phone)
            if ambiguous or (contact_match is not None and contact_match.id != identity_lead.id):
                # The provider identity and the contact details point at
                # different records; keep the identity lead, do not merge,
                # and flag the uncertainty for a human.
                identity_lead.needs_review = True
            else:
                _fill_missing_identity(
                    identity_lead, name=name, email=email, phone=phone, company=""
                )
        else:
            _fill_missing_identity(identity_lead, name=name, email=None, phone=None, company="")
        return identity_lead, False

    lead, ambiguous = (None, False)
    if has_contact_identity:
        lead, ambiguous = _match_lead(db, email, phone)

    if lead is not None:
        _fill_missing_identity(lead, name=name, email=email, phone=phone, company="")
        _attach_external_identity(db, payload, lead)
        return lead, False

    new_lead = Lead(
        name=name,
        email=email,
        phone=phone,
        status="new",
        source=payload.channel,
        needs_review=ambiguous or not has_contact_identity,
    )
    db.add(new_lead)
    db.flush()
    _attach_external_identity(db, payload, new_lead)
    return new_lead, True


def process_inbound_event(
    db: Session, payload: Any, idempotency_key: str, settings: Settings
) -> InboundResult:
    """Match or create the lead, record the inbound activity, and persist the
    idempotency row — all in one transaction. Retries with the same key replay
    the stored result. Unique-constraint losers (concurrent duplicate keys or
    concurrent identity creation) roll back and re-process once."""
    digest = digest_token(idempotency_key, settings.session_token_pepper)
    for attempt in range(2):
        replay = _find_replay(db, digest)
        if replay is not None:
            return replay

        email = clean_optional_email(payload.sender_email)
        phone = normalize_phone(payload.sender_phone)
        name = (payload.sender_name or "").strip()

        lead, lead_created = _resolve_lead(db, payload, email, phone, name)
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

        db.add(
            InboundEvent(
                idempotency_key_digest=digest,
                lead_id=lead.id,
                activity_id=activity.id,
                lead_created=lead_created,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # A concurrent request won a unique constraint (idempotency digest
            # or external identity). Roll back and re-process: an idempotency
            # duplicate replays, an identity race re-matches the new row.
            db.rollback()
            if attempt == 0:
                continue
            raise InboundConflictError("Concurrent inbound event could not be reconciled") from None
        return InboundResult(
            lead=lead, activity=activity, lead_created=lead_created, replayed=False
        )
    raise InboundConflictError(
        "Concurrent inbound event could not be reconciled"
    )  # pragma: no cover
