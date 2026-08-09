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

**Inbound channel integrations milestone complete — awaiting lead-architect review.** Phases 0–1 and the core CRM milestone approved. This milestone adds: the n8n Compose service (pinned image, named volume, env-only secrets) with version-controlled workflows in `n8n/workflows/` for website forms, Twilio SMS and voice/missed-call/voicemail events, WhatsApp, and Facebook Messenger — each validating provider signatures (Twilio HMAC-SHA1, Meta X-Hub-Signature-256, form shared secret/honeypot), normalizing to the CRM inbound contract with deterministic provider-derived idempotency keys, calling `POST /api/v1/inbound/events` with bounded retries, plus a central error workflow. CRM side: `lead_external_identities` (unique channel+provider+sender ID → lead) so provider-only senders reuse one lead, with conflict flagging instead of merging; corrections: actual-byte inbound body limit, required custom fields cannot be cleared, archived leads reject notes. Workflow Code-node logic is executed directly by Vitest from the committed JSON.

The CRM milestone adds: single `Lead` record (statuses new/contacted/qualified/won/lost; archive/restore, never hard-delete; E.164 phone when the country code is present — never guessed), role-scoped access (owners/managers see and manage everything; team members see only assigned leads and may change status, follow-ups, notes and custom values), activity timeline with automatic entries for status/assignment/follow-up/archive changes (inbound requests are immutable history), owner-managed custom fields (text/number/date/boolean/select; immutable keys; deactivation preserves data), attention queue (overdue and due-today follow-ups, new unassigned, needs-review), and the authenticated idempotent `POST /api/v1/inbound/events` endpoint for n8n (X-API-Key auth via INBOUND_API_KEY, required Idempotency-Key stored as keyed digest with a PostgreSQL unique constraint, conservative exact email/phone matching, ambiguous or identity-less events become needs_review leads).

Phase 1 rules remain: no public registration, MFA/OAuth, JWTs or browser-storage auth, Redis, or background workers; generic errors; never store or log raw tokens or plaintext passwords.

Deferred to later phases: real provider onboarding (Twilio numbers, Meta app review), outbound/two-way messaging, Gmail/Outlook, embedded public web form UI, Sheets integration and import, response-time tracking, media storage, lead merging, production deployment automation.

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
