import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import (
    DbDep,
    FullyAuthedUserDep,
    SettingsDep,
    check_csrf,
    check_origin,
    get_sms_sender,
)
from app.api.v1.schemas import (
    ActivityOut,
    AssignableUserOut,
    AssignLeadRequest,
    AttentionQueueOut,
    CreateLeadRequest,
    LeadListOut,
    LeadOut,
    NoteRequest,
    OutboundMessageOut,
    SendMessageRequest,
    UpdateLeadRequest,
)
from app.models import Lead, LeadActivity, OutboundMessage, User, utcnow
from app.services import custom_fields as custom_field_service
from app.services import leads as lead_service
from app.services import messaging
from app.services.leads import LeadError

router = APIRouter(
    prefix="/leads",
    tags=["leads"],
    dependencies=[Depends(check_origin), Depends(check_csrf)],
)


def _http(error: LeadError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


def serialize_lead(db: Session, lead: Lead, values: dict | None = None) -> LeadOut:
    if values is None:
        values = custom_field_service.current_values(db, lead)
    assignee_email = None
    if lead.assigned_to is not None:
        assignee = db.get(User, lead.assigned_to)
        assignee_email = assignee.email if assignee is not None else None
    return LeadOut(
        id=lead.id,
        name=lead.name,
        email=lead.email,
        phone=lead.phone,
        company=lead.company,
        status=lead.status,
        source=lead.source,
        assigned_to=lead.assigned_to,
        assignee_email=assignee_email,
        next_follow_up_at=lead.next_follow_up_at,
        last_contacted_at=lead.last_contacted_at,
        needs_review=lead.needs_review,
        archived_at=lead.archived_at,
        first_inbound_at=lead.first_inbound_at,
        response_due_at=lead.response_due_at,
        first_response_at=lead.first_response_at,
        first_response_seconds=lead.first_response_seconds,
        response_target_met=lead.response_target_met,
        response_overdue=(
            lead.first_response_at is None
            and lead.response_due_at is not None
            and lead.response_due_at < utcnow()
        ),
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        custom_values=values,
    )


def _serialize_many(db: Session, leads: list[Lead]) -> list[LeadOut]:
    values = custom_field_service.values_for_leads(db, [lead.id for lead in leads])
    return [serialize_lead(db, lead, values.get(lead.id, {})) for lead in leads]


@router.get("", response_model=LeadListOut)
def list_leads(
    user: FullyAuthedUserDep,
    db: DbDep,
    query: Annotated[str | None, Query(max_length=200)] = None,
    status: str | None = None,
    assignee_id: uuid.UUID | None = None,
    unassigned: bool = False,
    source: str | None = None,
    archived: bool = False,
    needs_review: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> LeadListOut:
    leads, total = lead_service.list_leads(
        db,
        user,
        query=query,
        status=status,
        assignee_id=assignee_id,
        unassigned=unassigned,
        source=source,
        archived=archived,
        needs_review=needs_review,
        page=page,
        page_size=page_size,
    )
    return LeadListOut(
        items=_serialize_many(db, leads), total=total, page=page, page_size=page_size
    )


@router.get("/attention", response_model=AttentionQueueOut)
def attention_queue(user: FullyAuthedUserDep, db: DbDep) -> AttentionQueueOut:
    groups = lead_service.attention_queue(db, user)
    return AttentionQueueOut(
        overdue=_serialize_many(db, groups["overdue"]),
        due_today=_serialize_many(db, groups["due_today"]),
        unassigned=_serialize_many(db, groups["unassigned"]),
        needs_review=_serialize_many(db, groups["needs_review"]),
        unresponded=_serialize_many(db, groups["unresponded"]),
    )


@router.get("/assignable-users", response_model=list[AssignableUserOut])
def assignable_users(user: FullyAuthedUserDep, db: DbDep) -> list[AssignableUserOut]:
    if not lead_service.can_manage_leads(user):
        raise HTTPException(status_code=403, detail="You are not allowed to assign leads.")
    rows = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.email))
    return [AssignableUserOut.model_validate(row) for row in rows]


@router.post("", response_model=LeadOut, status_code=201)
def create_lead(body: CreateLeadRequest, user: FullyAuthedUserDep, db: DbDep) -> LeadOut:
    try:
        lead = lead_service.create_lead(
            db,
            user,
            name=body.name,
            email=body.email,
            phone=body.phone,
            company=body.company,
            status=body.status,
            source=body.source,
            assigned_to=body.assigned_to,
            next_follow_up_at=body.next_follow_up_at,
        )
        custom_field_service.apply_custom_values(
            db, lead, body.custom_values, enforce_required=True
        )
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    return serialize_lead(db, lead)


@router.get("/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> LeadOut:
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
    except LeadError as error:
        raise _http(error) from error
    return serialize_lead(db, lead)


@router.patch("/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: uuid.UUID, body: UpdateLeadRequest, user: FullyAuthedUserDep, db: DbDep
) -> LeadOut:
    changes = body.model_dump(exclude_unset=True)
    custom_values = changes.pop("custom_values", None)
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
        if changes:
            lead_service.update_lead_fields(db, user, lead, changes)
        if custom_values is not None:
            if lead.archived_at is not None:
                raise LeadError("Archived leads must be restored before editing.", 409)
            custom_field_service.apply_custom_values(db, lead, custom_values)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    return serialize_lead(db, lead)


@router.post("/{lead_id}/assign", response_model=LeadOut)
def assign_lead(
    lead_id: uuid.UUID, body: AssignLeadRequest, user: FullyAuthedUserDep, db: DbDep
) -> LeadOut:
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
        lead_service.assign_lead(db, user, lead, body.user_id)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    return serialize_lead(db, lead)


@router.post("/{lead_id}/archive", response_model=LeadOut)
def archive_lead(lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> LeadOut:
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
        lead_service.archive_lead(db, user, lead)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    return serialize_lead(db, lead)


@router.post("/{lead_id}/restore", response_model=LeadOut)
def restore_lead(lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> LeadOut:
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
        lead_service.restore_lead(db, user, lead)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    return serialize_lead(db, lead)


@router.post("/{lead_id}/complete-follow-up", response_model=LeadOut)
def complete_follow_up(lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> LeadOut:
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
        lead_service.complete_follow_up(db, user, lead)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    return serialize_lead(db, lead)


@router.get("/{lead_id}/activities", response_model=list[ActivityOut])
def list_activities(lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> list[ActivityOut]:
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
    except LeadError as error:
        raise _http(error) from error
    return [_serialize_activity(activity) for activity in lead_service.list_activities(db, lead)]


@router.post("/{lead_id}/notes", response_model=ActivityOut, status_code=201)
def add_note(
    lead_id: uuid.UUID, body: NoteRequest, user: FullyAuthedUserDep, db: DbDep
) -> ActivityOut:
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
        activity = lead_service.add_note(db, user, lead, body.content)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    activity.author = user
    return _serialize_activity(activity)


def _serialize_activity(activity: LeadActivity) -> ActivityOut:
    return ActivityOut(
        id=activity.id,
        type=activity.type,
        channel=activity.channel,
        direction=activity.direction,
        content=activity.content,
        created_by_email=activity.author.email if activity.author is not None else None,
        provider=activity.provider,
        external_event_id=activity.external_event_id,
        occurred_at=activity.occurred_at,
        meta=activity.meta,
        created_at=activity.created_at,
    )


@router.get("/{lead_id}/messages", response_model=list[OutboundMessageOut])
def list_messages(
    lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep
) -> list[OutboundMessageOut]:
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
    except LeadError as error:
        raise _http(error) from error
    rows = db.scalars(
        select(OutboundMessage)
        .where(OutboundMessage.lead_id == lead.id)
        .order_by(OutboundMessage.created_at)
    )
    return [_serialize_message(db, row) for row in rows]


@router.post("/{lead_id}/messages", response_model=OutboundMessageOut, status_code=201)
def send_message(
    lead_id: uuid.UUID,
    body: SendMessageRequest,
    request: Request,
    user: FullyAuthedUserDep,
    db: DbDep,
    settings: SettingsDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> OutboundMessageOut:
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
        message = messaging.send_lead_sms(
            db, user, lead, body.body, idempotency_key, settings, get_sms_sender(request)
        )
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    return _serialize_message(db, message)


@router.post("/{lead_id}/mark-contacted", response_model=LeadOut)
def mark_contacted(lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> LeadOut:
    """Deliberate record of a response that happened off-platform."""
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
        messaging.mark_contacted_outside_crm(db, user, lead)
    except LeadError as error:
        db.rollback()
        raise _http(error) from error
    db.commit()
    return serialize_lead(db, lead)


def _serialize_message(db: Session, message: OutboundMessage) -> OutboundMessageOut:
    author_email = None
    if message.created_by is not None:
        author = db.get(User, message.created_by)
        author_email = author.email if author is not None else None
    return OutboundMessageOut(
        id=message.id,
        lead_id=message.lead_id,
        purpose=message.purpose,
        to_phone=message.to_phone,
        body=message.body,
        status=message.status,
        provider_sid=message.provider_sid,
        error_message=message.error_message,
        created_by_email=author_email,
        created_at=message.created_at,
        submitted_at=message.submitted_at,
        delivered_at=message.delivered_at,
        failed_at=message.failed_at,
    )
