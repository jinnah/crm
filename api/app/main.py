from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.appointments import lead_router as appointment_lead_router
from app.api.v1.appointments import router as appointments_router
from app.api.v1.auth import router as auth_router
from app.api.v1.custom_fields import router as custom_fields_router
from app.api.v1.deps import CSRF_HEADER
from app.api.v1.health import router as health_router
from app.api.v1.inbound import MAX_BODY_BYTES
from app.api.v1.inbound import router as inbound_router
from app.api.v1.leads import router as leads_router
from app.api.v1.public_booking import manage_router as public_appointment_router
from app.api.v1.public_booking import router as public_booking_router
from app.api.v1.settings import public_router as public_router
from app.api.v1.settings import router as settings_router
from app.api.v1.users import router as users_router
from app.config import get_settings, validate_production_settings
from app.middleware import BodyLimitMiddleware
from app.services.mailer import SmtpMailer
from app.services.messaging import N8nSmsSender
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
    # Outermost of the user middlewares: counts actual received bytes and
    # rejects oversized inbound payloads before any body parsing happens.
    app.add_middleware(
        BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES, path_prefixes=("/api/v1/inbound",)
    )

    app.state.login_limiter = default_login_limiter()
    app.state.recovery_limiter = default_recovery_limiter()
    app.state.mailer = SmtpMailer(settings)
    app.state.sms_sender = N8nSmsSender(settings)

    @app.middleware("http")
    async def no_store_sensitive_responses(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Authentication, session, CSRF and user-management responses must
        # never be cached by browsers or intermediaries.
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/v1/auth") or path.startswith("/api/v1/users"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(leads_router, prefix="/api/v1")
    app.include_router(custom_fields_router, prefix="/api/v1")
    app.include_router(inbound_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")
    app.include_router(public_router, prefix="/api/v1")
    app.include_router(appointments_router, prefix="/api/v1")
    app.include_router(appointment_lead_router, prefix="/api/v1")
    app.include_router(public_booking_router, prefix="/api/v1")
    app.include_router(public_appointment_router, prefix="/api/v1")
    return app


app = create_app()
