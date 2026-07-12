# 4. Role & Permission Matrix

Implemented in `server/rbac.py` as data; every API route declares required
permissions; the matrix below is the source of truth. ✓ = allowed,
"own" = only for their own patients/offices, ⚙ = allowed if the practice
setting or per-user grant enables it.

| Permission | Super Admin | Practice Admin | Office Manager | Doctor | Staff/Front Desk |
|---|:-:|:-:|:-:|:-:|:-:|
| Manage practices (create/suspend tenants) | ✓ | – | – | – | – |
| Manage offices | ✓ | ✓ | – | – | – |
| Manage users & roles | ✓ | ✓ | – | – | – |
| Manage phone numbers / SMS config | ✓ | ✓ | – | – | – |
| Manage templates | ✓ | ✓ | ✓ | own | – |
| Configure doctor send times | ✓ | ✓ | ✓ | own | – |
| Upload schedule | ✓ | ✓ | ✓ | ✓ | ✓ |
| Review/edit/exclude OCR entries | ✓ | ✓ | ✓ | ✓ | ✓ |
| Submit list for doctor approval | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Approve final patient list** | – | – | – | **✓ (own list)** | – |
| View follow-up dashboard & queue | ✓ | ✓ | ✓ (office) | own | ✓ (office) |
| View inbox / conversations | ✓ | ✓ | ✓ (office) | own¹ | ✓ (office) |
| Edit AI draft | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Approve & send reply** | – | – | ⚙ | ✓ | ⚙ |
| Override AI category | ✓ | ✓ | ✓ | ✓ | ✓ |
| Assign conversations / add notes / archive | ✓ | ✓ | ✓ | ✓ | ✓ |
| Upload patient photos | ✓ | ✓ | ✓ | ✓ | ✓ |
| View analytics | ✓ | ✓ | ✓ (office) | own | – |
| View delivery logs | ✓ | ✓ | ✓ (office) | own | – |
| View audit logs | ✓ | ✓ | – | – | – |
| Security & backup settings | ✓ | ✓ | – | – | – |

¹ Doctors see their own patients by default; `doctor_profiles.wider_access`
grants practice-wide visibility (requirement: "unless granted wider access").

⚙ "Approve & send reply" for non-doctors is off by default
(`practices.settings.staff_can_send_replies = false`) per SPEC.md §2.1; a
Practice Admin can enable it, and every send records `approved_by` + `sent_by`
in `messages` and the audit log regardless.

Cross-cutting rules:

- **Tenancy:** non-super-admin users can never read or write outside their
  `practice_id`; enforced in the query layer, not per-route.
- **Office scoping:** office managers and staff are limited to offices in
  `user_office_access`.
- **Minimum necessary:** roles grant the smallest set above; wider access is
  an explicit, audited grant.
- **Audit:** every permission-checked mutation writes an `audit_log` row
  (who, what, when, from where).
