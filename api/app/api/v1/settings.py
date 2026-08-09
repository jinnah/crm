from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.deps import DbDep, FullyAuthedUserDep, check_csrf, check_origin
from app.api.v1.schemas import (
    CommunicationSettingsOut,
    PublicFormInfoOut,
    SchedulingBasicsOut,
    SchedulingSettingsOut,
    UpdateCommunicationSettingsRequest,
    UpdateSchedulingSettingsRequest,
)
from app.services import messaging, scheduling
from app.services.leads import LeadError

router = APIRouter(prefix="/settings", tags=["settings"])

# Public, unauthenticated: only the safe presentation fields for the request
# form. No phone numbers, templates or operational configuration.
public_router = APIRouter(prefix="/public", tags=["public"])


@public_router.get("/form-info", response_model=PublicFormInfoOut)
def public_form_info(db: DbDep) -> PublicFormInfoOut:
    row = messaging.get_settings_row(db)
    db.commit()
    return PublicFormInfoOut(
        form_title=row.form_title, form_intro=row.form_intro, business_name=row.business_name
    )


@router.get(
    "/communication",
    response_model=CommunicationSettingsOut,
    dependencies=[Depends(check_origin)],
)
def get_communication_settings(user: FullyAuthedUserDep, db: DbDep) -> CommunicationSettingsOut:
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="You are not allowed to view these settings.")
    row = messaging.get_settings_row(db)
    db.commit()
    return CommunicationSettingsOut.model_validate(row)


@router.patch(
    "/communication",
    response_model=CommunicationSettingsOut,
    dependencies=[Depends(check_origin), Depends(check_csrf)],
)
def update_communication_settings(
    body: UpdateCommunicationSettingsRequest, user: FullyAuthedUserDep, db: DbDep
) -> CommunicationSettingsOut:
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="You are not allowed to change these settings.")
    row = messaging.get_settings_row(db)
    changes = body.model_dump(exclude_unset=True)
    try:
        for field in ("acknowledgment_template", "alert_template"):
            if field in changes:
                changes[field] = messaging.validate_template(changes[field])
        if "alert_destination_phone" in changes:
            changes["alert_destination_phone"] = messaging.validate_phone(
                changes["alert_destination_phone"]
            )
        if changes.get("alert_enabled") and not (
            changes.get("alert_destination_phone", row.alert_destination_phone)
        ):
            raise LeadError("A notification destination phone number is required.")
        for field, value in changes.items():
            setattr(row, field, value)
    except LeadError as error:
        db.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.message) from error
    db.commit()
    return CommunicationSettingsOut.model_validate(row)


@router.get(
    "/scheduling-basics",
    response_model=SchedulingBasicsOut,
    dependencies=[Depends(check_origin)],
)
def get_scheduling_basics(user: FullyAuthedUserDep, db: DbDep) -> SchedulingBasicsOut:
    """Time zone and duration defaults, readable by anyone who can schedule."""
    row = messaging.get_settings_row(db)
    if row.business_hours is None:
        row.business_hours = dict(scheduling.DEFAULT_BUSINESS_HOURS)
    db.commit()
    return SchedulingBasicsOut.model_validate(row)


@router.get(
    "/scheduling",
    response_model=SchedulingSettingsOut,
    dependencies=[Depends(check_origin)],
)
def get_scheduling_settings(user: FullyAuthedUserDep, db: DbDep) -> SchedulingSettingsOut:
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="You are not allowed to view these settings.")
    row = messaging.get_settings_row(db)
    if row.business_hours is None:
        row.business_hours = dict(scheduling.DEFAULT_BUSINESS_HOURS)
    db.commit()
    return SchedulingSettingsOut.model_validate(row)


@router.patch(
    "/scheduling",
    response_model=SchedulingSettingsOut,
    dependencies=[Depends(check_origin), Depends(check_csrf)],
)
def update_scheduling_settings(
    body: UpdateSchedulingSettingsRequest, user: FullyAuthedUserDep, db: DbDep
) -> SchedulingSettingsOut:
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="You are not allowed to change these settings.")
    row = messaging.get_settings_row(db)
    changes = body.model_dump(exclude_unset=True)
    try:
        if "business_timezone" in changes:
            changes["business_timezone"] = scheduling.validate_timezone(
                changes["business_timezone"]
            )
        if "business_hours" in changes:
            changes["business_hours"] = scheduling.validate_business_hours(
                changes["business_hours"]
            )
        for field in (
            "confirmation_template",
            "reminder_template",
            "appointment_canceled_template",
            "appointment_rescheduled_template",
        ):
            if field in changes:
                changes[field] = messaging.validate_appointment_template(changes[field])
        for field, value in changes.items():
            setattr(row, field, value)
    except LeadError as error:
        db.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.message) from error
    db.commit()
    return SchedulingSettingsOut.model_validate(row)
