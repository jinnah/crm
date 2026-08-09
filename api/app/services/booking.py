"""Customer booking links and the public booking flow.

A booking link is a revocable, expiring capability. Only its keyed digest is
stored, the public page exposes nothing but presentation data and free slots,
and a separate per-appointment capability governs customer reschedule/cancel —
knowing an appointment's UUID grants nothing.
"""

import secrets
import string
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Appointment, BookingLink, Lead, User, utcnow
from app.security.tokens import digest_token, generate_token
from app.services.leads import add_activity, can_manage_leads
from app.services.scheduling import SchedulingError

DEFAULT_LINK_TTL_DAYS = 14
_REFERENCE_ALPHABET = string.ascii_uppercase + string.digits


def new_booking_reference() -> str:
    """Short human-quotable reference; not a secret and not a capability."""
    return "APT-" + "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(8))


def _assert_may_manage_links(acting_user: User, lead: Lead) -> None:
    if can_manage_leads(acting_user):
        return
    if lead.assigned_to != acting_user.id:
        raise SchedulingError(
            "You can only create booking links for leads assigned to you.", status_code=403
        )


def create_link(
    db: Session,
    acting_user: User,
    lead: Lead,
    settings: Settings,
    *,
    staff_id: uuid.UUID | None = None,
    duration_minutes: int | None = None,
    ttl_days: int = DEFAULT_LINK_TTL_DAYS,
) -> tuple[BookingLink, str]:
    """Create a link, returning it with the raw token (shown once)."""
    _assert_may_manage_links(acting_user, lead)
    if lead.archived_at is not None:
        raise SchedulingError("Restore this lead before creating a booking link.", 409)
    if staff_id is not None:
        staff = db.get(User, staff_id)
        if staff is None or not staff.is_active:
            raise SchedulingError("Booking links can only target an active user.")
        if not can_manage_leads(acting_user) and staff_id != acting_user.id:
            raise SchedulingError("You can only offer your own calendar.", status_code=403)
    if ttl_days < 1 or ttl_days > 90:
        raise SchedulingError("A booking link may last between 1 and 90 days.")

    # Revoke any live link for this lead so only one is valid at a time.
    revoke_active(db, lead.id)

    raw = generate_token()
    link = BookingLink(
        lead_id=lead.id,
        assigned_to=staff_id,
        token_digest=digest_token(raw, settings.session_token_pepper),
        expires_at=utcnow() + timedelta(days=ttl_days),
        duration_minutes=duration_minutes,
        created_by=acting_user.id,
    )
    db.add(link)
    db.flush()
    add_activity(
        db,
        lead,
        "booking_link_created",
        f"Booking link created, valid for {ttl_days} days.",
        acting_user=acting_user,
        meta={"booking_link_id": str(link.id)},
    )
    return link, raw


def revoke_active(db: Session, lead_id: uuid.UUID) -> int:
    now = utcnow()
    links = db.scalars(
        select(BookingLink).where(
            BookingLink.lead_id == lead_id,
            BookingLink.revoked_at.is_(None),
            BookingLink.expires_at > now,
        )
    )
    revoked = 0
    for link in links:
        link.revoked_at = now
        revoked += 1
    if revoked:
        db.flush()
    return revoked


def revoke_link(db: Session, acting_user: User, lead: Lead, link: BookingLink) -> BookingLink:
    _assert_may_manage_links(acting_user, lead)
    if link.revoked_at is None:
        link.revoked_at = utcnow()
        db.flush()
        add_activity(
            db,
            lead,
            "booking_link_revoked",
            "Booking link revoked.",
            acting_user=acting_user,
            meta={"booking_link_id": str(link.id)},
        )
    return link


def active_link_for(db: Session, lead_id: uuid.UUID) -> BookingLink | None:
    now = utcnow()
    return db.scalar(
        select(BookingLink)
        .where(
            BookingLink.lead_id == lead_id,
            BookingLink.revoked_at.is_(None),
            BookingLink.expires_at > now,
        )
        .order_by(BookingLink.created_at.desc())
    )


def resolve_token(db: Session, raw_token: str, settings: Settings) -> BookingLink:
    """Look a link up by its raw token, rejecting expired or revoked ones.

    The failure messages deliberately do not distinguish "never existed" from
    "revoked" beyond what a customer needs to know.
    """
    digest = digest_token(raw_token, settings.session_token_pepper)
    link = db.scalar(select(BookingLink).where(BookingLink.token_digest == digest))
    if link is None:
        raise SchedulingError("This booking link is not valid.", status_code=404)
    if link.revoked_at is not None:
        raise SchedulingError("This booking link has been withdrawn.", status_code=410)
    if link.expires_at <= utcnow():
        raise SchedulingError("This booking link has expired.", status_code=410)
    return link


def issue_manage_token(settings: Settings) -> tuple[str, str]:
    """Capability for customer-side reschedule/cancel of one appointment."""
    raw = generate_token()
    return raw, digest_token(raw, settings.session_token_pepper)


def resolve_manage_token(db: Session, raw_token: str, settings: Settings) -> Appointment:
    digest = digest_token(raw_token, settings.session_token_pepper)
    appointment = db.scalar(select(Appointment).where(Appointment.manage_token_digest == digest))
    if appointment is None:
        raise SchedulingError("This appointment link is not valid.", status_code=404)
    return appointment
