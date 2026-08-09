import pytest

from app.config import Settings, validate_production_settings

REAL_LOOKING_PEPPER = "k3P1x9vQ2mZ8bT4wR6yU0aS5dF7gH1jL3nC8eV2q"


def test_production_rejects_placeholder_pepper() -> None:
    settings = Settings(environment="production", session_cookie_secure=True)
    with pytest.raises(RuntimeError, match="SESSION_TOKEN_PEPPER"):
        validate_production_settings(settings)


def test_production_rejects_short_pepper() -> None:
    settings = Settings(
        environment="production", session_token_pepper="short", session_cookie_secure=True
    )
    with pytest.raises(RuntimeError):
        validate_production_settings(settings)


def test_production_rejects_insecure_cookie() -> None:
    settings = Settings(
        environment="production",
        session_token_pepper=REAL_LOOKING_PEPPER,
        session_cookie_secure=False,
    )
    with pytest.raises(RuntimeError, match="SESSION_COOKIE_SECURE"):
        validate_production_settings(settings)


def test_production_accepts_generated_secrets() -> None:
    settings = Settings(
        environment="production",
        session_token_pepper=REAL_LOOKING_PEPPER,
        session_cookie_secure=True,
    )
    validate_production_settings(settings)


def test_development_allows_local_placeholders() -> None:
    validate_production_settings(Settings(environment="development"))
