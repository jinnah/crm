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

Docker Compose includes an `n8n` service (http://localhost:5678, data persisted in the `n8n_data` volume). Channel workflows live in `n8n/workflows/` and are mounted read-only at `/workflows`. Install them with the repeatable installer, which is safe to run on every deployment and update:

```bash
sh n8n/install-workflows.sh
docker compose restart n8n
```

The installer lists what is already present, **stops with instructions if duplicate active workflows exist** (it never deletes workflow data itself), imports only workflows whose names are not installed yet, and publishes what it imported. To update an existing workflow, delete or rename the old copy deliberately in the editor (the n8n 2.x CLI has no delete command) and re-run the installer. This keeps every installation on the same versioned workflow set with no silent duplicates.

### Editor access and exposure

n8n 2.x has no basic-auth environment mechanism (`N8N_BASIC_AUTH_*` was removed). Editor access is protected by **n8n's built-in user management**: the first visit to http://localhost:5678 prompts you to create the owner account, and further editors are invited from *Settings → Users*. Locally the port is bound to `127.0.0.1` only, so the editor is not reachable from other machines.

For any non-local deployment, put n8n behind a reverse proxy that:

- exposes only the production webhook paths (`/webhook/...`) to the internet, so providers can deliver events;
- restricts the editor UI and the REST/management surface (`/rest`, `/api`, `/webhook-test`) to trusted networks, a VPN, or an authenticated proxy;
- terminates TLS and sets `WEBHOOK_URL` to the public HTTPS origin (Twilio and Meta signature checks are computed against that exact URL).

Webhook endpoints (POST unless noted): `/webhook/web-form`, `/webhook/twilio-sms`, `/webhook/twilio-voice`, `/webhook/twilio-status` (delivery callbacks), `/webhook/meta-whatsapp`, `/webhook/meta-messenger` (the Meta paths also answer the GET verification handshake), plus the internal `/webhook/twilio-send` the CRM calls to send SMS. "Appointment Reminders" has no webhook — it runs on a schedule and calls the CRM. Configure the provider secrets in `.env` (`TWILIO_AUTH_TOKEN`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, optional `FORM_SHARED_SECRET`); signature checks reject unauthenticated calls. In the n8n UI, set "Inbound Error Handler" as the default error workflow for the channel workflows. Website forms POST JSON with `submission_id`, `name`, `email`/`phone`, `message`, and optional `form`/`page`/`campaign`/`referrer`/`submitted_at`.

## Jobs, documents and commercial records

Every uploaded or generated document belongs to a job, and every job belongs to one customer, so a document's customer is always derived `document → job → lead`. Uploads accept PDF, PNG, JPEG and WebP only; content is proven by decoding (never the filename or client MIME), images are re-encoded with metadata stripped, and every file is quarantined until malware scanning passes. Quotes, invoices and receipts use integer minor units, concurrency-safe numbers (`Q-2026-0001`, …) assigned at issuance, and immutable issued versions whose exact PDF bytes and SHA-256 are stored; corrections supersede or void — history is never rewritten. Manual payments record externally completed money movement only (cash, check, bank transfer, externally processed card); the CRM stores no card or bank credentials and rejects likely card numbers in payment fields. Online payments are out of scope by design.

### Document storage

Binaries live outside PostgreSQL behind `DOCUMENTS_STORAGE_BACKEND`:

- `local` (default): the `documents_data` volume, mounted at `/data/documents` in the `api` container.
- `s3`: any S3-compatible store for production — set `DOCUMENTS_S3_BUCKET`, optional `DOCUMENTS_S3_ENDPOINT_URL` (non-AWS providers), `DOCUMENTS_S3_REGION`, and the key pair in deployment secrets.

**Backups must cover the database and the object store together**: a database snapshot without the matching objects (or vice versa) restores documents whose files are missing. Take `pg_dump` and the object-store backup as one coordinated operation, and restore them as a pair. The authenticated `POST /api/v1/inbound/documents/reconcile` endpoint (run on a schedule by n8n, or manually) sweeps abandoned temporary objects and reports referenced-but-missing ones after a restore.

**Restore and rollback procedure.** The jobs/documents/commercial/email migration (`a7c9e2d41f68`) refuses to downgrade while any job, uploaded-document, commercial, payment or email-delivery row exists — the check runs before any schema change, so a refused downgrade leaves the database untouched (`api/tests/test_migration_guard.py` proves this against PostgreSQL). Rolling back a populated installation therefore always means **restore from backup**, never a data-bearing downgrade:

1. Take the coordinated backup pair (database + object store) immediately **before** every upgrade; stop the `api` container first so no upload sits mid-pipeline between the two snapshots.
2. To roll back: stop `api` and `n8n`, restore the database dump, restore the object-store backup from the same operation, then start the **code version that matches the restored schema** — each release expects exactly its own Alembic head; never run newer code against an older restored schema or vice versa.
3. After any restore, run `POST /api/v1/inbound/documents/reconcile` and review the result: `removed_orphans` counts swept stray objects (writes that happened after the object-store snapshot), `missing_objects` counts rows whose file is gone (writes after the database snapshot). Missing objects cannot be reconstructed — restore a matching object-store backup, or remove those document rows deliberately through the audited delete flow.
4. If the database and object store were restored from different points in time, treat the pair as diverged: re-restore a matching pair. Reconcile only measures the blast radius; it cannot re-pair mismatched backups.

### Malware scanning

Uploads stay quarantined until scanning succeeds; a scanner outage fails closed. `SCANNER_BACKEND=stub` (EICAR-only) keeps development light; production **must** use `clamd` — start the bundled scanner with `docker compose --profile scanning up -d` (first start downloads signatures; allow a few minutes).

### Document email

The CRM never talks to an email provider. It records a durable delivery (recipient, subject, bodies, exact document version) and the **Document Email** n8n workflow claims the work, sends through the installation's one verified sender, and reports `submitted` / `failed` / `unknown` back — `delivered` is only ever set from a trusted provider confirmation. Setup per installation:

1. Verify the sender address with your email provider, then set `DOCUMENT_EMAIL_FROM_ADDRESS` (and `DOCUMENT_EMAIL_API_KEY`) in `.env`. With no address configured, sending is disabled with a clear message; drafts and PDFs still work. Neither users nor API callers can override the From address.
2. In the n8n editor, create an SMTP credential named **Document Email SMTP** and select it in the workflow's two send nodes.
3. Set "Inbound Error Handler" as the workflow's error workflow.

The owner-facing pieces (From display name, Reply-To, subject/body templates with a small variable allowlist, link expiry, attach-versus-link default) live in *Settings → Documents & email*.

## Shutdown and cleanup

```bash
docker compose down            # stop containers (data is kept)
docker compose down -v         # stop and DELETE the database, n8n, document and scanner volumes
```

## Public request form

The customer-facing form lives at `/request` in the same Next.js app. The browser posts to the same-origin route `/api/public-request`, which adds the server-side form secret and forwards to the n8n web-form webhook — no secrets ever reach the browser. Embed it on a customer site with an iframe:

```html
<iframe src="https://crm.example.com/request" title="Request a quote"
        style="width:100%;max-width:640px;height:760px;border:0"></iframe>
```

Owners configure the form title, automated acknowledgment, new-lead alert, notification number and first-response target under **Settings** in the CRM. Templates accept `{{lead_name}}`, `{{business_name}}`, `{{source}}` and `{{lead_id}}`; any other variable is rejected.

## Branding and the business logo

Owners upload a logo under **Settings → Business & branding**. The server accepts PNG, JPEG or WebP up to 1 MB, verifies the file by decoding it (SVG, HTML, animated images and spoofed content types are refused regardless of filename), applies the EXIF orientation, strips all metadata, scales to at most 512px and re-encodes to PNG — the uploaded bytes themselves are never stored. The normalized image lives in PostgreSQL on the settings row and is served at `GET /api/v1/public/logo` with an `ETag`, `X-Content-Type-Options: nosniff` and short revalidating cache headers, so the CRM shell and the public booking/request pages all share one cached copy. Without a logo, a wordmark built from the business initials is shown instead. Downgrading the logo migration refuses to run while a logo is stored; remove it first.

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

## AI voice-call channel

A Twilio number answered by an AI voice agent (any provider) feeds the CRM as
the authoritative store — the agent and n8n never touch PostgreSQL, and no
Twilio or AI credentials ever enter the database, the browser or workflow
JSON. The flow is: call -> AI agent/n8n -> "Voice Call Intake" workflow ->
`POST /api/v1/inbound/voice-calls/completed` -> lead + durable call record +
SMS intents committed -> acknowledgment and staff alerts through the existing
Twilio send path.

Point the existing AI voice workflow at the intake webhook with the shared
secret, sending one structured completion per call:

```
POST http://localhost:5678/webhook/voice-complete
X-Voice-Secret: <VOICE_INTAKE_SECRET>
{
  "call_sid": "CA0123456789abcdef0123456789abcdef",
  "caller_phone": "+15555550123",
  "caller_name": "Pat Example",
  "call_status": "completed",
  "service_requested": "Water heater replacement",
  "summary": "No hot water since Monday; wants a quote this week.",
  "preferred_callback_window": "weekday mornings",
  "appointment_preference": "Tuesday if possible",
  "urgency": "normal",
  "requires_human_follow_up": false,
  "transfer_outcome": "none",
  "disclosure_version": "v1",
  "consent_result": "granted",
  "started_at": "2026-08-09T14:00:00Z",
  "ended_at": "2026-08-09T14:06:00Z",
  "duration_seconds": 360
}
```

`call_sid` is the idempotency identity: replays return the original result,
and a retry that disagrees about the caller's number gets 409 and flags the
record for review instead of rewriting history. Matching is conservative — an
exact phone match appends to that lead, ambiguity creates a review lead, and
collected values never overwrite populated CRM fields.

The agent can also offer real appointment slots mid-call through
server-to-server tools (`POST /api/v1/inbound/voice/availability` and
`/voice/book`, authenticated with `VOICE_API_KEY` or the inbound key, CallSid
in the body). Booking accepts only an exactly offered slot from the same
corrected scheduler as public booking and is idempotent per call. A caller's
stated preference is stored as text on the call — it is never presented as a
confirmed appointment.

Owners configure acknowledgment/alert messages, recipients (business number,
assigned staff's notification phone, or both), the default voice-booking
staff member and transcript retention under **Settings -> Voice calls**.
Transcript retention is off by default; when enabled it requires per-call
consent, keeps bounded text for the configured days, and the daily
"Voice Transcript Cleanup" workflow purges expired or consent-less
transcripts and recording references while the summary and audit trail
survive. Google Sheets can stay as an optional export after CRM success, but
it is no longer authoritative and its failures never roll back the CRM.

## Production reverse-proxy boundary

The API container binds to `127.0.0.1` — in production only a reverse proxy
faces the internet. It should: forward `/api/v1/public/*` (form info, logo,
branding) and nothing else of the API to browsers; keep `/api/v1/internal/*`
and `/api/v1/inbound/*` unreachable from outside (the BFF and n8n reach them
over the internal network with their keys); and avoid logging request bodies
or the `/book/[token]` / `/appointment/[token]` page URLs, which carry
capability tokens (FastAPI itself never sees tokens in URLs — the BFF sends
them in request bodies on fixed internal paths).
