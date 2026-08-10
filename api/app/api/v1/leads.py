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
    AttentionVoiceCallOut,
    CreateLeadRequest,
    LeadListOut,
    LeadOut,
    NoteRequest,
    OutboundMessageOut,
    SendMessageRequest,
    UpdateLeadRequest,
    VoiceCallOut,
)
from app.models import Lead, LeadActivity, OutboundMessage, User, VoiceCall, utcnow
from app.services import custom_fields as custom_field_service
from app.services import leads as lead_service
from app.services import messaging
from app.services.leads import LeadError
from app.services.voice import call_attention_reason

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
        voice_calls=_voice_attention(db, user),
        **_appointment_attention(db, user),
    )


def _appointment_attention(db: Session, user: User) -> dict:
    """Scheduling states that need action, shaped for the attention queue."""
    from app.api.v1.appointments import appointment_attention
    from app.api.v1.schemas import AttentionAppointmentOut
    from app.models import Appointment

    settings_row = messaging.get_settings_row(db)
    groups = appointment_attention(db, user, settings_row)

    def shape(appointment, detail=None):
        lead = db.get(Lead, appointment.lead_id)
        return AttentionAppointmentOut(
            id=appointment.id,
            lead_id=appointment.lead_id,
            lead_name=(lead.name or lead.email or lead.phone) if lead is not None else None,
            subject=appointment.subject,
            start_at=appointment.start_at,
            timezone=appointment.timezone,
            status=appointment.status,
            detail=detail,
        )

    def shape_notification(notification):
        appointment = db.get(Appointment, notification.appointment_id)
        if appointment is None:
            return None
        return shape(
            appointment,
            f"{notification.type} message {notification.state}"
            + (f": {notification.failure_message}" if notification.failure_message else ""),
        )

    failed = [shape_notification(row) for row in groups["failed_notifications"]]
    unknown = [shape_notification(row) for row in groups["unknown_notifications"]]
    return {
        "appointments_overdue": [shape(item, "Needs a disposition") for item in groups["overdue"]],
        "appointments_upcoming": [shape(item) for item in groups["upcoming"]],
        "appointment_messages_failed": [item for item in failed if item is not None],
        "appointment_messages_unknown": [item for item in unknown if item is not None],
    }


def _voice_attention(db: Session, user: User) -> list:
    """Voice calls that still need a person, with one concise reason each.

    An item stays visible until the underlying condition clears: a follow-up
    or urgent call until the lead gets its first human response, a conflict
    or ambiguity until review is cleared, a missing number until one is
    recorded, and messaging problems as long as the state stands.
    """
    query = (
        select(VoiceCall)
        .join(Lead, Lead.id == VoiceCall.lead_id)
        .where(Lead.archived_at.is_(None))
        .order_by(VoiceCall.created_at.desc())
        .limit(200)
    )
    if not lead_service.can_manage_leads(user):
        query = query.where(Lead.assigned_to == user.id)

    items = []
    for call in db.scalars(query):
        reason = call_attention_reason(call)
        if reason is None:
            continue
        lead = db.get(Lead, call.lead_id)
        if lead is None:
            continue
        needs_person = (
            call.completion_conflict
            or (call.transfer_outcome == "failed" and lead.first_response_at is None)
            or (call.urgency == "urgent" and lead.first_response_at is None)
            or (call.requires_human_follow_up and lead.first_response_at is None)
            or (not call.caller_phone and lead.email is None and lead.phone is None)
            or call.ack_state in ("failed", "unknown")
            or call.alert_state in ("failed", "unknown", "no_destination")
        )
        if not needs_person:
            continue
        items.append(
            AttentionVoiceCallOut(
                id=call.id,
                lead_id=call.lead_id,
                lead_name=(lead.name or lead.email or lead.phone),
                reason=reason,
                summary=call.summary[:200],
                occurred_at=call.started_at or call.created_at,
            )
        )
    return items


@router.get("/{lead_id}/voice-calls", response_model=list[VoiceCallOut])
def lead_voice_calls(lead_id: uuid.UUID, user: FullyAuthedUserDep, db: DbDep) -> list[VoiceCallOut]:
    """Call history for one lead, newest first, following lead access rules.
    Never includes transcript text — the summary is the timeline surface."""
    try:
        lead = lead_service.get_visible_lead(db, user, lead_id)
    except LeadError as error:
        raise _http(error) from error
    rows = db.scalars(
        select(VoiceCall)
        .where(VoiceCall.lead_id == lead.id)
        .order_by(VoiceCall.created_at.desc())
        .limit(50)
    )
    return [VoiceCallOut.model_validate(row) for row in rows]


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
