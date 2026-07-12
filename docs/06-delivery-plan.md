# 10–13. Development Phases, Acceptance Criteria, Test Plan, Assumptions & Risks

## 10. Development phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 — AI triage engine** ✅ done | Findings extractor, deterministic urgency rules, eval harness + red→green gate, seed dataset | 38 tests green; gate enforced |
| **1 — Foundation** | Full DB schema, auth, sessions, RBAC, audit write-path, seed mock data, API skeleton | All models + constraints tested; login/RBAC tested |
| **2 — Schedule intake** | Upload endpoint + storage, OCR adapter (Claude vision, MOCK in tests), review/edit/exclude API + screens 4–6 | Mock day-sheet flows end-to-end to a submitted list |
| **3 — Approval → send** | Doctor approval gate, visit/follow-up creation with dedup, scheduler, MOCK SMS adapter, statuses, queue + dashboard counts, screens 7–9 | No send without approval (tested); duplicates blocked (tested); scheduled mock sends fire at configured times |
| **4 — Inbox & AI replies** | Inbound webhook, threads, classifier extended to 8 categories, drafting in doctor tone, urgent alerts, category override, approve-and-send gate, screens 10–12 | Red reply → alert + needs_attention (tested); draft can't send without approver (tested) |
| **5 — Configuration** | Templates + placeholders + preview, doctor send times, notifications center, user/office/phone admin, screens 2, 3, 13–15, 17, 18 | Template render + preview tested; per-doctor send time respected |
| **6 — Visibility** | Full dashboard tiles, analytics, delivery logs, audit log viewer, screens 8 (full), 16, 19, 20 | Tile counts match DB (tested); analytics queries tested on seeded data |
| **7 — Hardening & go-live prep** | 2FA, rate limits, webhook signatures, backups screen 21, Playwright smoke suite, real-provider adapters behind config, HIPAA checklist walkthrough | Checklist items flipped to ☑ or explicitly deferred with owner |

Each phase = implemented + tested + pushed before the next starts. Nothing is
reported as "works" without a test exercising it.

## 11. Acceptance criteria (per module)

| Module | Acceptance criteria (all must have automated tests where marked ⊙) |
|---|---|
| 1 Login/auth | ⊙ Wrong password rejected; ⊙ session cookie httpOnly; ⊙ logout revokes server-side; ⊙ inactive user cannot log in; lockout after repeated failures |
| 2 Clinic/office setup | ⊙ Admin can CRUD offices; ⊙ office manager cannot; timezone stored and used by scheduler |
| 3 Doctor/staff mgmt | ⊙ Admin creates users with roles; ⊙ doctor gets doctor_profile; ⊙ office scoping enforced |
| 4 Schedule upload | ⊙ Accepts image/PDF, rejects others; file stored privately; upload row `processing` |
| 5 OCR validation | ⊙ Extraction returns rows with per-field confidence; ⊙ crossed-out rows arrive `excluded (source: ocr)`; OCR failure → manual-entry path, never a crash |
| 6 Patient confirmation | ⊙ Staff can edit/add/remove/exclude rows; ⊙ staff exclusion overrides OCR (and vice versa is impossible); ⊙ all edits audited |
| 7 Doctor approval | ⊙ Only the treating doctor can approve their list; ⊙ zero outbound messages exist before approval; ⊙ reject returns list to staff with note |
| 8 Dashboard | ⊙ Every tile count equals the underlying query; tiles deep-link to filtered views |
| 9 Message queue | ⊙ `ready_to_send` rows send at the doctor's configured time in office tz (fake clock test); ⊙ excluded/opted-out patients never send |
| 10 Inbox | ⊙ Inbound webhook creates/updates the right thread; ⊙ filters (doctor/office/category/urgency/status) return correct sets; unread counts correct |
| 11 Thread | ⊙ Full history ordered with timestamps; shows AI category+confidence and approved_by/sent_by per outbound |
| 12 Urgent queue | ⊙ Red-urgency replies appear immediately; ⊙ emergency-language reply (breathing/swallowing/bleeding/allergic) is red; acknowledged state audited |
| 13 Templates | ⊙ All six placeholders render; ⊙ per-doctor/office/procedure resolution order; ⊙ preview shows final text before approval |
| 14 Send times | ⊙ Per-doctor time persisted; ⊙ change before send re-schedules pending follow-ups |
| 15 Notifications | ⊙ Each of the 7 required notification types fires on its trigger; read state per user |
| 16 Analytics | ⊙ Delivery rate, response rate, avg response time, category %, urgent count computed correctly on a seeded fixture; filterable by office/doctor/procedure |
| 17 SMS config | ⊙ Number→doctor/office mapping drives the outbound sender; MOCK provider clearly labeled in UI |
| 18 Users/permissions | ⊙ Full matrix in docs/03 enforced per endpoint (parametrized test over the matrix) |
| 19 Delivery logs | ⊙ Every provider status event stored and visible; failures link to retry |
| 20 Audit logs | ⊙ Login, approval, send, override, exclusion, settings-change all produce rows; log is append-only |
| 21 Security/backup | Backup job runs and is visible; restore procedure documented and rehearsed |
| AI engine | ⊙ Red-never-green gate (Phase 0, keeps running in CI); ⊙ 8-category mapping tested; ⊙ draft for red = escalation-only (no clinical advice); ⊙ AI cannot reach the send path |

## 12. Test plan

| Layer | Tooling | What it covers |
|---|---|---|
| Unit | pytest | Rules engine, RBAC matrix (parametrized over every role×permission), template rendering, scheduler time math, state-machine transitions |
| API/integration | pytest + httpx client, SQLite | Every endpoint: happy path + authz denial + tenant isolation; webhook ingest with signed/unsigned payloads; approval gate; dedup constraint |
| AI evals | Phase-0 harness | Category + urgency accuracy vs labeled sets; **hard gate: zero red→green**; prompt changes require a green eval run |
| End-to-end | Playwright (Phase 7) | Flow A and Flow B happy paths on the built SPA with MOCK providers |
| Non-functional | scripted | Auth lockout, rate limits, no-PHI-in-logs grep, backup/restore rehearsal |
| Data | — | **Mock patients only** (seed generator produces obviously fake names/numbers). Real-corpus eval work stays in gitignored `data/` under Phase-0 rules |

CI order: lint → unit/API tests → AI eval gate. A red→green regression fails the build.

## 13. Assumptions & unresolved technical risks

**Assumptions**
1. MVP: one practice, ~25 patients/day, multiple doctors/offices; DB-as-queue is sufficient.
2. English-only messaging in MVP (multilingual is future scope; `patients.language` already stored).
3. Practice will sign BAAs (Anthropic, Twilio, host) before any real patient use; until then everything runs on mock data.
4. Staff-send-replies stays **off** unless the practice enables it (SPEC.md default).
5. The initial check-in template contains no PHI (name + doctor + "how are you feeling").

**Risks (open)**
1. **Hosted SMS eligibility** — the practice's existing landline may not be eligible for Twilio Hosted SMS (carrier-dependent, weeks-long process). Fallback: new local numbers per office/doctor. Must be validated early in Phase 3.
2. **A2P 10DLC registration lead time** — healthcare campaign approval can take days-to-weeks; start paperwork before Phase 3 ends.
3. **OCR on real day sheets** — handwriting, cross-out styles, and multi-column layouts vary; per-field confidence + mandatory human review is the control, but extraction quality needs a labeled day-sheet eval set (mirror of the reply-eval approach). A single misread phone digit texts a stranger — phone fields get the strictest confidence threshold + visual verify UI.
4. **TCPA/consent** — patients must have consented to receive texts; needs practice policy + STOP handling (built) + consent flag if the practice requires it.
5. **Doctor-voice drafting quality** — matching tone from a handful of approved replies is unproven; mitigated by mandatory human edit/approve and by storing edit deltas as training signal.
6. **Anthropic API data retention** — BAA + retention configuration must be verified for the chosen models before real PHI flows (Phase 0 note stands).
7. **Shared family phones** — one mobile number, two patients: threading by phone can merge conversations; soft-unique + UI warning, revisit before go-live.
8. **Eaglesoft integration** — no public API assumption is safe; future phase uses CSV export or a certified integration partner. Not an MVP blocker.
