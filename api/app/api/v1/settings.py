from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.v1.deps import (
    DbDep,
    FullyAuthedUserDep,
    check_csrf,
    check_origin,
    get_branding_limiter,
)
from app.api.v1.schemas import (
    BrandingOut,
    CommunicationSettingsOut,
    PublicFormInfoOut,
    SchedulingBasicsOut,
    SchedulingSettingsOut,
    UpdateCommunicationSettingsRequest,
    UpdateSchedulingSettingsRequest,
)
from app.services import branding, messaging, scheduling
from app.services.leads import LeadError
from app.services.rate_limit import RateLimiter

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


# --- Branding ------------------------------------------------------------
#
# The upload is sent as a raw image body, not multipart: there is no form to
# parse, so no multipart parser ever sees the bytes and nothing but the image
# itself can be smuggled in. The 1 MiB ceiling is enforced ahead of this by
# BodyLimitMiddleware on the /api/v1/settings/branding prefix.

LimiterDep = Annotated[RateLimiter, Depends(get_branding_limiter)]


def _branding_out(row) -> BrandingOut:
    return BrandingOut(
        business_name=row.business_name,
        has_logo=row.logo_bytes is not None,
        width=row.logo_width,
        height=row.logo_height,
        updated_at=row.logo_updated_at,
        initials=branding.initials(row.business_name),
    )


@router.get("/branding", response_model=BrandingOut, dependencies=[Depends(check_origin)])
def get_branding(user: FullyAuthedUserDep, db: DbDep) -> BrandingOut:
    """Metadata for the branding editor. Any signed-in user may read it — the
    logo is shown in the shell they already see."""
    row = messaging.get_settings_row(db)
    db.commit()
    return _branding_out(row)


@router.post(
    "/branding/logo",
    response_model=BrandingOut,
    dependencies=[Depends(check_origin), Depends(check_csrf)],
)
async def upload_logo(
    request: Request, user: FullyAuthedUserDep, db: DbDep, limiter: LimiterDep
) -> BrandingOut:
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner may change the logo.")
    key = f"branding:{user.id}"
    if not limiter.allowed(key):
        raise HTTPException(
            status_code=429, detail="Too many logo changes. Try again in a few minutes."
        )
    limiter.record(key)

    raw = await request.body()
    row = messaging.get_settings_row(db)
    try:
        branding.set_logo(db, row, raw)
    except LeadError as error:
        db.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.message) from error
    db.commit()
    return _branding_out(row)


@router.delete(
    "/branding/logo",
    response_model=BrandingOut,
    dependencies=[Depends(check_origin), Depends(check_csrf)],
)
def remove_logo(user: FullyAuthedUserDep, db: DbDep) -> BrandingOut:
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner may change the logo.")
    row = messaging.get_settings_row(db)
    branding.clear_logo(db, row)
    db.commit()
    return _branding_out(row)


@public_router.get("/logo")
def public_logo(request: Request, db: DbDep) -> Response:
    """The stored logo, on a stable unauthenticated route.

    Both the CRM shell and the customer-facing pages point at this, so the
    image is cached once and no private setting travels with it.
    """
    row = messaging.get_settings_row(db)
    db.commit()
    if row.logo_bytes is None or row.logo_digest is None:
        raise HTTPException(status_code=404, detail="No logo has been set.")

    etag = f'"{row.logo_digest}"'
    headers = {
        "ETag": etag,
        # The digest changes whenever the image does, so a short max-age plus
        # revalidation keeps a replaced logo from lingering in a browser.
        "Cache-Control": "public, max-age=300, must-revalidate",
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        content=row.logo_bytes,
        media_type=row.logo_mime or branding.STORED_MIME,
        headers=headers,
    )


@public_router.get("/branding", response_model=BrandingOut)
def public_branding(db: DbDep) -> BrandingOut:
    """What a customer-facing page needs to render the header: the business
    name, whether a logo exists, and the fallback initials."""
    row = messaging.get_settings_row(db)
    db.commit()
    return _branding_out(row)
