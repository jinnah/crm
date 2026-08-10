"""Jobs: one piece of work for one customer.

The lifecycle map below is the single authority on status transitions — API
handlers and UI code never invent their own rules. Jobs are archived, never
hard-deleted; RESTRICT foreign keys on documents, commercial records,
payments-via-invoices and appointments make erasing history impossible.
"""

import uuid
from datetime import datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    JOB_STATUSES,
    Appointment,
    CommercialDocument,
    EmailDelivery,
    Job,
    JobDocument,
    Lead,
    User,
    utcnow,
)
from app.services.leads import LeadError, add_activity, can_manage_leads
from app.services.messaging import lock_lead
from app.services.numbering import allocate_number

# Central lifecycle. Forward motion plus cancellation; completed and canceled
# are terminal. Issuing a quote auto-advances new → quoted and an accepted
# quote advances to approved — both go through change_status.
JOB_TRANSITIONS: dict[str, set[str]] = {
    "new": {"quoted", "approved", "scheduled", "in_progress", "canceled"},
    "quoted": {"approved", "scheduled", "in_progress", "canceled"},
    "approved": {"scheduled", "in_progress", "completed", "canceled"},
    "scheduled": {"in_progress", "completed", "canceled"},
    "in_progress": {"completed", "canceled"},
    "completed": set(),
    "canceled": set(),
}


class JobError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def visible_jobs_query(user: User) -> Select[tuple[Job]]:
    query = select(Job).options(joinedload(Job.lead), joinedload(Job.assignee))
    if not can_manage_leads(user):
        # Team members see jobs on their leads or assigned to them directly.
        query = query.join(Lead, Lead.id == Job.lead_id).where(
            or_(Job.assigned_to == user.id, Lead.assigned_to == user.id)
        )
    return query


def get_visible_job(db: Session, user: User, job_id: uuid.UUID) -> Job:
    job = db.scalar(visible_jobs_query(user).where(Job.id == job_id))
    if job is None:
        raise JobError("Job not found.", status_code=404)
    return job


def list_jobs(
    db: Session,
    user: User,
    *,
    query: str | None = None,
    status: str | None = None,
    assignee_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    archived: bool = False,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[Job], int]:
    base = visible_jobs_query(user)
    base = (
        base.where(Job.archived_at.isnot(None))
        if archived
        else base.where(Job.archived_at.is_(None))
    )
    if status:
        if status not in JOB_STATUSES:
            raise JobError("Unknown job status filter.")
        base = base.where(Job.status == status)
    if assignee_id is not None:
        base = base.where(Job.assigned_to == assignee_id)
    if lead_id is not None:
        base = base.where(Job.lead_id == lead_id)
    if query:
        term = f"%{query.strip()[:100]}%"
        base = base.join(Lead, Lead.id == Job.lead_id).where(
            or_(
                Job.job_number.ilike(term),
                Job.title.ilike(term),
                Job.service_type.ilike(term),
                Job.service_address.ilike(term),
                Lead.name.ilike(term),
                Lead.email.ilike(term),
                Lead.phone.ilike(term),
            )
        )
    total = db.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    rows = list(
        db.scalars(
            base.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).unique()
    )
    return rows, total


def create_job(
    db: Session,
    acting_user: User,
    lead: Lead,
    *,
    title: str,
    service_type: str = "",
    service_address: str = "",
    assigned_to: uuid.UUID | None = None,
    scheduled_for: datetime | None = None,
    internal_notes: str = "",
) -> Job:
    if lead.archived_at is not None:
        raise JobError("Restore this customer before creating a job.", status_code=409)
    if not can_manage_leads(acting_user) and lead.assigned_to != acting_user.id:
        raise JobError("You can only create jobs for your own customers.", status_code=403)
    if assigned_to is not None:
        staff = db.get(User, assigned_to)
        if staff is None or not staff.is_active:
            raise JobError("Jobs can only be assigned to an active user.")
    # Serialize on the lead so a burst of creations still allocates cleanly.
    lock_lead(db, lead.id)
    job = Job(
        job_number=allocate_number(db, "job", "J"),
        lead_id=lead.id,
        title=title.strip()[:200],
        service_type=service_type.strip()[:200],
        service_address=service_address.strip()[:300],
        assigned_to=assigned_to,
        scheduled_for=scheduled_for,
        internal_notes=internal_notes.strip()[:5000],
        created_by=acting_user.id,
    )
    db.add(job)
    db.flush()
    add_activity(
        db,
        lead,
        "job_created",
        f"Job {job.job_number} created: {job.title or job.service_type or 'untitled'}.",
        acting_user=acting_user,
        meta={"job_id": str(job.id), "job_number": job.job_number},
    )
    return job


def change_status(
    db: Session, acting_user: User, job: Job, new_status: str, *, note: str = ""
) -> Job:
    if new_status not in JOB_STATUSES:
        raise JobError("Unknown job status.")
    if job.archived_at is not None:
        raise JobError("Restore this job before changing its status.", status_code=409)
    if new_status == job.status:
        return job
    if new_status not in JOB_TRANSITIONS[job.status]:
        raise JobError(
            f"A {job.status.replace('_', ' ')} job cannot become {new_status.replace('_', ' ')}.",
            status_code=409,
        )
    previous = job.status
    job.status = new_status
    now = utcnow()
    if new_status == "in_progress" and job.started_at is None:
        job.started_at = now
    if new_status == "completed":
        job.completed_at = now
    db.flush()
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    content = (
        f"Job {job.job_number}: {previous.replace('_', ' ')} → {new_status.replace('_', ' ')}."
    )
    if note.strip():
        content += f" {note.strip()[:300]}"
    add_activity(
        db,
        lead,
        "job_status_change",
        content,
        acting_user=acting_user,
        meta={"job_id": str(job.id), "from": previous, "to": new_status},
    )
    return job


def auto_advance(db: Session, job: Job, new_status: str) -> None:
    """System-driven forward motion (quote issued/accepted). Silently skips
    transitions the central map forbids instead of failing the caller."""
    if job.status != new_status and new_status in JOB_TRANSITIONS.get(job.status, set()):
        previous = job.status
        job.status = new_status
        db.flush()
        lead = db.get(Lead, job.lead_id)
        if lead is not None:
            add_activity(
                db,
                lead,
                "job_status_change",
                f"Job {job.job_number}: {previous.replace('_', ' ')} → "
                f"{new_status.replace('_', ' ')} (automatic).",
                meta={"job_id": str(job.id), "from": previous, "to": new_status},
            )


def update_job(
    db: Session,
    acting_user: User,
    job: Job,
    *,
    title: str | None = None,
    service_type: str | None = None,
    service_address: str | None = None,
    assigned_to: uuid.UUID | None = None,
    clear_assignee: bool = False,
    scheduled_for: datetime | None = None,
    clear_scheduled_for: bool = False,
    internal_notes: str | None = None,
) -> Job:
    if job.archived_at is not None:
        raise JobError("Restore this job before editing it.", status_code=409)
    if title is not None:
        job.title = title.strip()[:200]
    if service_type is not None:
        job.service_type = service_type.strip()[:200]
    if service_address is not None:
        job.service_address = service_address.strip()[:300]
    if clear_assignee:
        job.assigned_to = None
    elif assigned_to is not None:
        staff = db.get(User, assigned_to)
        if staff is None or not staff.is_active:
            raise JobError("Jobs can only be assigned to an active user.")
        job.assigned_to = assigned_to
    if clear_scheduled_for:
        job.scheduled_for = None
    elif scheduled_for is not None:
        job.scheduled_for = scheduled_for
    if internal_notes is not None:
        job.internal_notes = internal_notes.strip()[:5000]
    db.flush()
    return job


def _job_has_history(db: Session, job: Job) -> bool:
    has_document = (
        db.scalar(select(JobDocument.id).where(JobDocument.job_id == job.id).limit(1)) is not None
    )
    has_commercial = (
        db.scalar(select(CommercialDocument.id).where(CommercialDocument.job_id == job.id).limit(1))
        is not None
    )
    has_appointment = (
        db.scalar(select(Appointment.id).where(Appointment.job_id == job.id).limit(1)) is not None
    )
    has_email = (
        db.scalar(select(EmailDelivery.id).where(EmailDelivery.job_id == job.id).limit(1))
        is not None
    )
    return has_document or has_commercial or has_appointment or has_email


def archive_job(db: Session, acting_user: User, job: Job) -> Job:
    if job.archived_at is not None:
        return job
    job.archived_at = utcnow()
    db.flush()
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    add_activity(
        db,
        lead,
        "job_archived",
        f"Job {job.job_number} archived.",
        acting_user=acting_user,
        meta={"job_id": str(job.id)},
    )
    return job


def restore_job(db: Session, acting_user: User, job: Job) -> Job:
    if job.archived_at is None:
        return job
    lead = db.get(Lead, job.lead_id)
    assert lead is not None
    if lead.archived_at is not None:
        raise JobError("Restore the customer before restoring this job.", status_code=409)
    job.archived_at = None
    db.flush()
    add_activity(
        db,
        lead,
        "job_restored",
        f"Job {job.job_number} restored.",
        acting_user=acting_user,
        meta={"job_id": str(job.id)},
    )
    return job


def assert_never_deleted(db: Session, job: Job) -> None:
    """Jobs are archived, never hard-deleted; anything with history is also
    protected by RESTRICT constraints. This exists for explicit API refusals."""
    if _job_has_history(db, job):
        raise JobError(
            "This job has documents, commercial records, appointments or email history "
            "and can only be archived.",
            status_code=409,
        )
    raise JobError("Jobs are archived, never deleted.", status_code=405)


def link_appointment(
    db: Session, acting_user: User, job: Job, appointment: Appointment
) -> Appointment:
    """Deliberate association of an existing appointment with a job. The
    appointment must belong to the same customer; scheduling rules and the
    appointment revision are untouched."""
    if appointment.lead_id != job.lead_id:
        raise JobError(
            "An appointment can only be linked to a job for the same customer.",
            status_code=409,
        )
    if not can_manage_leads(acting_user):
        lead = db.get(Lead, job.lead_id)
        if lead is None or (
            lead.assigned_to != acting_user.id and job.assigned_to != acting_user.id
        ):
            raise LeadError("Lead not found.", status_code=404)
    appointment.job_id = job.id
    db.flush()
    return appointment
