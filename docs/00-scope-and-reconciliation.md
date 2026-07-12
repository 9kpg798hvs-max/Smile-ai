# SmileFlow AI — Scope & Reconciliation

Two requirement documents exist in this repo:

1. **SPEC.md** — the original post-op check-in triage spec (endodontic group,
   classifier-first).
2. **The SmileFlow AI platform requirements** (delivered later) — the full
   end-to-end product: OCR schedule intake → doctor-approved follow-up sends →
   AI-triaged reply inbox → analytics, with roles, audit, and HIPAA posture.

The SmileFlow requirements **supersede and extend** SPEC.md. Where they
conflict, this document records the resolution so nothing changes silently.

## Deltas from SPEC.md (flag anything you want reversed)

| # | SPEC.md said | SmileFlow says | Resolution |
|---|---|---|---|
| 1 | Built for one 11-doctor endodontic group; "selling to other practices" out of scope | MVP = one practice, but architecture must be SaaS-ready (multi-practice) | Multi-tenant data model from day one (`practice_id` on every table); UI/business logic targets one practice for MVP |
| 2 | Three triage colors (red/yellow/green) | Eight reply categories + urgency level | **Both.** The classifier assigns a category (Doing Well, Pain, Swelling, Medication Question, Emergency, Appointment Request, Question/Mild Concern, Other) **and** an urgency (red/yellow/green). All SPEC.md safety rules apply to urgency unchanged — including the "red never files as green" gate and the eval harness |
| 3 | "The doctor approves every outbound reply" | "The doctor **or authorized staff member** reviews and edits the AI response before sending" | Role-based: a practice setting controls whether staff can send approved replies or only doctors. **Default: doctor-only**, matching SPEC.md |
| 4 | Only auto-sent message is the initial check-in | Same (no AI response sent automatically in MVP) | Unchanged; check-in sends only after the doctor approves the day's patient list |
| 5 | Doctor photographs their own day sheet | Staff uploads the schedule; doctor approves the extracted list | Staff-upload workflow adopted; doctor approval is a hard gate before any send |
| 6 | Dedicated virtual number per doctor | "Use the doctor's or practice's existing phone number whenever supported" | Twilio Hosted SMS can text-enable an existing landline/VoIP number where eligible; dedicated numbers are the fallback. Never a personal cell (SPEC §5.1 stands: iOS gives no programmatic access) |
| 7 | Endodontic-specific rules (2-appointment root canals, appt-1 red flag carryover) | General dental | Kept — they're modeled as optional fields (`appointment_number`, `prior_red_flag`) that general practices simply don't use |

## What is already built (Phase 0, tested)

- Two-stage reply classifier: LLM extracts clinical findings (no sentiment in
  the schema), deterministic rules assign urgency. 38 passing offline tests,
  including an exhaustive proof that no red-trigger finding can classify green.
- Eval harness with a hard CI gate (zero red→green), extraction caching, and a
  30-case synthetic seed dataset.
- PHI hygiene: real data directories gitignored; de-identification scrubber.

The classifier will be extended (not replaced) to also emit the eight
SmileFlow categories; its urgency output and safety gate are unchanged.

## Non-negotiables carried forward

1. Nothing sends to a patient without human approval, except the initial
   check-in (which itself sends only after the doctor approves the list).
2. The classifier must never file a red as green. Red recall is optimized;
   yellow false positives are accepted.
3. Triage on clinical content, never sentiment.
4. Mocked patient data only during development. Every mocked integration is
   labeled `MOCK` in code and UI.
