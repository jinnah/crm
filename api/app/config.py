from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that must never reach production; checked by validate_production_settings.
_PLACEHOLDER_SECRETS = {
    "",
    "change-me",
    "changeme",
    "dev-only-pepper-not-for-production",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Service CRM"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://crm:crm@localhost:5432/crm"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Authentication / sessions
    session_token_pepper: str = "dev-only-pepper-not-for-production"
    session_cookie_name: str = "crm_session"
    session_cookie_secure: bool = False
    session_inactivity_minutes: int = 480  # 8 hours
    session_absolute_days: int = 7

    # Public frontend URL, used for password-reset links and Origin validation.
    frontend_url: str = "http://localhost:3000"

    # Transactional SMTP for password-recovery email (not the future CRM email integration).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_sender: str = ""
    smtp_tls: str = "starttls"  # starttls | ssl | none


def validate_production_settings(settings: Settings) -> None:
    """Fail startup when production is configured with missing or placeholder secrets."""
    if settings.environment != "production":
        return
    problems: list[str] = []
    pepper = settings.session_token_pepper
    if pepper in _PLACEHOLDER_SECRETS or pepper.startswith("dev-") or len(pepper) < 32:
        problems.append("SESSION_TOKEN_PEPPER must be a generated secret of at least 32 characters")
    if not settings.session_cookie_secure:
        problems.append("SESSION_COOKIE_SECURE must be true in production")
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
