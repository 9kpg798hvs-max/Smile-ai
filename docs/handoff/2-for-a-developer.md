# SmileFlow AI — Summary for a software developer / IT contractor

## What this is
A HIPAA-oriented post-op patient follow-up app for a dental practice: OCR of a
daily schedule → doctor-approved SMS check-ins → AI-triaged reply inbox with
human-approved responses. It is **built and tested against mock data**; we're
hiring you to deploy it on compliant infrastructure and finish the go-live items.

## Current state (honest)
- Full working app: Python 3.11 / FastAPI backend, React + TypeScript (Vite)
  frontend served by the API, SQLAlchemy models (SQLite in dev → **PostgreSQL**
  in prod), in-process send scheduler.
- **209 automated tests pass** (unit + API + a browser smoke via Playwright).
- All external integrations run behind interfaces with **MOCK implementations**;
  dev/test never call a real backend. Real adapters for Anthropic (AI) and
  Twilio (SMS) exist but are unverified against live accounts.
- Design docs live in the repo: `docs/00`–`06` (architecture, DB schema, role
  matrix, screens, **HIPAA checklist**, phased plan) and `docs/handoff/`.
- Source: private GitHub repo (access provided separately). Start with `README.md`.

## Core invariants you must not break
1. **Only the initial check-in auto-sends**, and only after a doctor approves the
   day's list. Every reply is human-approved (`approve_and_send_draft` /
   `bulk-approve-green` are the *only* send paths for replies).
2. **The classifier must never file an urgent ("red") reply as "well" (green).**
   Enforced in `triage/rules.py`, covered by a property test and a CI eval gate
   (`evals/runner.py` exits non-zero on any red→green).
3. Tenant + role scoping is enforced server-side (`server/rbac.py`,
   `server/deps.py`), not in the UI.

## What we're hiring you to do (see `docs/05-security-hipaa.md` for the full list)
- **Deploy** on a HIPAA-eligible host with a signed BAA (AWS/GCP/Azure). Docker
  Compose (api, worker, db, frontend) is the intended shape; Postgres via
  `DATABASE_URL`.
- **Encryption at rest** for the database, uploaded schedule files, and backups;
  TLS in transit; secrets in a manager (never in the repo).
- **Twilio go-live:** create account, complete **A2P 10DLC** healthcare
  registration, check **Hosted SMS** eligibility for the practice's existing
  number(s), wire the real SMS adapter, and — required before internet exposure —
  **replace the webhook shared-secret with Twilio request-signature verification**
  (`server/hardening.py` marks the spot).
- **Anthropic go-live:** set `ANTHROPIC_API_KEY`; the real reply classifier and
  OCR are constructed lazily. With no key, replies safely hold at "needs manual
  review" — verify that fail-safe survives deployment.
- **Scheduled backups + a rehearsed restore** (`scripts/backup.sh` is a starting
  point), plus monitoring/alerting on the send worker and webhooks.
- Optional but recommended: **2FA** for logins; per-IP rate limiting exists.
- **Run the AI evaluation on the practice's real historical messages** before the
  pilot (`evals/`), to confirm the zero-missed-red bar holds on real data. The
  practice will supply a de-identified export; `triage/deidentify.py` helps.

## Scale
MVP target: one practice, ~25 patients/day, multiple doctors. Schema is
multi-tenant (`practice_id` everywhere) so it can extend to multiple practices
later, but don't build that now.

## Rough running costs (confirm with vendors)
Estimates at this volume, monthly: cloud host low tens of dollars; Twilio ~$1–2
per number + a small per-message fee (plus one-time 10DLC registration); Anthropic
usage-based and expected to be modest. Not authoritative — please validate.

*The app is a working prototype on test data. Treat "go-live" as new work: real infra, real credentials, and the security items above, none of which are done yet.*
