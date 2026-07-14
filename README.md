# SmileFlow AI — Dental Patient Follow-Up Platform

AI-powered post-op follow-up: OCR schedule intake → doctor-approved SMS
check-ins → AI-triaged reply inbox with human-approved responses.

- **Design package** (architecture, schema, roles, screens, HIPAA, phases,
  acceptance criteria, risks): [docs/](docs/) — start with
  [docs/00-scope-and-reconciliation.md](docs/00-scope-and-reconciliation.md)
- **Build status**: Phases 0–4 implemented and tested — AI triage engine;
  schema/auth/RBAC/audit; schedule intake with OCR review; doctor approval →
  scheduled sending with duplicate prevention; reply inbox with AI
  category/urgency, escalation-only red drafts, urgent alerts, and
  human-approved sending. Phases 5–7 (configuration screens, analytics,
  hardening) per [docs/06-delivery-plan.md](docs/06-delivery-plan.md).
  A React web interface (`web/`) covers all 21 required screens — login,
  dashboard, inbox/thread, approvals, intake, notifications, templates,
  settings, analytics, admin, and logs — browser-verified end to end
  (Playwright). Phase 7 hardening is partly done (login rate limiting,
  security headers, webhook shared-secret, backup script); remaining items
  (Twilio signature verification, 2FA) are on the [HIPAA
  checklist](docs/05-security-hipaa.md) and mostly need real provider
  accounts. **All integrations are MOCK** (OCR, SMS, reply AI in dev — see
  `/api/v1/health` and the in-app banner); mock patient data only.

```sh
# Run the full app (API + UI) locally with mock providers:
pip install -e ".[dev]" && (cd web && npm install && npm run build)
python -m server.seed
uvicorn server.app:create_app --factory   # open http://localhost:8000
```

```sh
pip install -e ".[dev]"
pytest                      # 119 offline tests
python -m server.seed       # seed MOCK data (logins printed)
uvicorn server.app:create_app --factory   # API at /api/v1
```

---

## Phase 0 — Post-Op Reply Triage Engine

The green/yellow/red triage classifier and the eval harness that proves it
against a labeled corpus (original [SPEC.md](SPEC.md) build-order step 3).
It will be extended to emit the eight SmileFlow reply categories; the safety
design below is unchanged.

## Safety architecture

The classifier is two stages, and **the LLM never picks the color**:

1. **Extraction** (`triage/extractor.py`) — Claude reads the reply and emits
   structured clinical findings: symptoms (with severity / trajectory /
   controlled-by-meds), questions, terseness, proxy reporter,
   immunocompromise, contact requests, praise. The schema
   (`triage/schema.py`) has **no sentiment field**, so tone physically cannot
   reach the color decision (SPEC §2.2).
2. **Rules** (`triage/rules.py`) — deterministic Python maps findings → color.
   Every SPEC non-negotiable is a unit-tested code path, not a prompt hope:
   - terse/vague → yellow, always (§2.3)
   - swelling, numbness, fever, med reaction, lost temporary → red on sight (§3)
   - severe / worsening / uncontrolled anything → red (§3)
   - immunocompromise + any symptom → red (§3)
   - proxy reports triaged identically, flagged for the UI (§2.5)
   - green is unreachable unless the patient *affirmatively* reports doing
     well with no symptoms and no questions; everything unclassifiable is
     yellow (§2.6)
   - extraction failure of any kind → yellow + `needs_manual_review`, never
     green, never a crash

`tests/test_rules_never_green_property.py` brute-forces every combination of
symptom attributes and message flags (thousands of cases) to prove no
red-trigger finding can map to green under any circumstances.

## The gate metric

`evals/runner.py` computes the usual metrics, but only one gates: **zero
red-labeled cases predicted green.** The process exits non-zero on any
violation, so CI fails. Red→yellow is tracked separately as a "soft miss"
(survivable — the doctor still sees it early), and yellow false positives on
greens are reported as an accepted cost (SPEC §2.6, §8).

An eval-set zero is necessary, not sufficient — the doctor-approval loop
(SPEC §2.1) remains the actual safety net, and every `doctor_override` in
production becomes a new labeled eval case.

## Layout

```
triage/
  schema.py      Findings / TriageResult / VisitContext (pydantic)
  rules.py       deterministic findings → color (the safety layer)
  prompts.py     versioned extraction prompt
  extractor.py   Claude structured-output extraction (claude-opus-4-8)
  classifier.py  classify(text, context) → TriageResult, fail-safe yellow
  deidentify.py  best-effort PHI scrubber for corpus prep
evals/
  runner.py      eval CLI: metrics, report, red→green gate, extraction cache
  DATA_FORMAT.md dataset format + labeling guidance
  datasets/synthetic_seed.jsonl   30 synthetic spec-archetype cases
tests/           38 offline tests (rules, property gate, harness, fail-safe)
```

## Running

```sh
pip install -e ".[dev]"
pytest                       # offline; no API key needed

# Real eval run (needs ANTHROPIC_API_KEY):
python -m evals.runner evals/datasets/synthetic_seed.jsonl
# Your corpus (kept under gitignored data/, see evals/DATA_FORMAT.md):
python -m evals.runner data/corpus.jsonl
```

Extractions are cached in `.eval_cache/` keyed on (model, prompt hash, text,
context) — iterating on the rules layer re-runs free; only prompt/model
changes re-hit the API. Model defaults to `claude-opus-4-8`; override with
`TRIAGE_MODEL` or `--model`.

## PHI handling

- `data/`, `evals/results/`, and `.eval_cache/` are **gitignored** — real
  patient text never enters this repository. `evals/datasets/` is committed
  and must stay synthetic-only.
- Eval runs send reply text to the Claude API. Before running the real
  corpus, either have a **BAA in place with Anthropic** or scrub the corpus
  first (`python -m triage.deidentify` — see its docstring for limits; it
  reduces exposure, it does not eliminate it).

## Decisions taken pending your sign-off

These were the open questions from the spec review; building proceeded on
safe defaults. Each is a small, localized change if you want it different.

1. **Expected post-op pain is yellow, not red and not green.** Pain that is
   not severe/worsening/uncontrolled ("a little sore but ibuprofen handles
   it, better than yesterday") lands yellow. Making it red would red-flag
   nearly every honest day-1 reply; making it green would let a symptom
   through unread. Change in `rules.py::_symptom_red_reason` / the
   symptom→yellow branch.
2. **Mild, stable bleeding and cold sensitivity are yellow;** severe or
   worsening anything is red. Swelling, numbness, fever, medication
   reactions, and temporary-restoration problems are red at *any* severity.
3. **Red→yellow is a tracked soft miss, not a gate failure.** Only
   red→green gates.
4. **"He is doing good" is green** (affirmative, proxy-flagged), while
   "ok"/"fine" are yellow (neutral non-answers per §2.3). If you want
   proxy replies or bare "doing good" held at yellow, that's one rule.
5. **Replies with zero status content ("Thanks!") are yellow** — no
   affirmative wellness report, no green (§2.6).
6. **PHI route**: de-identification helper shipped; BAA recommended before
   running the raw corpus. Waiting on your call from question 4c.

## What's next (per SPEC §8 build order)

- You export the corpus; import per `evals/DATA_FORMAT.md`; label (doctor
  ground truth); run; iterate prompt/rules until the gate holds at scale.
- Then drafting (doctor-voice replies), then the pilot app around it.
