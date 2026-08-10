import os
from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import get_db
from app.main import create_app
from app.models import Base, User
from app.security.passwords import hash_password

# Long enough for the 12-character policy; used across the test suite.
DEFAULT_PASSWORD = "correct horse battery staple"

# Test-only inbound API key (≥32 chars); real keys are generated per install.
TEST_INBOUND_KEY = "test-inbound-api-key-0123456789abcdef"
os.environ["INBOUND_API_KEY"] = TEST_INBOUND_KEY
# Server-only BFF credential for the internal capability endpoints, and the
# dedicated voice-ingestion key. Test values; real keys are generated.
TEST_INTERNAL_KEY = "test-internal-bff-key-0123456789abcdef"
os.environ["INTERNAL_BFF_KEY"] = TEST_INTERNAL_KEY
TEST_VOICE_KEY = "test-voice-api-key-0123456789abcdefgh"
os.environ["VOICE_API_KEY"] = TEST_VOICE_KEY
get_settings.cache_clear()


def internal_headers() -> dict[str, str]:
    """Headers the Next.js BFF would send to the internal endpoints."""
    return {"X-Internal-Key": TEST_INTERNAL_KEY}


def voice_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_VOICE_KEY}


class RecordingMailer:
    """Test double: records outgoing reset mail; never contacts a real service."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    def send_password_reset(self, to_email: str, reset_url: str) -> None:
        if self.fail:
            raise RuntimeError("simulated SMTP failure")
        self.sent.append((to_email, reset_url))


@pytest.fixture()
def db_session_factory() -> Generator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    # Schema creation here is test-only; the application itself never calls create_all().
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def db(db_session_factory: sessionmaker[Session]) -> Generator[Session]:
    session = db_session_factory()
    yield session
    session.close()


class RecordingSmsSender:
    """Test double for outbound SMS; never contacts a real provider."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.outcome = None
        self.raise_error = False
        self._counter = 0

    def send(self, message):
        from app.services.messaging import SendOutcome

        if self.raise_error:
            raise RuntimeError("simulated messaging failure")
        self.sent.append(
            {
                "to": message.to_phone,
                "body": message.body,
                "purpose": message.purpose,
                "message_id": str(message.id),
            }
        )
        if self.outcome is not None:
            return self.outcome
        self._counter += 1
        return SendOutcome(status="submitted", provider_sid=f"SMtest{self._counter:026d}")


@pytest.fixture()
def mailer() -> RecordingMailer:
    return RecordingMailer()


@pytest.fixture()
def sms_sender() -> RecordingSmsSender:
    return RecordingSmsSender()


@pytest.fixture()
def app(
    db_session_factory: sessionmaker[Session],
    mailer: RecordingMailer,
    sms_sender: "RecordingSmsSender",
) -> Generator[FastAPI]:
    application = create_app()

    def override_get_db() -> Generator[Session]:
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = override_get_db
    application.state.mailer = mailer
    application.state.sms_sender = sms_sender
    yield application
    application.dependency_overrides.clear()


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def make_user(db: Session):
    def _make(
        email: str = "owner@example.com",
        password: str = DEFAULT_PASSWORD,
        role: str = "owner",
        is_active: bool = True,
        must_change_password: bool = False,
    ) -> User:
        user = User(
            email=email,
            password_hash=hash_password(password),
            role=role,
            is_active=is_active,
            must_change_password=must_change_password,
        )
        db.add(user)
        db.commit()
        return user

    return _make


def login(client: TestClient, email: str, password: str = DEFAULT_PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def csrf_headers(login_response) -> dict[str, str]:
    return {"X-CSRF-Token": login_response.json()["csrf_token"]}
