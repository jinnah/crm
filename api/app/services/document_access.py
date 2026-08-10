"""Customer capabilities for document access.

A capability grants access to exactly ONE immutable document version and,
for quotes, the response action — never another document, job or customer.
Only the keyed digest is stored; the raw value travels in the customer's
link and onward in request bodies through the BFF, never in a FastAPI path,
so no access log ever records it.
"""

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    CommercialDocument,
    CommunicationSettings,
    DocumentCapability,
    User,
    utcnow,
)
from app.security.tokens import digest_token, generate_token


class AccessError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def issue_capability(
    db: Session,
    settings: Settings,
    settings_row: CommunicationSettings,
    version_id: uuid.UUID,
    *,
    purpose: str,
    created_by: User | None = None,
) -> tuple[DocumentCapability, str]:
    """Create a capability, returning it with the raw token (never stored)."""
    if purpose not in ("view", "quote_response"):
        raise AccessError("Unknown capability purpose.")
    days = max(1, min(365, settings_row.secure_link_expiry_days))
    raw = generate_token()
    capability = DocumentCapability(
        version_id=version_id,
        purpose=purpose,
        token_digest=digest_token(raw, settings.session_token_pepper),
        expires_at=utcnow() + timedelta(days=days),
        created_by=created_by.id if created_by else None,
    )
    db.add(capability)
    db.flush()
    return capability, raw


def resolve_capability(db: Session, raw_token: str, settings: Settings) -> DocumentCapability:
    """Reject unknown, expired or revoked capabilities. The messages stay
    deliberately vague — a customer needs no more, an attacker even less."""
    digest = digest_token(raw_token, settings.session_token_pepper)
    capability = db.scalar(
        select(DocumentCapability).where(DocumentCapability.token_digest == digest)
    )
    if capability is None:
        raise AccessError("This document link is not valid.", status_code=404)
    if capability.revoked_at is not None:
        raise AccessError("This document link has been withdrawn.", status_code=410)
    if capability.expires_at <= utcnow():
        raise AccessError("This document link has expired.", status_code=410)
    return capability


def mark_viewed(db: Session, capability: DocumentCapability) -> None:
    """Record first successful access; flips quote/invoice sent → viewed.
    Viewing never implies acceptance."""
    now = utcnow()
    capability.last_used_at = now
    if capability.first_viewed_at is None:
        capability.first_viewed_at = now
        document = db.get(CommercialDocument, capability.version.document_id)
        if document is not None and document.status == "sent":
            document.status = "viewed"
    db.flush()


def revoke_for_document(db: Session, document_id: uuid.UUID) -> int:
    """Revoke every live capability for any version of one document."""
    from app.models import CommercialDocumentVersion

    now = utcnow()
    capabilities = db.scalars(
        select(DocumentCapability)
        .join(
            CommercialDocumentVersion,
            CommercialDocumentVersion.id == DocumentCapability.version_id,
        )
        .where(
            CommercialDocumentVersion.document_id == document_id,
            DocumentCapability.revoked_at.is_(None),
        )
    )
    count = 0
    for capability in capabilities:
        capability.revoked_at = now
        count += 1
    if count:
        db.flush()
    return count
