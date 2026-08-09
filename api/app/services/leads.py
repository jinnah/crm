import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import CHANNELS, LEAD_STATUSES, Lead, LeadActivity, User, utcnow
from app.services.auth import normalize_email


class LeadError(Exception):
    """Rejected lead action; message is safe to return to the client."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


_PHONE_JUNK = re.compile(r"[\s\-().]")
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone(raw: str | None) -> str | None:
    """Strip formatting; return E.164 when the number carries its own country
    code. A number without one is kept digits-only — never guess a country."""
    if raw is None:
        return None
    cleaned = _PHONE_JUNK.sub("", raw.strip())
    if not cleaned:
        return None
    if cleaned.startswith("+") and _E164.match(cleaned):
        return cleaned
    digits = re.sub(r"\D", "", cleaned)
    return digits or None


def clean_optional_email(raw: str | None) -> str | None:
    if raw is None or raw.strip() == "":
        return None
    return normalize_email(raw)


def can_manage_leads(user: User) -> bool:
    return user.role in ("owner", "manager")


def visible_leads_query(user: User) -> Select[tuple[Lead]]:
    query = select(Lead).options(joinedload(Lead.assignee))
    if not can_manage_leads(user):
        query = query.where(Lead.assigned_to == user.id)
    return query


def get_visible_lead(db: Session, user: User, lead_id: uuid.UUID) -> Lead:
    lead = db.scalar(visible_leads_query(user).where(Lead.id == lead_id))
    if lead is None:
        raise LeadError("Lead not found.", status_code=404)
    return lead


def add_activity(
    db: Session,
    lead: Lead,
    type_: str,
    content: str,
    *,
    acting_user: User | None = None,
    channel: str | None = None,
    direction: str | None = None,
    provider: str | None = None,
    external_event_id: str | None = None,
    occurred_at: datetime | None = None,
    meta: dict[str, Any] | None = None,
) -> LeadActivity:
    activity = LeadActivity(
        lead_id=lead.id,
        type=type_,
        content=content,
        created_by=acting_user.id if acting_user is not None else None,
        channel=channel,
        direction=direction,
        provider=provider,
        external_event_id=external_event_id,
        occurred_at=occurred_at,
        meta=meta,
    )
    db.add(activity)
    db.flush()
    return activity


def _validate_assignee(db: Session, user_id: uuid.UUID) -> User:
    assignee = db.get(User, user_id)
    if assignee is None or not assignee.is_active:
        raise LeadError("Assignee must be an active user.")
    return assignee


def create_lead(
    db: Session,
    acting_user: User,
    *,
    name: str = "",
    email: str | None = None,
    phone: str | None = None,
    company: str = "",
    status: str = "new",
    source: str = "manual",
    assigned_to: uuid.UUID | None = None,
    next_follow_up_at: datetime | None = None,
) -> Lead:
    if not can_manage_leads(acting_user):
        raise LeadError("You are not allowed to create leads.", status_code=403)
    if status not in LEAD_STATUSES:
        raise LeadError("Invalid status.")
    if source not in CHANNELS:
        raise LeadError("Invalid source.")
    assignee = None
    if assigned_to is not None:
        assignee = _validate_assignee(db, assigned_to)
    lead = Lead(
        name=name.strip(),
        email=clean_optional_email(email),
        phone=normalize_phone(phone),
        company=company.strip(),
        status=status,
        source=source,
        assigned_to=assigned_to,
        next_follow_up_at=next_follow_up_at,
    )
    db.add(lead)
    db.flush()
    add_activity(db, lead, "created", "Lead created.", acting_user=acting_user)
    if assignee is not None:
        add_activity(
            db, lead, "assignment_change", f"Assigned to {assignee.email}.", acting_user=acting_user
        )
    if next_follow_up_at is not None:
        add_activity(
            db,
            lead,
            "follow_up_scheduled",
            f"Follow-up scheduled for {next_follow_up_at.isoformat()}.",
            acting_user=acting_user,
        )
    return lead


def update_lead_fields(
    db: Session,
    acting_user: User,
    lead: Lead,
    changes: dict[str, Any],
) -> Lead:
    """Apply permitted field changes; team members may only touch status and
    follow-up on their own leads (visibility already enforced upstream)."""
    if lead.archived_at is not None:
        raise LeadError("Archived leads must be restored before editing.", status_code=409)

    manager = can_manage_leads(acting_user)
    allowed = (
        {"status", "next_follow_up_at"}
        if not manager
        else {
            "name",
            "email",
            "phone",
            "company",
            "status",
            "source",
            "next_follow_up_at",
            "needs_review",
        }
    )
    rejected = set(changes) - allowed
    if rejected:
        raise LeadError("You are not allowed to change: " + ", ".join(sorted(rejected)), 403)

    if "status" in changes:
        new_status = changes["status"]
        if new_status not in LEAD_STATUSES:
            raise LeadError("Invalid status.")
        if new_status != lead.status:
            add_activity(
                db,
                lead,
                "status_change",
                f"Status changed from {lead.status} to {new_status}.",
                acting_user=acting_user,
            )
            lead.status = new_status
            if new_status == "contacted":
                lead.last_contacted_at = utcnow()

    if "next_follow_up_at" in changes:
        _apply_follow_up_change(db, acting_user, lead, changes["next_follow_up_at"])

    if manager:
        if "source" in changes and changes["source"] not in CHANNELS:
            raise LeadError("Invalid source.")
        for field in ("name", "company", "source"):
            if field in changes:
                setattr(lead, field, str(changes[field]).strip())
        if "email" in changes:
            lead.email = clean_optional_email(changes["email"])
        if "phone" in changes:
            lead.phone = normalize_phone(changes["phone"])
        if "needs_review" in changes:
            lead.needs_review = bool(changes["needs_review"])

    db.flush()
    return lead


def _apply_follow_up_change(
    db: Session, acting_user: User, lead: Lead, new_value: datetime | None
) -> None:
    old = lead.next_follow_up_at
    if old == new_value:
        return
    if old is None:
        type_, content = (
            "follow_up_scheduled",
            f"Follow-up scheduled for {new_value.isoformat()}.",  # type: ignore[union-attr]
        )
    elif new_value is None:
        type_, content = "follow_up_cleared", "Follow-up cleared."
    else:
        type_, content = "follow_up_changed", f"Follow-up moved to {new_value.isoformat()}."
    lead.next_follow_up_at = new_value
    add_activity(db, lead, type_, content, acting_user=acting_user)


def complete_follow_up(db: Session, acting_user: User, lead: Lead) -> Lead:
    if lead.archived_at is not None:
        raise LeadError("Archived leads must be restored before editing.", status_code=409)
    if lead.next_follow_up_at is None:
        raise LeadError("No follow-up is scheduled.")
    lead.next_follow_up_at = None
    lead.last_contacted_at = utcnow()
    add_activity(db, lead, "follow_up_completed", "Follow-up completed.", acting_user=acting_user)
    db.flush()
    return lead


def assign_lead(db: Session, acting_user: User, lead: Lead, user_id: uuid.UUID | None) -> Lead:
    if not can_manage_leads(acting_user):
        raise LeadError("You are not allowed to assign leads.", status_code=403)
    if lead.archived_at is not None:
        raise LeadError("Archived leads must be restored before editing.", status_code=409)
    if user_id == lead.assigned_to:
        return lead
    if user_id is None:
        content = "Unassigned."
    else:
        assignee = _validate_assignee(db, user_id)
        content = f"Assigned to {assignee.email}."
    lead.assigned_to = user_id
    add_activity(db, lead, "assignment_change", content, acting_user=acting_user)
    db.flush()
    return lead


def archive_lead(db: Session, acting_user: User, lead: Lead) -> Lead:
    if not can_manage_leads(acting_user):
        raise LeadError("You are not allowed to archive leads.", status_code=403)
    if lead.archived_at is not None:
        raise LeadError("Lead is already archived.", status_code=409)
    lead.archived_at = utcnow()
    add_activity(db, lead, "archived", "Lead archived.", acting_user=acting_user)
    db.flush()
    return lead


def restore_lead(db: Session, acting_user: User, lead: Lead) -> Lead:
    if not can_manage_leads(acting_user):
        raise LeadError("You are not allowed to restore leads.", status_code=403)
    if lead.archived_at is None:
        raise LeadError("Lead is not archived.", status_code=409)
    lead.archived_at = None
    add_activity(db, lead, "restored", "Lead restored.", acting_user=acting_user)
    db.flush()
    return lead


def add_note(db: Session, acting_user: User, lead: Lead, content: str) -> LeadActivity:
    if lead.archived_at is not None:
        raise LeadError("Archived leads must be restored before adding notes.", status_code=409)
    text = content.strip()
    if not text:
        raise LeadError("Note content is required.")
    if len(text) > 10_000:
        raise LeadError("Note is too long.")
    return add_activity(db, lead, "note", text, acting_user=acting_user)


def list_leads(
    db: Session,
    user: User,
    *,
    query: str | None = None,
    status: str | None = None,
    assignee_id: uuid.UUID | None = None,
    unassigned: bool = False,
    source: str | None = None,
    archived: bool = False,
    needs_review: bool | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[Lead], int]:
    stmt = visible_leads_query(user)
    stmt = stmt.where(Lead.archived_at.is_not(None) if archived else Lead.archived_at.is_(None))
    if status:
        stmt = stmt.where(Lead.status == status)
    if assignee_id is not None:
        stmt = stmt.where(Lead.assigned_to == assignee_id)
    if unassigned:
        stmt = stmt.where(Lead.assigned_to.is_(None))
    if source:
        stmt = stmt.where(Lead.source == source)
    if needs_review is not None:
        stmt = stmt.where(Lead.needs_review == needs_review)
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                Lead.name.ilike(like),
                Lead.email.ilike(like),
                Lead.phone.ilike(like),
                Lead.company.ilike(like),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    rows = (
        db.scalars(
            stmt.order_by(Lead.created_at.desc(), Lead.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .unique()
        .all()
    )
    return list(rows), int(total or 0)


def attention_queue(db: Session, user: User) -> dict[str, list[Lead]]:
    """Accessible, non-archived leads needing action."""
    base = visible_leads_query(user).where(Lead.archived_at.is_(None))
    now = utcnow()
    end_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    overdue = list(db.scalars(base.where(Lead.next_follow_up_at < now)).unique())
    due_today = list(
        db.scalars(
            base.where(Lead.next_follow_up_at >= now, Lead.next_follow_up_at < end_of_today)
        ).unique()
    )
    needs_review = list(db.scalars(base.where(Lead.needs_review.is_(True))).unique())
    unassigned: list[Lead] = []
    if can_manage_leads(user):
        unassigned = list(
            db.scalars(base.where(Lead.assigned_to.is_(None), Lead.status == "new")).unique()
        )
    return {
        "overdue": overdue,
        "due_today": due_today,
        "unassigned": unassigned,
        "needs_review": needs_review,
    }


def list_activities(db: Session, lead: Lead, limit: int = 200) -> list[LeadActivity]:
    return list(
        db.scalars(
            select(LeadActivity)
            .options(joinedload(LeadActivity.author))
            .where(LeadActivity.lead_id == lead.id)
            .order_by(LeadActivity.created_at.desc(), LeadActivity.id)
            .limit(limit)
        ).unique()
    )
