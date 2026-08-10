"""Concurrency-safe human-readable numbers (J-2026-0001, INV-2026-0001, …).

Allocation locks the (kind, year) counter row FOR UPDATE, so two concurrent
issuances serialize and can never mint the same value. The number becomes
permanent only when the caller's transaction commits; a rollback leaves a gap,
which is acceptable — reuse is not. SQLite ignores FOR UPDATE; the PostgreSQL
behaviour is covered by the concurrency tests.
"""

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import NumberSequence, utcnow

# Owner-configurable prefixes are letters only, short, and never empty.
PREFIX_PATTERN = re.compile(r"^[A-Za-z]{1,8}$")


class NumberingError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def validate_prefix(value: str) -> str:
    cleaned = value.strip().upper()
    if not PREFIX_PATTERN.match(cleaned):
        raise NumberingError("A number prefix must be 1-8 letters.")
    return cleaned


def allocate_number(db: Session, kind: str, prefix: str) -> str:
    """Return the next number for `kind`, e.g. "INV-2026-0001".

    Must be called inside the transaction that persists the numbered record;
    the counter row lock is held until that transaction ends.
    """
    year = utcnow().year
    for attempt in range(2):
        row = db.scalar(
            select(NumberSequence)
            .where(NumberSequence.kind == kind, NumberSequence.year == year)
            .with_for_update()
        )
        if row is not None:
            break
        # First allocation of the year: create the counter, tolerating a
        # concurrent creator by retrying the locked read once.
        try:
            with db.begin_nested():
                db.add(NumberSequence(kind=kind, year=year, last_value=0))
        except IntegrityError:
            if attempt == 1:  # pragma: no cover - second race in a row
                raise
    else:  # pragma: no cover - loop always breaks or raises
        raise NumberingError("Could not allocate a number.", status_code=500)

    row.last_value += 1
    db.flush()
    return f"{validate_prefix(prefix)}-{year}-{row.last_value:04d}"
