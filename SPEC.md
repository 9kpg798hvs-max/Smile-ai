# Post-Op Check-In Triage — Build Spec

An evening patient check-in and triage tool for an 11-doctor endodontic group.
Eleven doctors already do this by hand, every night. This automates the sending
and the sorting. It does not automate the doctor.

---

## 1. The one-paragraph version

Each endodontist photographs (or imports) their day sheet. The system extracts
patient names and mobile numbers, the doctor confirms the list, and a single
check-in text goes out from that doctor's dedicated number. Replies come back,
get classified **green / yellow / red**, and land in that doctor's inbox sorted
worst-first with a drafted response pre-filled in their own voice. **The doctor
approves every outbound reply.** Analytics accumulate across all 11 doctors.

---

## 2. Non-negotiables

These are the constraints that came out of testing against real patient
messages. Violating any of them makes the product unsafe or useless.

### 2.1 Nothing sends without approval — except the opening text

The only auto-sent message is the initial check-in:

> Hi {first_name}, this is Dr. {last_name} checking on you. How are you feeling?

Every reply after that is doctor-approved. The AI drafts; the human sends. This
is what makes a misclassification survivable — a wrong color becomes an
annoyance instead of an injury.

The doctor can choose per-session:
- **One by one** — tap through each green individually (default)
- **All at once** — bulk-approve the greens after reading them

### 2.2 Triage on clinical content, never on sentiment

**This is the most important rule in the system.** In real messages, the most
clinically urgent patients wrote the warmest, most polite texts. A patient four
weeks post-chemo reported facial swelling and cold sensitivity while opening
with "Hope all is well!" and "Everything is going very well." Any sentiment-based
classifier files that green. It is a red.

Do not score positivity. Score symptoms.

### 2.3 Terse is not the same as well

"ok" / "I'm doing ok" / "fine" → **yellow, always.** Stoic patients in real
trouble under-report. A vague non-answer is an unanswered question, not a
green light.

### 2.4 Reds escalate — they do not advise

A red draft's only job is to shorten the distance between the patient and the
doctor. It must not diagnose, reassure, or give clinical instructions.

> "Thank you for telling me. I want to see that — call the office now at
> {number}."

### 2.5 The replier is not always the patient

Spouses, parents, and adult children reply on the patient's behalf ("He is doing
good"). The classifier must handle third-person reports, and must not
under-weight distress reported about someone else. Flag proxy replies in the UI.

### 2.6 Bias to yellow under uncertainty

The only error that matters is a **red misfiled as green**. Yellow is cheap;
a missed red is a patient with a spreading infection who got "glad to hear it!"
and never followed up. When in doubt, escalate the color.

---

## 3. Triage definitions

| Color | Meaning | Draft behavior |
|---|---|---|
| **Red** | Pain, swelling, worsening symptoms, altered sensation/numbness, loose or lost temporary, medication reaction, any immunocompromise + symptom | Escalation only. Get them on the phone. |
| **Yellow** | A question, uncertainty, terse/ambiguous reply, or anything that can't confidently be ruled out | Draft an answer in the doctor's voice |
| **Green** | Clearly well, specific, no question asked | Standard reply, doctor-approved |

**Observed yellow categories** (narrow and learnable): diet and food restrictions,
medication timing/dosage, "is this normal," temporary crown behavior.

**Green subtype — unprompted praise.** Some greens are effusive thank-yous. In a
referral business these are marketable. Surface a one-tap "ask for a Google
review" action. Do not auto-send it.

---

## 4. Doctor voice

Each of the 11 doctors has their own voice model, trained on their own reply
history. No blended house voice.

Dr. Belani's baseline green reply, for calibration:

> "Glad to hear it. Stick with the meds, and expect the temp to sink in a bit.
> Reach out if you need me."

Register: warm, brief, plain. No hedging. Tells the patient exactly what to do.
Every doctor edit to a draft is training signal — capture it.

---

## 5. Architecture

### 5.1 Telephony — the thing that constrains everything

**You cannot build on personal cell phones.** iOS gives no app access to
Messages; there is no API and no permission to request. This is not a
workaround problem.

**Each doctor gets a dedicated virtual number** (Twilio or a dental-specific
platform with an API — Weave, NexHealth). Roughly $1–2/month per line, 11 lines
total. It rings inside the app on the phone they already carry. Nobody carries a
second device.

Practice-owned numbers also fix three problems the group has today:
- Associates have handed personal numbers to thousands of strangers, permanently
- No record exists of any clinical conversation
- No visibility into red rates across the group

**Required from the vendor:** BAA, 10DLC healthcare registration, 11 numbers,
send/receive API.

### 5.2 Compliance

Patient replies contain PHI — symptoms, diagnoses, treatment. This is not
optional:

- BAA with the SMS provider
- Encrypted storage at rest for message history
- Access controls and audit logging (11 doctors + admin)
- Schedule photos must not persist to the camera roll
- The outbound check-in itself contains no PHI by design — keep it that way

### 5.3 Data model (sketch)

```
Doctor        id, name, line_number, voice_profile_id
Patient       id, first_name, last_name, mobile, referring_dentist_id
Visit         id, patient_id, doctor_id, date, procedure, appt_number (1 or 2)
Message       id, visit_id, direction, body, sent_at, approved_by
Triage        message_id, color, rationale, model_version, doctor_override
Draft         message_id, body, edited_body, accepted (bool)
```

Notes:
- A root canal spans up to **two appointments**, same doctor both times.
  A red flag from appointment 1 must surface when the patient returns for
  appointment 2. This is clinical continuity, not analytics.
- `doctor_override` on Triage is the eval set. Every time a doctor recolors
  something, that's ground truth for improving the classifier.
- `Draft.edited_body` vs `body` is the voice training signal.

### 5.4 Schedule ingest

Day sheets already include name, mobile, and time. Two paths:

1. **Photo + OCR** — extract name/number/time, show a confirm screen with
   low-confidence digits flagged. Never send without confirmation. A single
   misread digit sends a patient text to a stranger.
2. **Practice-management export** (Dentrix / Eaglesoft / Open Dental) — preferred.
   No OCR risk, cleaner audit trail. Check what the group's PMS can export.

---

## 6. Screens

1. **Capture** — photograph or import today's schedule
2. **Confirm** — extracted patient list, low-confidence fields flagged, doctor
   confirms before anything sends
3. **Inbox** — replies sorted red → yellow → green → no reply. Day-strip at top
   showing the whole day's status at a glance. Bulk/individual toggle for greens.
4. **Thread** — full conversation, drafted reply pre-filled, **Approve and send**.
   Call button on reds.
5. **Trends** — reply rate, median response time, status distribution over time,
   common themes. Segmentable by doctor and procedure.

---

## 7. Analytics — the part that isn't obvious

Eleven endodontists generating structured post-op outcome data is a dataset
nobody else has. Beyond the operational dashboard:

- **Red rate by procedure type** — which procedures generate the most post-op pain
- **Red rate by operator** — a quality signal, handled carefully
- **Theme clustering** — if "pain when biting" clusters on one procedure, that's
  clinical, not administrative
- **Referring dentist loop** — the GP is the actual customer in a referral model.
  Closing the loop back to them ("your patient is comfortable") is a strong
  candidate for v2.

---

## 8. Build order

1. **Export the existing text corpus** from all 11 doctors' phones before anyone
   upgrades a device. This is the training data and it exists nowhere else.
2. **Vendor call** — BAA, 10DLC, API. Nothing else can start until this is settled.
3. **Classifier + drafting**, evaluated against the real corpus. Metric that
   matters: **zero reds misfiled as green.** Optimize recall on red, accept
   yellow false positives.
4. **Single-doctor pilot** (Dr. Belani), real patients, one month.
5. **Roll to associates** one at a time, each with their own voice profile.

---

## 9. Explicitly out of scope for v1

- Any auto-send beyond the initial check-in
- Patient-initiated conversations (this is a check-in tool, not a chat line)
- Clinical decision support
- Selling to other practices — build for this group first
