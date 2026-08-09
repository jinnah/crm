# Service CRM

Single-tenant CRM for local service businesses (roofing, HVAC, concrete, landscaping). Each customer gets an isolated installation — its own deployment, PostgreSQL database, configuration and secrets — all built from this one codebase.

## Repository structure

```
web/                Next.js frontend (App Router, React, TypeScript strict)
api/                FastAPI backend (SQLAlchemy, Alembic, Pydantic)
docker-compose.yml  Local / per-customer deployment (db + api + web)
.env.example        Environment variable template (safe placeholders)
CLAUDE.md           Project rules and current phase
```

## Prerequisites

- Node.js 20+ and pnpm 11+
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Docker with Docker Compose (for PostgreSQL and full-stack runs)

## Initial setup

```bash
# Frontend
cd web
pnpm install

# Backend
cd api
uv sync
```

## Environment configuration

Copy `.env.example` and adjust values as needed:

```bash
cp .env.example .env        # used by docker compose
cp .env.example api/.env    # used by the API when run outside Docker
```

All values in `.env.example` are safe local placeholders. Never commit `.env` files or real credentials. `NEXT_PUBLIC_*` variables are exposed to the browser — never put secrets in them.

For production, generate `SESSION_TOKEN_PEPPER` outside source control and keep it secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Production also requires `SESSION_COOKIE_SECURE=true`; the API refuses to start with placeholder secrets. Password-recovery email needs the `SMTP_*` settings; with `SMTP_HOST` empty (local default), reset emails are skipped safely.

## Development

Run PostgreSQL in Docker, the apps directly:

```bash
docker compose up -d db

cd api && uv run uvicorn app.main:app --reload --port 8000
cd web && pnpm dev            # http://localhost:3000
```

The API serves OpenAPI docs at http://localhost:8000/docs and health at http://localhost:8000/api/v1/health.

## Docker Compose

```bash
docker compose up -d --build   # full stack: db + api + web
docker compose ps              # status and health
docker compose logs -f api     # logs
```

## Tests, lint and type checks

Frontend (`web/`):

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Backend (`api/`):

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## Database migrations

From `api/` (database URL comes from `DATABASE_URL` / `api/.env`):

```bash
uv run alembic upgrade head                          # apply migrations
uv run alembic revision --autogenerate -m "message"  # create a migration
uv run alembic downgrade -1                          # roll back one
```

Inside Docker Compose:

```bash
docker compose exec api uv run --frozen --no-dev alembic upgrade head
```

## Account administration

There is no public registration. After applying migrations, create the first owner account (interactive prompts; the password uses hidden input and is never passed as an argument):

```bash
cd api && uv run python -m app.cli create-owner
```

Emergency password reset for any account (sets a temporary password, revokes the user's sessions, forces a change at next login):

```bash
cd api && uv run python -m app.cli reset-password
```

Inside Docker Compose, prefix with `docker compose exec api` and use `uv run --frozen --no-dev` instead of `uv run`.

The owner account created at first login must change its temporary password before entering the CRM. Further users are created by an owner from the CRM's user-management page.

## Inbound events (n8n)

`POST /api/v1/inbound/events` captures inbound requests (web form, phone call, SMS, WhatsApp, Facebook, email) as leads and timeline activities. It authenticates with the server-side `INBOUND_API_KEY` (sent as `X-API-Key`; generate with the command shown in `.env.example`) and requires an `Idempotency-Key` header per event — retries with the same key safely return the original result. n8n workflows call this endpoint; nothing writes to PostgreSQL directly.

## n8n channel workflows

Docker Compose includes an `n8n` service (http://localhost:5678, data persisted in the `n8n_data` volume). Channel workflows live in `n8n/workflows/` and are mounted read-only at `/workflows`. Import and activate them once per installation:

```bash
docker compose exec n8n n8n import:workflow --separate --input=/workflows
docker compose exec n8n n8n update:workflow --all --active=true
docker compose restart n8n
```

Webhook endpoints (POST unless noted): `/webhook/web-form`, `/webhook/twilio-sms`, `/webhook/twilio-voice`, `/webhook/meta-whatsapp`, `/webhook/meta-messenger` (the Meta paths also answer the GET verification handshake). Configure the provider secrets in `.env` (`TWILIO_AUTH_TOKEN`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, optional `FORM_SHARED_SECRET`); signature checks reject unauthenticated calls. In the n8n UI, set "Inbound Error Handler" as the default error workflow for the channel workflows. Website forms POST JSON with `submission_id`, `name`, `email`/`phone`, `message`, and optional `form`/`page`/`campaign`/`referrer`/`submitted_at`.

## Shutdown and cleanup

```bash
docker compose down            # stop containers (data is kept)
docker compose down -v         # stop and DELETE the database volume
```
