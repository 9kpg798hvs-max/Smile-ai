# 1–2. System Architecture & Technical Stack

## Architecture overview

```
                            ┌──────────────────────────────────────────┐
                            │              React SPA (Vite)            │
                            │  21 screens: intake, approval, inbox,    │
                            │  analytics, admin (docs/04)              │
                            └───────────────┬──────────────────────────┘
                                            │ HTTPS / JSON, session cookie
                            ┌───────────────▼──────────────────────────┐
                            │            FastAPI backend               │
                            │  auth + RBAC + audit middleware          │
                            │ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
                            │ │ Intake   │ │ Messaging│ │ Inbox/AI  │  │
                            │ │ (OCR)    │ │ (queue)  │ │ (triage)  │  │
                            │ └────┬─────┘ └────┬─────┘ └────┬──────┘  │
                            └──────┼────────────┼────────────┼─────────┘
                 ┌─────────────────┤            │            ├───────────────┐
        ┌────────▼───────┐ ┌───────▼──────┐ ┌───▼────────┐ ┌─▼─────────────┐
        │  Claude API     │ │  PostgreSQL  │ │ Scheduler  │ │ SMS provider  │
        │  (BAA required) │ │  (encrypted  │ │ (send-time │ │ adapter:      │
        │  OCR extraction │ │   at rest)   │ │  worker)   │ │ Twilio | MOCK │
        │  reply triage   │ │              │ │            │ │ + webhooks    │
        │  reply drafting │ └──────────────┘ └────────────┘ └───────────────┘
        └────────────────┘
```

**Key boundaries**

- **Provider adapters.** SMS (`SMSProvider` interface: `send`, webhook parse,
  hosted-number status) and AI (extractor interface, already in `triage/`) are
  behind interfaces with a `MOCK` implementation. Dev/test always run MOCK;
  production swaps in Twilio/Claude by configuration. Every mocked surface is
  labeled in the UI.
- **The send gate is server-side.** No code path can create an outbound SMS
  unless (a) it is the initial check-in of a doctor-approved, deduplicated
  follow-up, or (b) it is a human-approved reply. Enforced in the messaging
  service, not the UI.
- **AI never sends.** The AI produces classifications and drafts as database
  rows; only the messaging service sends, and it requires an approving user id.
- **Multi-tenant from day one.** Every row carries `practice_id`; all queries
  are scoped through a request context. MVP runs one practice.
- **Scheduler.** A worker loop scans `follow_ups` in `ready_to_send` whose
  doctor-configured send time has arrived (per-doctor time, office timezone)
  and hands them to the SMS adapter. Uses the DB as the queue (25 patients/day
  scale does not need Redis/celery; the interface allows swapping later).

## Technical stack (recommended)

| Layer | Choice | Why |
|---|---|---|
| Backend | **Python 3.11 + FastAPI + SQLAlchemy 2 + Alembic** | The triage engine (Phase 0) is already Python; FastAPI gives typed JSON APIs, dependency-injected auth, OpenAPI docs for free |
| Database | **PostgreSQL 15+** (SQLite in dev/tests) | Relational integrity for the approval/dedup constraints; JSONB for findings/audit payloads; row-level tenancy |
| Frontend | **React 18 + TypeScript + Vite + Tailwind** | 21-screen SPA with live inbox filtering; standard, hireable stack |
| Auth | Server-side sessions (httpOnly secure cookies), passwords via scrypt, optional TOTP 2FA (Phase 7) | Simple, revocable, audit-friendly; no JWT footguns |
| AI | **Claude API — `claude-opus-4-8`** via existing `triage/` package; vision for OCR; structured outputs everywhere | One vendor, one BAA; OCR + triage + drafting on the same account |
| SMS | **Twilio** behind `SMSProvider` (see docs/01 §SMS recommendation below) | BAA available, Hosted SMS for existing numbers, A2P 10DLC, delivery webhooks |
| Scheduling | In-process worker (asyncio task) reading the DB queue | Right-sized for 25/day; adapter-swappable |
| Files (schedule images, patient photos) | Local encrypted volume in MVP; S3-compatible w/ SSE later | Keep PHI storage surface minimal |
| Deploy | Docker Compose (api, worker, db, frontend) on a HIPAA-eligible host (AWS/GCP/Azure with BAA) | Single-box MVP, portable |
| Tests | pytest (unit + API via httpx), the Phase-0 eval harness for AI, Playwright smoke for the SPA (Phase 7) | |

## 7. API & integration plan

All endpoints JSON under `/api/v1`, session-cookie auth, RBAC-checked, audit-logged.

| Area | Endpoints (representative) |
|---|---|
| Auth | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` |
| Admin | CRUD `/practices`, `/offices`, `/users`, `/phone-numbers`; `GET /audit-log` |
| Intake | `POST /schedule-uploads` (file), `GET /schedule-uploads/{id}` (OCR result), `PATCH /schedule-uploads/{id}/entries/{eid}` (edit/exclude/override), `POST /schedule-uploads/{id}/submit` (to doctor) |
| Approval | `GET /approvals/pending`, `POST /schedule-uploads/{id}/approve`, `POST .../reject` |
| Messaging | `GET /follow-ups?date=&status=`, `POST /follow-ups/{id}/exclude`, `GET /queue`, `POST /messages/{id}/retry` |
| Inbox | `GET /conversations?filter…`, `GET /conversations/{id}`, `POST /conversations/{id}/notes`, `POST /conversations/{id}/assign`, `POST /conversations/{id}/archive`, `POST /replies/{id}/override-category`, `POST /drafts/{id}/approve-and-send` (RBAC-gated) |
| Templates | CRUD `/templates`, `POST /templates/{id}/preview` |
| Settings | `GET/PATCH /doctors/{id}/settings` (send time, tone), `GET/PATCH /practice/settings` |
| Notifications | `GET /notifications`, `POST /notifications/{id}/read` |
| Analytics | `GET /analytics/summary?range=&office=&doctor=` |
| Webhooks | `POST /webhooks/sms/inbound`, `POST /webhooks/sms/status` (provider-signed) |

**External integrations**

| Integration | MVP | Notes |
|---|---|---|
| Claude API | Real (behind interface; MOCK in tests) | BAA + appropriate data-retention config before any real PHI |
| Twilio SMS | `MOCK` adapter in dev; Twilio adapter code + webhook verification | Go-live requires BAA, A2P 10DLC registration, Hosted SMS eligibility check per office number |
| Eaglesoft / PMS | **Not in MVP** — future | Interim path: CSV import of completed procedures (same intake pipeline, no OCR risk) |
| Email (notifications) | Console/MOCK in MVP | Any real provider needs PHI-free message bodies or a BAA |

## 8. SMS provider recommendation

**Twilio**, for four reasons: (1) signs BAAs and documents HIPAA-eligible
products; (2) **Hosted SMS** can text-enable the practice's *existing* landline
numbers where eligible — directly satisfying "use the doctor's existing number
when supported," with number porting or new local numbers as fallback; (3)
first-class inbound webhooks + per-message delivery status; (4) A2P 10DLC
registration for healthcare traffic is well-trodden.

Runner-up: Telnyx (also BAA-capable, cheaper) — the `SMSProvider` interface
keeps us portable. Dental-specific platforms (Weave, NexHealth) lock the
number to their product; rejected for MVP.

Compliance notes baked into the design: STOP/HELP handling is mandatory
(opt-outs recorded on the patient, hard-block future sends); outbound
check-in text contains no PHI by design (SPEC §5.2); quiet-hours guard on the
scheduler.
