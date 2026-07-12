# 9. HIPAA & Security Checklist

Status legend: ☐ open · ◐ partial/in progress · ☑ done (only when implemented & tested)

## Administrative / organizational (owner: practice + vendor contracts)

- ☐ **BAA with Anthropic** (Claude API: OCR, triage, drafting) before any real PHI
- ☐ **BAA with Twilio** + A2P 10DLC healthcare registration before go-live
- ☐ **BAA with hosting provider** (AWS/GCP/Azure HIPAA-eligible services only)
- ☐ BAA or PHI-free design for email/notification provider
- ☐ Vendor inventory doc: every vendor touching PHI, its BAA status, data flows
- ☐ Workforce access policy: minimum necessary, role assignments reviewed
- ☐ Incident response & breach notification procedure
- ☐ Data retention & disposal policy (messages, uploads, backups)

## Technical — application (owner: this codebase)

- ◐ Role-based access control on every endpoint (matrix in docs/03) — *Phase 1*
- ◐ Secure authentication: scrypt password hashing, server-side revocable
  sessions, httpOnly/secure/SameSite cookies, lockout on repeated failures — *Phase 1*
- ☐ Session timeout + re-auth for admin actions
- ☐ Optional TOTP 2FA (Phase 7)
- ◐ Tenant isolation: practice_id scoping in the query layer — *Phase 1*
- ◐ Audit logging: append-only who/what/when/where for every PHI access &
  mutation, viewable on screen 20 — *Phase 1 (write path)*
- ☐ Webhook signature verification (Twilio) + replay protection
- ☐ Rate limiting on auth + webhook endpoints
- ☐ Input validation everywhere (pydantic at the boundary)
- ☐ No PHI in application logs, error messages, or URLs
- ☐ Outbound check-in contains no PHI by design (name + "checking on you" only)
- ☐ File uploads: type/size validation, virus-scan hook, stored outside web root
- ☐ AI hard rules: never auto-send; never diagnose (escalation-only red drafts);
  category override always available — *classifier gate already enforced in Phase 0*

## Technical — infrastructure (owner: deployment)

- ☐ TLS 1.2+ everywhere (encryption in transit)
- ☐ Encryption at rest: DB volume + file storage + backups
- ☐ Secrets in a secrets manager / env, never in the repo
- ☐ Network: DB not publicly reachable; least-privilege security groups
- ☐ Automated encrypted backups (daily) + tested restore procedure (screen 21
  shows last backup / last restore-test)
- ☐ Environment separation: dev/test environments contain **mock data only**
  (enforced culturally + by the gitignored `data/` convention from Phase 0)
- ☐ Monitoring & alerting on auth anomalies, failed webhooks, queue stalls

## Development-time rules (already in force)

- ☑ No real patient data in the repository (`data/`, results, caches gitignored)
- ☑ Mock SMS + mock extractor used by the entire test suite
- ☑ De-identification scrubber available for any corpus work
- ◐ Every mocked integration labeled `MOCK` in code and UI
