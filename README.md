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

No migrations exist yet — the first will arrive with the Phase 1 schema.

## Shutdown and cleanup

```bash
docker compose down            # stop containers (data is kept)
docker compose down -v         # stop and DELETE the database volume
```
