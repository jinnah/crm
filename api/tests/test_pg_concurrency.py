"""PostgreSQL concurrency tests for last-owner protection and duplicate-email
creation. SQLite ignores SELECT ... FOR UPDATE, so these run only against a
real disposable PostgreSQL database:

    TEST_POSTGRES_URL=postgresql+psycopg://user:pass@host:port/db \
        uv run pytest tests/test_pg_concurrency.py
"""

import os
import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Base, User
from app.security.passwords import hash_password
from app.services import users as user_service
from app.services.users import UserManagementError

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL not set; PostgreSQL concurrency tests need a disposable database",
)

TEMP_PASSWORD = "temporary password 123"


@pytest.fixture()
def pg_session_factory():
    engine = create_engine(TEST_POSTGRES_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)  # test-only; the app itself migrates via Alembic
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


def make_owner(session, email: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password(TEMP_PASSWORD),
        role="owner",
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


def test_concurrent_owner_removals_cannot_leave_zero_owners(pg_session_factory) -> None:
    """Two transactions each demote a different owner; the row locks must
    serialize them so exactly one succeeds and one active owner remains."""
    setup = pg_session_factory()
    owner_a = make_owner(setup, "owner-a@example.com")
    owner_b = make_owner(setup, "owner-b@example.com")
    setup.close()

    session1 = pg_session_factory()
    session2 = pg_session_factory()
    try:
        # Transaction 1 evaluates the demotion of owner A and holds the
        # FOR UPDATE locks (no commit yet).
        target_a = session1.get(User, owner_a.id)
        acting_1 = session1.get(User, owner_b.id)
        user_service.update_user(session1, acting_1, target_a, role="manager")

        # Transaction 2 concurrently demotes owner B; it must block on the
        # locked owner rows rather than seeing the stale two-owner count.
        outcome: dict[str, object] = {}

        def demote_b() -> None:
            try:
                target_b = session2.get(User, owner_b.id)
                acting_2 = session2.get(User, owner_a.id)
                user_service.update_user(session2, acting_2, target_b, role="manager")
                session2.commit()
                outcome["result"] = "committed"
            except UserManagementError as error:
                session2.rollback()
                outcome["result"] = "conflict"
                outcome["status"] = error.status_code

        thread = threading.Thread(target=demote_b)
        thread.start()
        thread.join(timeout=2)
        assert thread.is_alive(), "transaction 2 should be blocked on the row locks"

        session1.commit()  # releases the locks; transaction 2 re-evaluates
        thread.join(timeout=10)
        assert not thread.is_alive()

        assert outcome["result"] == "conflict"
        assert outcome["status"] == 409
    finally:
        session1.close()
        session2.close()

    check = pg_session_factory()
    active_owners = check.scalars(select(User).where(User.role == "owner", User.is_active)).all()
    check.close()
    assert len(active_owners) == 1, "concurrent removals must never leave zero active owners"


def test_concurrent_duplicate_email_creation_yields_one_conflict(pg_session_factory) -> None:
    """Two transactions insert the same normalized email; the unique index
    serializes them and the loser gets the safe 409, not an unhandled error."""
    setup = pg_session_factory()
    acting_owner = make_owner(setup, "owner@example.com")
    setup.close()

    session1 = pg_session_factory()
    session2 = pg_session_factory()
    try:
        acting_1 = session1.get(User, acting_owner.id)
        user_service.create_user(
            session1, acting_1, "New.Hire@Example.com", "team_member", TEMP_PASSWORD
        )  # flushed, uncommitted: holds the unique-index claim

        outcome: dict[str, object] = {}

        def create_duplicate() -> None:
            try:
                acting_2 = session2.get(User, acting_owner.id)
                user_service.create_user(
                    session2, acting_2, "new.hire@example.com", "manager", TEMP_PASSWORD
                )
                session2.commit()
                outcome["result"] = "committed"
            except UserManagementError as error:
                outcome["result"] = "conflict"
                outcome["status"] = error.status_code

        thread = threading.Thread(target=create_duplicate)
        thread.start()
        thread.join(timeout=2)
        assert thread.is_alive(), "transaction 2 should be blocked on the unique index"

        session1.commit()
        thread.join(timeout=10)
        assert not thread.is_alive()

        assert outcome["result"] == "conflict"
        assert outcome["status"] == 409
    finally:
        session1.close()
        session2.close()

    check = pg_session_factory()
    rows = check.scalars(select(User).where(User.email == "new.hire@example.com")).all()
    check.close()
    assert len(rows) == 1, "exactly one creation must succeed"


def test_concurrent_inbound_duplicates_yield_one_lead_and_activity(pg_session_factory) -> None:
    """Two simultaneous inbound submissions with the same Idempotency-Key must
    produce exactly one lead, one activity, and identical responses."""
    from sqlalchemy import func

    from app.api.v1.schemas import InboundEventRequest
    from app.config import get_settings
    from app.models import InboundEvent, Lead, LeadActivity
    from app.services.inbound import process_inbound_event

    payload = InboundEventRequest(
        channel="sms",
        provider="twilio",
        external_event_id="SM-concurrent",
        sender_phone="+15550107777",
        content="Concurrent hello",
    )
    settings = get_settings()
    barrier = threading.Barrier(2)
    results: list[tuple[str, str, bool]] = []
    errors: list[Exception] = []

    def submit() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            result = process_inbound_event(session, payload, "evt-concurrent-1", settings)
            results.append((str(result.lead.id), str(result.activity.id), result.replayed))
        except Exception as error:  # pragma: no cover - failure diagnostics
            errors.append(error)
        finally:
            session.close()

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not errors, errors
    assert len(results) == 2
    assert results[0][:2] == results[1][:2], "both requests must return the same lead/activity"

    check = pg_session_factory()
    assert check.scalar(select(func.count()).select_from(Lead)) == 1
    assert check.scalar(select(func.count()).select_from(LeadActivity)) == 1
    assert check.scalar(select(func.count()).select_from(InboundEvent)) == 1
    check.close()


def test_concurrent_settings_creation_yields_one_row(pg_session_factory) -> None:
    """Two transactions racing to create the settings row must leave exactly
    one; the unique singleton key decides the winner."""
    from sqlalchemy import func

    from app.models import CommunicationSettings
    from app.services.messaging import get_settings_row

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def create() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            get_settings_row(session)
            session.commit()
        except Exception as error:  # pragma: no cover - failure diagnostics
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not errors, errors

    check = pg_session_factory()
    assert check.scalar(func.count(CommunicationSettings.id)) == 1
    # Reads stay deterministic afterwards.
    first = get_settings_row(check).id
    assert get_settings_row(check).id == first
    check.close()
