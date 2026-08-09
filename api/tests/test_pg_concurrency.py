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
