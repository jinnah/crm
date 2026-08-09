from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.auth import router as auth_router
from app.api.v1.deps import CSRF_HEADER
from app.api.v1.health import router as health_router
from app.api.v1.users import router as users_router
from app.config import get_settings, validate_production_settings
from app.services.mailer import SmtpMailer
from app.services.rate_limit import default_login_limiter, default_recovery_limiter


def create_app() -> FastAPI:
    settings = get_settings()
    validate_production_settings(settings)
    app = FastAPI(title=settings.app_name, version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER],
    )

    app.state.login_limiter = default_login_limiter()
    app.state.recovery_limiter = default_recovery_limiter()
    app.state.mailer = SmtpMailer(settings)

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    return app


app = create_app()
