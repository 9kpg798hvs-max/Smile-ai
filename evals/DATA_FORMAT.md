# Eval dataset format

One JSON object per line (JSONL). Files containing **real patient text belong
under `data/`** (gitignored) — never under `evals/datasets/`, which is
committed and holds synthetic cases only.

## Fields

| Field | Required | Type | Meaning |
|---|---|---|---|
| `id` | yes | string | Unique, stable case ID (used for caching and diffs) |
| `text` | yes | string | The patient reply, verbatim |
| `label` | yes | `"red" \| "yellow" \| "green"` | Ground-truth color — assigned by a doctor, per SPEC.md §3 |
| `context` | no | object | `{procedure, days_post_op, appointment_number, prior_red_flag, known_immunocompromised}` — all optional |
| `expect_proxy` | no | bool | True if the reply is from someone other than the patient (checked when present) |
| `notes` | no | string | Why this case is labeled the way it is; provenance |

## Example

```json
{"id": "seed-001", "text": "Hope all is well! ... my cheek has swollen a bit", "label": "red", "notes": "SPEC §2.2 archetype: warm tone masking swelling"}
{"id": "seed-002", "text": "ok", "label": "yellow", "notes": "SPEC §2.3 terse"}
```

## Labeling guidance (from SPEC.md §3)

- **red** — pain that is severe/worsening/uncontrolled, swelling, altered
  sensation/numbness, loose or lost temporary, medication reaction, fever,
  any immunocompromise + any symptom.
- **yellow** — a question, uncertainty, terse/ambiguous reply, expected mild
  post-op symptoms, anything that can't confidently be ruled out.
- **green** — clearly well, specific, no question asked, no symptoms.

When labelers disagree, or you're unsure: label the *more urgent* color.
The gate metric is zero red-labeled cases predicted green.

## Importing your real corpus

1. Export threads to JSONL with the fields above (one row per patient reply).
2. Optionally scrub: `python -m triage.deidentify data/raw.jsonl data/scrubbed.jsonl --names ...`
3. Keep everything under `data/` — it is gitignored.
4. Run: `python -m evals.runner data/scrubbed.jsonl`
