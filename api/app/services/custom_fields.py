import re
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import CUSTOM_FIELD_TYPES, CustomFieldDefinition, Lead, LeadCustomValue, User
from app.services.leads import LeadError

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,49}$")


def _require_owner(acting_user: User) -> None:
    if acting_user.role != "owner":
        raise LeadError("You are not allowed to manage custom fields.", status_code=403)


def list_definitions(db: Session, include_inactive: bool = False) -> list[CustomFieldDefinition]:
    stmt = select(CustomFieldDefinition).order_by(
        CustomFieldDefinition.display_order, CustomFieldDefinition.created_at
    )
    if not include_inactive:
        stmt = stmt.where(CustomFieldDefinition.is_active.is_(True))
    return list(db.scalars(stmt))


def _clean_options(type_: str, options: list[str] | None) -> list[str] | None:
    if type_ != "select":
        return None
    cleaned = [option.strip() for option in options or [] if option and option.strip()]
    if not cleaned:
        raise LeadError("Select fields need at least one option.")
    if len(cleaned) != len(set(cleaned)):
        raise LeadError("Select options must be unique.")
    return cleaned


def create_definition(
    db: Session,
    acting_user: User,
    *,
    key: str,
    label: str,
    type_: str,
    options: list[str] | None = None,
    required: bool = False,
    display_order: int = 0,
) -> CustomFieldDefinition:
    _require_owner(acting_user)
    key = key.strip().lower()
    if not _KEY_PATTERN.match(key):
        raise LeadError(
            "Key must start with a letter and use only lowercase letters, digits and underscores."
        )
    if type_ not in CUSTOM_FIELD_TYPES:
        raise LeadError("Invalid field type.")
    if not label.strip():
        raise LeadError("Label is required.")
    if db.scalar(select(CustomFieldDefinition).where(CustomFieldDefinition.key == key)):
        raise LeadError("A field with this key already exists.", status_code=409)
    definition = CustomFieldDefinition(
        key=key,
        label=label.strip(),
        type=type_,
        options=_clean_options(type_, options),
        required=required,
        display_order=display_order,
    )
    db.add(definition)
    db.flush()
    return definition


def update_definition(
    db: Session,
    acting_user: User,
    definition: CustomFieldDefinition,
    changes: dict[str, Any],
) -> CustomFieldDefinition:
    """Label, options, required, active state and order are editable; the key
    and type are immutable so stored values stay meaningful."""
    _require_owner(acting_user)
    if not set(changes) <= {"label", "options", "required", "is_active", "display_order"}:
        raise LeadError("Only label, options, required, is_active and display_order can change.")
    if "label" in changes:
        if not str(changes["label"]).strip():
            raise LeadError("Label is required.")
        definition.label = str(changes["label"]).strip()
    if "options" in changes:
        if definition.type != "select":
            raise LeadError("Only select fields have options.")
        definition.options = _clean_options("select", changes["options"])
    if "required" in changes:
        definition.required = bool(changes["required"])
    if "is_active" in changes:
        definition.is_active = bool(changes["is_active"])
    if "display_order" in changes:
        definition.display_order = int(changes["display_order"])
    db.flush()
    return definition


def _validate_value(definition: CustomFieldDefinition, value: Any) -> Any:
    """Normalize and validate one custom value; None clears the value."""
    if value is None or value == "":
        return None
    if definition.type == "text":
        if not isinstance(value, str) or len(value) > 2000:
            raise LeadError(f"{definition.label} must be text of at most 2000 characters.")
        return value
    if definition.type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise LeadError(f"{definition.label} must be a number.")
        return value
    if definition.type == "date":
        if not isinstance(value, str):
            raise LeadError(f"{definition.label} must be a date (YYYY-MM-DD).")
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise LeadError(f"{definition.label} must be a date (YYYY-MM-DD).") from error
        return value
    if definition.type == "boolean":
        if not isinstance(value, bool):
            raise LeadError(f"{definition.label} must be true or false.")
        return value
    if definition.type == "select":
        if value not in (definition.options or []):
            raise LeadError(f"{definition.label} must be one of its configured options.")
        return value
    raise LeadError("Invalid field type.")


def apply_custom_values(
    db: Session,
    lead: Lead,
    values: dict[str, Any],
    *,
    enforce_required: bool = False,
) -> None:
    """Validate and upsert custom values keyed by definition key. Inactive
    definitions are rejected for new writes; existing stored data is kept."""
    definitions = {definition.key: definition for definition in list_definitions(db)}
    unknown = set(values) - set(definitions)
    if unknown:
        raise LeadError("Unknown custom fields: " + ", ".join(sorted(unknown)))

    existing = {
        row.field_id: row
        for row in db.scalars(select(LeadCustomValue).where(LeadCustomValue.lead_id == lead.id))
    }
    for key, raw in values.items():
        definition = definitions[key]
        value = _validate_value(definition, raw)
        row = existing.get(definition.id)
        if value is None:
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            db.add(LeadCustomValue(lead_id=lead.id, field_id=definition.id, value=value))
        else:
            row.value = value

    if enforce_required:
        db.flush()
        stored = current_values(db, lead)
        for definition in definitions.values():
            if definition.required and stored.get(definition.key) in (None, ""):
                raise LeadError(f"{definition.label} is required.")
    db.flush()


def current_values(db: Session, lead: Lead) -> dict[str, Any]:
    rows = db.scalars(
        select(LeadCustomValue)
        .options(joinedload(LeadCustomValue.field))
        .where(LeadCustomValue.lead_id == lead.id)
    ).unique()
    return {row.field.key: row.value for row in rows}


def values_for_leads(db: Session, lead_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    if not lead_ids:
        return {}
    rows = db.scalars(
        select(LeadCustomValue)
        .options(joinedload(LeadCustomValue.field))
        .where(LeadCustomValue.lead_id.in_(lead_ids))
    ).unique()
    result: dict[uuid.UUID, dict[str, Any]] = {}
    for row in rows:
        result.setdefault(row.lead_id, {})[row.field.key] = row.value
    return result
