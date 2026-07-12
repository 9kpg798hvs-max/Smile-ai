# 3. Database Schema

PostgreSQL in production, SQLite in dev/tests. Every tenant-owned table
carries `practice_id` (multi-practice SaaS-ready). All timestamps UTC.
Implemented in `server/models.py`; this document is the reference.

## Tenancy & people

| Table | Columns (key ones) | Notes |
|---|---|---|
| `practices` | id, name, settings_json (e.g. `staff_can_send_replies: false`), created_at | Tenant root |
| `offices` | id, practice_id, name, address, phone_display, timezone | Send times computed in office tz |
| `users` | id, practice_id (NULL = super admin), email UNIQUE, password_hash (scrypt), full_name, role, is_active, created_at | role ∈ super_admin, practice_admin, office_manager, doctor, staff |
| `sessions` | id (token hash), user_id, created_at, expires_at, ip, revoked_at | Server-side session store |
| `user_office_access` | user_id, office_id | Scopes non-admin users to offices |
| `doctor_profiles` | user_id PK→users, display_name ("Dr. Belani"), default_send_time (time), tone, signature, wider_access (bool) | Doctors see own patients unless wider_access |
| `phone_numbers` | id, practice_id, office_id?, doctor_id?, e164 UNIQUE, provider (`twilio`\|`mock`), kind (hosted\|dedicated), status, capabilities | Sender identity resolution: doctor → office → practice |

## Patients & visits

| Table | Columns | Notes |
|---|---|---|
| `patients` | id, practice_id, first_name, last_name, mobile_e164, language, sms_opt_out (bool), created_at | UNIQUE(practice_id, mobile_e164) soft — flagged, not blocked (family shared phones) |
| `visits` | id, practice_id, office_id, patient_id, doctor_id, visit_date, procedure, appointment_number?, source (`ocr`\|`manual`\|`import`), schedule_entry_id? | **UNIQUE(patient_id, doctor_id, visit_date, procedure)** = duplicate follow-up prevention (workflow req #7) |

## Schedule intake (OCR)

| Table | Columns | Notes |
|---|---|---|
| `schedule_uploads` | id, practice_id, office_id, uploaded_by, file_path, mime, schedule_date, status (`processing`→`extracted`→`in_review`→`submitted`→`approved`/`rejected`), ocr_model, ocr_prompt_version, approved_by?, approved_at?, created_at | Status is the approval state machine; **no sends until `approved`** |
| `schedule_entries` | id, upload_id, row_index, patient_name_raw, time_raw, doctor_name_raw, procedure_raw, phone_raw, ocr_crossed_out (bool), ocr_confidence (per-field json), excluded (bool), exclusion_source (`ocr`\|`staff`\|`doctor`), exclusion_reason, matched_patient_id?, resolved_doctor_id?, edited_by?, edited_at | `excluded` is the staff-overridable flag seeded from `ocr_crossed_out`; low-confidence fields flagged in UI |

## Follow-ups & messaging

| Table | Columns | Notes |
|---|---|---|
| `follow_ups` | id, practice_id, visit_id UNIQUE, status, template_id, rendered_body, scheduled_send_at, approved_by, approved_at, sent_at, failure_reason | status ∈ `pending`, `ready_to_send`, `sent`, `delivered`, `awaiting_reply`, `replied`, `needs_attention`, `failed`, `excluded` (exact required set) |
| `conversations` | id, practice_id, patient_id, doctor_id, office_id, status (`open`\|`archived`), assigned_to?, last_message_at, unread_count | One thread per patient(+doctor) |
| `messages` | id, practice_id, conversation_id, follow_up_id?, direction (`out`\|`in`), body, status, provider (`twilio`\|`mock`), provider_message_id, from_e164, to_e164, sent_by?, approved_by?, created_at, delivered_at, failed_reason | Complete communication history; who approved/sent each reply |
| `delivery_events` | id, message_id, event (`queued`\|`sent`\|`delivered`\|`failed`\|`undelivered`), provider_payload_json, occurred_at | Delivery log screen |
| `attachments` | id, practice_id, conversation_id, message_id?, file_path, content_type, uploaded_by, created_at | Patient photos |
| `internal_notes` | id, conversation_id, author_id, body, created_at | Inbox notes |

## AI

| Table | Columns | Notes |
|---|---|---|
| `reply_classifications` | id, message_id UNIQUE (inbound), category, urgency (`red`\|`yellow`\|`green`), confidence, findings_json, model, prompt_version, override_category?, override_by?, override_at | category ∈ doing_well, pain, swelling, medication_question, emergency, appointment_request, question_or_mild_concern, other. Overrides preserved = training/eval data |
| `ai_drafts` | id, message_id (the inbound reply), body, edited_body?, status (`suggested`\|`approved`\|`discarded`), approved_by?, sent_message_id? | `edited_body` vs `body` = doctor-voice training signal (SPEC §5.3) |

## Templates, notifications, audit

| Table | Columns | Notes |
|---|---|---|
| `templates` | id, practice_id, name, body, tone (`formal`\|`friendly`\|`custom`), doctor_id?, office_id?, procedure?, is_default, updated_by | Placeholders: `{first_name} {doctor_name} {office_name} {procedure} {treatment_date} {office_phone}` |
| `notifications` | id, user_id, type, title, payload_json, read_at, created_at | Types per requirements (new reply, urgent, symptom alert, failed message, approval required, daily summary, unanswered reminder) |
| `audit_log` | id, practice_id?, user_id?, action, entity_type, entity_id, details_json, ip, created_at | Append-only; written by middleware + service layer |

## State machines

```
schedule_upload: processing → extracted → in_review → submitted → approved | rejected
follow_up:       pending → ready_to_send → sent → delivered → awaiting_reply → replied
                     └→ excluded                    └→ failed        └→ needs_attention
message(out):    queued → sent → delivered | failed
```

`follow_ups.status` transitions are service-enforced: `ready_to_send` is only
reachable via doctor approval of the upload; `sent` only via the scheduler.
