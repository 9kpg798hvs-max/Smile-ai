# 5–6. Screen Inventory & User Flows

## 5. Screen inventory (21 required screens)

| # | Screen | Route | Primary roles | Phase |
|---|---|---|---|---|
| 1 | Login | `/login` | all | 1 |
| 2 | Clinic & office setup | `/admin/practice` | admins | 5 |
| 3 | Doctor & staff management | `/admin/users` | admins | 5 |
| 4 | Schedule upload | `/intake/upload` | staff+ | 2 |
| 5 | OCR processing & validation | `/intake/{uploadId}/review` | staff+ | 2 |
| 6 | Editable patient confirmation | same screen, edit mode | staff+ | 2 |
| 7 | Doctor approval | `/approvals` | doctor | 3 |
| 8 | Today's follow-up dashboard | `/` (home) | all | 3/6 |
| 9 | Scheduled message queue | `/queue` | staff+, doctor | 3 |
| 10 | Central reply inbox | `/inbox` | all | 4 |
| 11 | Conversation thread | `/inbox/{conversationId}` | all | 4 |
| 12 | Urgent / Needs Attention queue | `/inbox?urgency=red` (pinned view) | all | 4 |
| 13 | Message template editor | `/templates` | managers+ | 5 |
| 14 | Doctor send-time settings | `/settings/doctors` | doctor, admins | 5 |
| 15 | Notification center | `/notifications` | all | 5 |
| 16 | Analytics & reporting | `/analytics` | managers+ | 6 |
| 17 | SMS & phone-number configuration | `/admin/phone-numbers` | admins | 5 |
| 18 | User & permission management | `/admin/users` (roles tab) | admins | 5 |
| 19 | Delivery logs | `/logs/delivery` | managers+ | 6 |
| 20 | Audit logs | `/admin/audit` | admins | 6 |
| 21 | Security & backup settings | `/admin/security` | admins | 7 |

**Main dashboard tiles (screen 8):** Today's follow-ups · Pending doctor
approval · Ready to Send · Sent · Delivered · Awaiting Reply · Replied ·
Needs Attention · Unread messages · Urgent replies · Pain replies · Swelling
replies · Medication questions · Patients doing well. Each tile deep-links to
the filtered queue/inbox view behind it.

## 6. User flows (text)

### Flow A — Daily intake → send (the core loop)

```
Staff logs in
 → Schedule Upload: drops photo/screenshot/PDF of today's day sheet
 → system stores file, calls OCR (Claude vision, structured output)
 → OCR Review screen:
     rows = extracted patients (name, time, doctor, procedure, phone)
     crossed-out rows arrive pre-marked EXCLUDED (source: ocr) — badge shown
     low-confidence fields highlighted (esp. phone digits — SPEC §5.4 risk)
 → Staff edits/corrects/adds/removes rows; toggles exclusions (override OCR)
 → Staff clicks "Submit to Dr. {name}" → upload status = submitted
 → Doctor (notified) opens Approval screen:
     final list + rendered message preview per patient (template + placeholders)
     doctor may exclude individual patients, then Approve (or Reject w/ note)
 → On approval:
     for each included entry: create visit (UNIQUE constraint silently skips
     duplicates and marks them "excluded — duplicate") + follow_up(ready_to_send,
     scheduled_send_at = doctor's send time in office tz)
 → Scheduler at send time: sends via SMS adapter → status sent → delivered
     (webhook) → awaiting_reply
 ✗ No message can send from any other path.
```

### Flow B — Reply → triaged inbox → human-approved response

```
Patient replies to the check-in SMS
 → inbound webhook (provider-signed) matches phone → conversation, message(in)
 → AI pipeline (existing triage engine, extended):
     extract clinical findings → deterministic urgency (red/yellow/green)
     + category (8 SmileFlow categories) + confidence
     + draft response in doctor's tone (draft NEVER auto-sends)
 → red urgency (Emergency / swelling / severe / uncontrolled / airway /
   allergic-reaction language) → immediate alert notifications to doctor
   + on-screen Urgent queue; conversation marked needs_attention
 → Inbox: thread shows reply, AI category + confidence, urgency, draft
 → Doctor (or ⚙-authorized staff) edits draft → "Approve & send"
     — red-urgency drafts are escalation-only wording (SPEC §2.4)
     — user can override a wrong AI category (recorded, feeds evals)
 → sent message records approved_by / sent_by; thread updated; audit row
```

### Flow C — Template & send-time management

```
Admin/manager creates templates (per doctor / office / procedure / tone)
 → placeholder preview with sample data → save (versioned by updated_by/audit)
Doctor sets their send time (e.g. 18:30) → scheduler uses it per office tz
```

### Flow D — Exception paths

```
Send failure → follow_up failed + notification + Delivery Log entry → manual retry
Patient texts STOP → sms_opt_out=true, all queued sends for patient excluded, audit row
Unanswered urgent reply > N minutes → escalating reminder notification
OCR total failure → upload flagged, staff enters patients manually (same review screen)
Duplicate upload of same day sheet → dedup constraint excludes repeats, staff sees "already followed up"
```
