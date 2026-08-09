import pytest
from pydantic import ValidationError

from app.config import Settings, validate_production_settings

REAL_LOOKING_PEPPER = "k3P1x9vQ2mZ8bT4wR6yU0aS5dF7gH1jL3nC8eV2q"


def production_settings(**overrides) -> Settings:
    values = {
        "environment": "production",
        "session_token_pepper": REAL_LOOKING_PEPPER,
        "session_cookie_secure": True,
        "frontend_url": "https://crm.example.com",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_rejects_placeholder_pepper() -> None:
    settings = production_settings(session_token_pepper="dev-only-pepper-not-for-production")
    with pytest.raises(RuntimeError, match="SESSION_TOKEN_PEPPER"):
        validate_production_settings(settings)


def test_production_rejects_short_pepper() -> None:
    with pytest.raises(RuntimeError):
        validate_production_settings(production_settings(session_token_pepper="short"))


def test_production_rejects_insecure_cookie() -> None:
    with pytest.raises(RuntimeError, match="SESSION_COOKIE_SECURE"):
        validate_production_settings(production_settings(session_cookie_secure=False))


def test_production_rejects_http_frontend() -> None:
    with pytest.raises(RuntimeError, match="FRONTEND_URL"):
        validate_production_settings(production_settings(frontend_url="http://crm.example.com"))


def test_production_accepts_safe_configuration() -> None:
    validate_production_settings(production_settings())


def test_development_allows_local_placeholders() -> None:
    validate_production_settings(Settings(environment="development"))


def test_environment_is_restricted_to_recognized_values() -> None:
    for value in ("development", "test", "production"):
        assert Settings(environment=value).environment == value
    with pytest.raises(ValidationError):
        Settings(environment="prod")  # a typo must not bypass production checks
    with pytest.raises(ValidationError):
        Settings(environment="staging")


def test_smtp_tls_is_restricted() -> None:
    for value in ("starttls", "ssl", "none"):
        assert Settings(smtp_tls=value).smtp_tls == value
    with pytest.raises(ValidationError):
        Settings(smtp_tls="tls")
    with pytest.raises(ValidationError):
        Settings(smtp_tls="")


def test_wildcard_cors_origin_is_rejected() -> None:
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(cors_origins=["*"])
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(cors_origins=["http://localhost:3000", "*"])
    assert Settings(cors_origins=["http://localhost:3000"]).cors_origins == [
        "http://localhost:3000"
    ]


def test_frontend_url_must_be_http_origin() -> None:
    assert Settings(frontend_url="http://localhost:3000").frontend_url == "http://localhost:3000"
    assert (
        Settings(frontend_url="https://crm.example.com").frontend_url == "https://crm.example.com"
    )
    # A trailing slash is normalized away.
    assert (
        Settings(frontend_url="https://crm.example.com/").frontend_url == "https://crm.example.com"
    )
    for invalid in (
        "not-a-url",
        "ftp://crm.example.com",
        "https://crm.example.com/app",
        "https://crm.example.com?next=1",
        "https://crm.example.com#top",
        "https://",
    ):
        with pytest.raises(ValidationError):
            Settings(frontend_url=invalid)
