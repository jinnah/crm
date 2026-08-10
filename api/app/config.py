from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that must never reach production; checked by validate_production_settings.
_PLACEHOLDER_SECRETS = {
    "",
    "change-me",
    "changeme",
    "dev-only-pepper-not-for-production",
}


def _validate_http_origin(value: str, field_name: str) -> str:
    """Require a bare http(s) origin (scheme://host[:port], no path/query)."""
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.netloc
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            f"{field_name} must be an http(s) origin like https://crm.example.com "
            "with no path or query"
        )
    return f"{parts.scheme}://{parts.netloc}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Service CRM"
    environment: Literal["development", "test", "production"] = "development"
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

    # Server-side API key for the n8n inbound-event endpoint. Empty disables
    # the endpoint (all requests rejected). Never exposed to the browser.
    inbound_api_key: str = ""

    # Dedicated rotatable key for the voice-ingestion endpoints. Falls back to
    # the inbound key when unset; with neither configured the endpoints fail
    # closed. Never exposed to the browser.
    voice_api_key: str = ""

    # Server-only credential the Next.js BFF routes present on the internal
    # capability endpoints. Empty disables those endpoints entirely (fail
    # closed). NEVER exposed via NEXT_PUBLIC_* or any response.
    internal_bff_key: str = ""

    # Outbound SMS: the CRM calls this authenticated n8n workflow, which holds
    # the Twilio credentials. No provider secrets are stored in PostgreSQL.
    n8n_send_url: str = ""
    n8n_send_secret: str = ""
    twilio_from_number: str = ""

    # --- Job documents: object storage --------------------------------
    # Binaries live outside PostgreSQL behind this abstraction. "local" keeps
    # objects under documents_local_path (a Docker volume in compose);
    # production uses any S3-compatible store. Credentials only ever live
    # here (environment/secrets), never in settings rows or the browser.
    documents_storage_backend: Literal["local", "s3"] = "local"
    documents_local_path: str = "./data/documents"
    documents_s3_bucket: str = ""
    documents_s3_endpoint_url: str = ""  # empty = provider default (AWS)
    documents_s3_region: str = ""
    documents_s3_access_key_id: str = ""
    documents_s3_secret_access_key: str = ""

    # --- Malware scanning ----------------------------------------------
    # Files stay quarantined until a scan succeeds. "clamd" streams to a
    # ClamAV daemon; "stub" is for development/tests only (detects the EICAR
    # test signature) and is refused by production validation.
    scanner_backend: Literal["clamd", "stub"] = "stub"
    clamd_host: str = "clamav"
    clamd_port: int = 3310

    # --- Transactional document email ----------------------------------
    # The one verified sender for this installation. Deployment
    # configuration, shown read-only in settings; with no address configured,
    # sending is disabled (drafts and PDFs still work) and nothing falls back
    # to an unknown address. The provider credential itself lives in n8n.
    document_email_from_address: str = ""
    # Server-side key n8n presents on the document-email claim/report
    # endpoints. Falls back to the inbound key when unset; with neither, the
    # endpoints fail closed.
    document_email_api_key: str = ""

    # Transactional SMTP for password-recovery email (not the future CRM email integration).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_sender: str = ""
    smtp_tls: Literal["starttls", "ssl", "none"] = "starttls"

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_origins(cls, value: list[str]) -> list[str]:
        # Credentials are always enabled for this API; a wildcard origin with
        # credentials is never acceptable.
        if any(origin.strip() == "*" for origin in value):
            raise ValueError("CORS_ORIGINS must not contain a wildcard origin")
        return value

    @field_validator("frontend_url")
    @classmethod
    def _frontend_url_is_origin(cls, value: str) -> str:
        return _validate_http_origin(value, "FRONTEND_URL")


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
    if not settings.frontend_url.startswith("https://"):
        problems.append("FRONTEND_URL must be an https origin in production")
    if settings.inbound_api_key and len(settings.inbound_api_key) < 32:
        problems.append("INBOUND_API_KEY must be at least 32 characters when set")
    if settings.voice_api_key and len(settings.voice_api_key) < 32:
        problems.append("VOICE_API_KEY must be at least 32 characters when set")
    if settings.internal_bff_key and (
        len(settings.internal_bff_key) < 32 or settings.internal_bff_key.startswith("dev-")
    ):
        problems.append("INTERNAL_BFF_KEY must be a generated secret of at least 32 characters")
    if settings.scanner_backend == "stub":
        problems.append("SCANNER_BACKEND must be 'clamd' in production; 'stub' is dev/test only")
    if settings.documents_storage_backend == "s3" and not settings.documents_s3_bucket:
        problems.append("DOCUMENTS_S3_BUCKET must be set when the s3 storage backend is selected")
    if settings.document_email_api_key and len(settings.document_email_api_key) < 32:
        problems.append("DOCUMENT_EMAIL_API_KEY must be at least 32 characters when set")
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
