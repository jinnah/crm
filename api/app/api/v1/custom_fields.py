import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.deps import DbDep, FullyAuthedUserDep, check_csrf, check_origin
from app.api.v1.schemas import (
    CreateCustomFieldRequest,
    CustomFieldOut,
    UpdateCustomFieldRequest,
)
from app.models import CustomFieldDefinition
from app.services import custom_fields as service
from app.services.leads import LeadError

router = APIRouter(
    prefix="/custom-fields",
    tags=["custom-fields"],
    dependencies=[Depends(check_origin), Depends(check_csrf)],
)


@router.get("", response_model=list[CustomFieldOut])
def list_custom_fields(
    user: FullyAuthedUserDep, db: DbDep, include_inactive: bool = False
) -> list[CustomFieldOut]:
    # Everyone needs active definitions to render forms; the full list
    # (including inactive) is an owner concern.
    if include_inactive and user.role != "owner":
        raise HTTPException(status_code=403, detail="You are not allowed to manage custom fields.")
    definitions = service.list_definitions(db, include_inactive=include_inactive)
    return [CustomFieldOut.model_validate(definition) for definition in definitions]


@router.post("", response_model=CustomFieldOut, status_code=201)
def create_custom_field(
    body: CreateCustomFieldRequest, user: FullyAuthedUserDep, db: DbDep
) -> CustomFieldOut:
    try:
        definition = service.create_definition(
            db,
            user,
            key=body.key,
            label=body.label,
            type_=body.type,
            options=body.options,
            required=body.required,
            display_order=body.display_order,
        )
    except LeadError as error:
        db.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.message) from error
    db.commit()
    return CustomFieldOut.model_validate(definition)


@router.patch("/{field_id}", response_model=CustomFieldOut)
def update_custom_field(
    field_id: uuid.UUID, body: UpdateCustomFieldRequest, user: FullyAuthedUserDep, db: DbDep
) -> CustomFieldOut:
    definition = db.get(CustomFieldDefinition, field_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Custom field not found.")
    try:
        service.update_definition(db, user, definition, body.model_dump(exclude_unset=True))
    except LeadError as error:
        db.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.message) from error
    db.commit()
    return CustomFieldOut.model_validate(definition)
