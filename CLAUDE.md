# CLAUDE.md

## Product summary

CRM for local service businesses (~2–10 team members: roofing, HVAC, concrete, landscaping). A website form captures leads; n8n validates, normalizes and stores them in the customer's chosen system of record (Google Sheets **or** this CRM — mutually exclusive, no sync), then sends acknowledgment SMS to the lead and a new-lead SMS to the owner. Storage must succeed before any messaging. Later CRM phases: lead statuses/assignment, 5-minute response tracking, two-way SMS, Gmail/Outlook email via OAuth, notes, follow-ups, activity history, owner/manager/team-member roles.

## Deployment model: single-tenant

Each customer gets an isolated installation from the same versioned codebase: own deployment, PostgreSQL database, env config, secrets, n8n workflow, Twilio subaccount, email OAuth connection, branding, backups and logs.

- **Never** add `business_id`/`tenant_id` columns, cross-tenant filtering, shared databases or multi-tenant abstractions. One database = one business.

## One-UI rule

Exactly one frontend application (`web/`). The authenticated CRM, role-gated settings (including internal onboarding settings) and the public embedded form route all live in the same Next.js app. Never create separate frontends or a super-admin product.

## Technology stack (settled)

- `web/`: Next.js (App Router), React, TypeScript strict, pnpm, Vitest + React Testing Library, ESLint
- `api/`: FastAPI, SQLAlchemy, Alembic, Pydantic (+ pydantic-settings), uv, Pytest, Ruff
- PostgreSQL; Docker Compose for local and per-customer deployment
- API versioned under `/api/v1`; OpenAPI is the contract

Do not introduce: microservices, Kubernetes, message brokers, extra frontends, shared databases, multi-tenant frameworks, or speculative abstractions.

## Architectural rules

- n8n talks to the CRM only through the authenticated HTTP API — never directly to PostgreSQL.
- Lead intake will use an external submission ID for idempotency (later phase).
- Store a lead successfully before sending customer or owner SMS.
- Preserve raw external values when normalization may change them.
- Custom fields use metadata + stored values with immutable internal keys — never dynamic database columns.
- All schema changes go through Alembic migrations.
- Never commit secrets, OAuth tokens or production credentials; never expose private credentials via `NEXT_PUBLIC_*`; never log access tokens, authorization headers or complete sensitive payloads.
- Return safe error responses — no secrets, no stack traces.
- Twilio: separate platform-managed subaccount per installation. Gmail/Outlook: OAuth in a later phase.
- Keep deployments easy to install, update and troubleshoot across many isolated instances.
- Do not implement later-phase behavior early; no fake implementations.

## Current phase

**Appointment scheduling, calendar and automated reminders milestone complete — awaiting lead-architect review.** Phases 0–1, the core CRM milestone, the inbound-channel milestone and the public-form/SMS milestone are approved.

This milestone adds: owner-only scheduling settings (IANA business time zone, default duration, minimum notice, booking window, buffers, per-weekday business hours, self-booking toggle, confirmation/reminder toggles and offsets, four message templates with a fixed safe variable set); `appointments`, `booking_links` and `appointment_notifications` tables; availability calculation and buffer-aware conflict prevention serialized per staff member by a transaction-scoped advisory lock and backed by a PostgreSQL `EXCLUDE USING gist` constraint; a lead-detail appointment panel (history, create/reschedule/complete/no-show/cancel, `.ics` download, booking-link create/copy/revoke/regenerate); an authenticated `/calendar` route with day, week and agenda views and a staff filter; a public `/book/[token]` page and a customer `/appointment/[token]` page, both reached only through same-origin BFF routes with byte limits, throttling and honeypots; confirmation, reminder, rescheduling and cancellation SMS reusing the durable outbound-message path; and scheduling states in the attention queue.

Scheduling rules that must not regress: every timestamp is stored in UTC and each appointment keeps a snapshot of the zone it was scheduled under; a local time inside a DST gap is rejected and an ambiguous local time resolves to the first occurrence; booking-link and per-appointment manage tokens are stored only as keyed digests and an appointment UUID grants no public access; notifications are claimed with `FOR UPDATE SKIP LOCKED` and committed before the provider is contacted, so an interrupted send becomes a visible `unknown` and is never resent automatically; cancellation and rescheduling suppress only *pending* notifications, so sent history is never rewritten; reminders are dispatched by a scheduled n8n workflow calling `POST /api/v1/inbound/appointment-notifications/dispatch`.

Deferred to later phases: outbound WhatsApp/Facebook/email, Gmail/Outlook, Google Calendar or Outlook sync, recurring appointment series, marketing or bulk messaging, media storage, lead merging, payments, Sheets integration and import, production deployment automation.

## Validation commands

- `web/`: `pnpm lint && pnpm typecheck && pnpm test && pnpm build`
- `api/`: `uv run ruff check . && uv run ruff format --check . && uv run pytest`
- Compose: `docker compose config -q && docker compose up -d --build`
- Health: `GET http://localhost:8000/api/v1/health`

## Process rules

Implementation proceeds in phases. **Stop at the end of each phase for lead-architect review; do not begin the next phase without explicit approval.**

- Work directly on `main` — no feature branches or pull requests. Before starting a phase, fetch and confirm local `main` fast-forwards cleanly; stop and report if it cannot.
- Commit and push to `origin/main` only after all applicable validation passes. Never force-push, amend reviewed commits, or rewrite history.
- After pushing a completed phase, stop and report: commit SHA and URL, changed files, validation results, unresolved issues, and clean `git status`.
- Keep documentation minimal: only this file, the root `README.md`, and framework-required agent instruction files. No `docs/` directory, ADRs, planning documents, or implementation journals unless the lead architect requests them. Update docs only when commands, configuration, or operational instructions materially change.
- Keep each phase narrowly scoped: no early features, no speculative abstractions or dependencies. Report unrelated problems instead of silently expanding scope.
