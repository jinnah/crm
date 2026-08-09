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

The appointment migration creates the `btree_gist` extension so PostgreSQL itself can refuse two overlapping appointments for the same staff member. The database role needs permission to create it (the default `postgres` superuser in Docker Compose does); on a managed host, enable `btree_gist` before upgrading.

### If you already applied an earlier `f93c51bfa8ee`

An earlier form of this migration kept the **oldest** `communication_settings` row and deleted the rest, so a database that ran it may have lost settings that lived on a newer duplicate row. Deleted rows cannot be reconstructed from the database — restore from a backup taken before the upgrade, or re-enter the values under **Settings** in the CRM. The current migration is configuration-aware and aborts rather than choosing between conflicting rows. To confirm what a database now holds:

```bash
docker compose exec db psql -U crm -d crm -c "SELECT id, business_name, alert_destination_phone, response_target_minutes FROM communication_settings;"
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
# n8n 2.x publishes workflows individually (update:workflow --active was removed):
docker compose exec n8n sh -c 'n8n list:workflow | cut -d"|" -f1 | xargs -I{} n8n publish:workflow --id={}'
docker compose restart n8n
```

The exported JSON carries no workflow id, so importing again creates a second copy rather than updating the first. Import once per installation; to update a workflow afterwards, open it in the editor and paste the new JSON, or delete the old copy there first.

### Editor access and exposure

n8n 2.x has no basic-auth environment mechanism (`N8N_BASIC_AUTH_*` was removed). Editor access is protected by **n8n's built-in user management**: the first visit to http://localhost:5678 prompts you to create the owner account, and further editors are invited from *Settings → Users*. Locally the port is bound to `127.0.0.1` only, so the editor is not reachable from other machines.

For any non-local deployment, put n8n behind a reverse proxy that:

- exposes only the production webhook paths (`/webhook/...`) to the internet, so providers can deliver events;
- restricts the editor UI and the REST/management surface (`/rest`, `/api`, `/webhook-test`) to trusted networks, a VPN, or an authenticated proxy;
- terminates TLS and sets `WEBHOOK_URL` to the public HTTPS origin (Twilio and Meta signature checks are computed against that exact URL).

Webhook endpoints (POST unless noted): `/webhook/web-form`, `/webhook/twilio-sms`, `/webhook/twilio-voice`, `/webhook/twilio-status` (delivery callbacks), `/webhook/meta-whatsapp`, `/webhook/meta-messenger` (the Meta paths also answer the GET verification handshake), plus the internal `/webhook/twilio-send` the CRM calls to send SMS. "Appointment Reminders" has no webhook — it runs on a schedule and calls the CRM. Configure the provider secrets in `.env` (`TWILIO_AUTH_TOKEN`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, optional `FORM_SHARED_SECRET`); signature checks reject unauthenticated calls. In the n8n UI, set "Inbound Error Handler" as the default error workflow for the channel workflows. Website forms POST JSON with `submission_id`, `name`, `email`/`phone`, `message`, and optional `form`/`page`/`campaign`/`referrer`/`submitted_at`.

## Shutdown and cleanup

```bash
docker compose down            # stop containers (data is kept)
docker compose down -v         # stop and DELETE the database volume
```

## Public request form

The customer-facing form lives at `/request` in the same Next.js app. The browser posts to the same-origin route `/api/public-request`, which adds the server-side form secret and forwards to the n8n web-form webhook — no secrets ever reach the browser. Embed it on a customer site with an iframe:

```html
<iframe src="https://crm.example.com/request" title="Request a quote"
        style="width:100%;max-width:640px;height:760px;border:0"></iframe>
```

Owners configure the form title, automated acknowledgment, new-lead alert, notification number and first-response target under **Settings** in the CRM. Templates accept `{{lead_name}}`, `{{business_name}}`, `{{source}}` and `{{lead_id}}`; any other variable is rejected.

## Appointments and reminders

Owners set the business time zone (IANA name), default appointment length, minimum booking notice, how far ahead customers may book, buffers either side, per-weekday business hours, message templates and reminder offsets under **Settings → Scheduling**. Appointment templates accept `{{lead_name}}`, `{{business_name}}`, `{{appointment_date}}`, `{{appointment_time}}`, `{{assigned_staff}}`, `{{appointment_subject}}` and `{{booking_reference}}`; any other variable is rejected.

Staff schedule from the lead detail page and see everything they may access on `/calendar` (day, week and agenda views, with a staff filter for owners and managers). Every appointment offers an `.ics` download.

Customers book through a revocable, expiring link created on the lead detail page. The raw token is shown once — only its digest is stored — and the public page at `/book/[token]` exposes nothing but the business name, introduction, staff display name, duration, time zone and free slots. After booking, the customer gets a separate per-appointment link (`/appointment/[token]`) for changing or cancelling; knowing an appointment's UUID grants no access. Both pages talk only to same-origin routes (`/api/public-booking/[token]`, `/api/public-appointment/[token]`), which enforce byte limits, content-type checks and per-IP throttling and forward nothing but the chosen time.

Reminders are dispatched by the **Appointment Reminders** workflow, which runs every five minutes and calls the authenticated CRM endpoint:

```bash
curl -X POST -H "X-API-Key: $INBOUND_API_KEY" \
  http://localhost:8000/api/v1/inbound/appointment-notifications/dispatch
```

The CRM claims each notification atomically and owns every send, so overlapping runs cannot send a reminder twice and n8n never touches PostgreSQL. An interrupted provider response is recorded as `unknown` and never resent automatically — it surfaces in the attention queue for someone to check.
