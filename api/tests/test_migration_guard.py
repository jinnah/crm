"""Populated-downgrade guard for a7c9e2d41f68 (jobs/documents/commercial/email).

Downgrading that revision would destroy irreplaceable business history, so its
downgrade() must refuse — BEFORE any mutation — whenever populated rows exist.
These tests prove that against a real disposable PostgreSQL database (the same
TEST_POSTGRES_URL used by the concurrency suite):

    TEST_POSTGRES_URL=postgresql+psycopg://user:pass@host:port/db \
        uv run pytest tests/test_migration_guard.py

The database is wiped (drop_all + alembic table) per test — never point this
at a database holding data you care about.
"""

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from app.config import get_settings
from app.models import Base, Job, Lead, User
from app.security.passwords import hash_password

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL not set; migration-guard tests need a disposable database",
)

GUARDED_REVISION = "a7c9e2d41f68"
PREVIOUS_REVISION = "d94f7ae1c2b5"


def _alembic_config() -> Config:
    api_root = Path(__file__).resolve().parents[1]
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "alembic"))
    return config


@pytest.fixture()
def migrated_engine():
    """A disposable database migrated to head via the real Alembic chain."""
    engine = sa.create_engine(TEST_POSTGRES_URL)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    Base.metadata.drop_all(engine)

    # env.py resolves the URL through get_settings().database_url.
    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_POSTGRES_URL
    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(), "head")
        yield engine
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url
        get_settings.cache_clear()
        with engine.begin() as connection:
            connection.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
        Base.metadata.drop_all(engine)
        engine.dispose()


def _current_revision(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()


def test_empty_database_round_trips(migrated_engine):
    """With no populated rows the downgrade is allowed and reversible."""
    assert _current_revision(migrated_engine) == GUARDED_REVISION
    command.downgrade(_alembic_config(), "-1")
    assert _current_revision(migrated_engine) == PREVIOUS_REVISION
    command.upgrade(_alembic_config(), "head")
    assert _current_revision(migrated_engine) == GUARDED_REVISION


def test_populated_downgrade_refuses_before_any_mutation(migrated_engine):
    session_factory = sa.orm.sessionmaker(bind=migrated_engine)
    with session_factory() as session:
        owner = User(
            email=f"owner-{uuid.uuid4().hex[:8]}@crm.test",
            password_hash=hash_password("migration guard test pw"),
            role="owner",
            is_active=True,
        )
        session.add(owner)
        session.flush()
        lead = Lead(name="Migration Guard Lead", source="manual")
        session.add(lead)
        session.flush()
        job = Job(
            job_number="J-2026-9999",
            lead_id=lead.id,
            title="Migration guard job",
            status="new",
            created_by=owner.id,
        )
        session.add(job)
        session.commit()
        job_id = job.id

    with pytest.raises(RuntimeError, match="job record"):
        command.downgrade(_alembic_config(), "-1")

    # Refusal must leave the schema AND the data untouched: still at head,
    # every guarded table present, the seeded row intact.
    assert _current_revision(migrated_engine) == GUARDED_REVISION
    inspector = sa.inspect(migrated_engine)
    tables = set(inspector.get_table_names())
    for table in (
        "jobs",
        "job_documents",
        "commercial_documents",
        "payments",
        "email_deliveries",
        "document_capabilities",
    ):
        assert table in tables, f"{table} was dropped despite the refusal"
    with migrated_engine.connect() as connection:
        survivor = connection.execute(
            sa.text("SELECT count(*) FROM jobs WHERE id = :id"), {"id": job_id}
        ).scalar_one()
    assert survivor == 1
