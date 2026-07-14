# SmileFlow AI — Summary for our attorney / HIPAA compliance advisor

**What it is.** A tool that automatically texts our post-op patients an evening
"how are you feeling?" check-in from the practice's own number, then uses AI to
sort the replies by urgency and draft a suggested response. **A person on our
team approves and sends every reply** — the AI never messages a patient on its
own, except the initial check-in, which only goes out after a doctor approves
that day's patient list.

**Where it stands.** The software is built and fully tested, but only on *made-up*
patients. It has never touched real patient data and will not until the items
below are in place.

## What we need from you

We believe three outside companies will handle protected health information
(PHI) and therefore each needs a **Business Associate Agreement (BAA)** before we
use real patients. Please advise on and help execute these:

| Company | What it does for us | Why it touches PHI |
|---|---|---|
| **Anthropic** | Provides the AI that reads patient replies | Patient messages (symptoms) are sent to it for classification |
| **Twilio** | Sends/receives the text messages | Carries the patient conversations |
| **Our cloud host** (e.g. AWS, Google, or Microsoft) | Runs the software and stores data | Stores the message history and schedules |

All three publicly offer BAAs and market HIPAA-eligible services; we are asking
you to confirm suitability and paper the agreements.

## What's relevant to your review

- **The outbound check-in text contains no medical information** by design — just
  the patient's first name and "Dr. ___ checking on you." The clinical detail is
  only in the patients' *replies*.
- **Patient consent / TCPA:** patients must have agreed to receive texts. The
  software honors "STOP" (auto opt-out) and records it. Please advise on our
  consent language and where it should be captured.
- **Security already built in:** role-based access (staff see only what they
  should), individual logins with lockout, a complete audit log of who did what,
  and a design that keeps patient data encrypted and access-controlled. A full
  HIPAA security checklist is maintained with the software (`docs/05-security-hipaa.md`).
- **Minimum-necessary / breach considerations:** the outbound text is
  deliberately minimal so a misdirected message discloses as little as possible.

## The ask
Advise us on the BAAs above and on our patient-consent language, and flag
anything else you'd want in place before a limited pilot with real patients.

*Prepared for internal use. The software is a working prototype on test data; nothing described here is live with patients yet.*
