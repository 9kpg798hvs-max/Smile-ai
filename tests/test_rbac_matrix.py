"""Parametrized enforcement of the docs/03 role/permission matrix.

If the matrix doc and the code drift apart, this file is the tiebreaker —
update both together.
"""

import pytest

from server.models import Role
from server.rbac import Permission, has_permission

# (permission, {role: expected}) — mirrors docs/03-roles-and-permissions.md.
MATRIX = {
    Permission.MANAGE_PRACTICES: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: False, Role.OFFICE_MANAGER: False,
        Role.DOCTOR: False, Role.STAFF: False,
    },
    Permission.MANAGE_OFFICES: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: True, Role.OFFICE_MANAGER: False,
        Role.DOCTOR: False, Role.STAFF: False,
    },
    Permission.MANAGE_USERS: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: True, Role.OFFICE_MANAGER: False,
        Role.DOCTOR: False, Role.STAFF: False,
    },
    Permission.MANAGE_PHONE_NUMBERS: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: True, Role.OFFICE_MANAGER: False,
        Role.DOCTOR: False, Role.STAFF: False,
    },
    Permission.UPLOAD_SCHEDULE: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: True, Role.OFFICE_MANAGER: True,
        Role.DOCTOR: True, Role.STAFF: True,
    },
    Permission.REVIEW_OCR: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: True, Role.OFFICE_MANAGER: True,
        Role.DOCTOR: True, Role.STAFF: True,
    },
    Permission.APPROVE_PATIENT_LIST: {
        # Doctor-only. Even admins cannot approve a clinical list.
        Role.SUPER_ADMIN: False, Role.PRACTICE_ADMIN: False, Role.OFFICE_MANAGER: False,
        Role.DOCTOR: True, Role.STAFF: False,
    },
    Permission.SEND_REPLY: {
        # Default practice settings: doctor-only (SPEC.md §2.1).
        Role.SUPER_ADMIN: False, Role.PRACTICE_ADMIN: False, Role.OFFICE_MANAGER: False,
        Role.DOCTOR: True, Role.STAFF: False,
    },
    Permission.EDIT_DRAFT: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: True, Role.OFFICE_MANAGER: True,
        Role.DOCTOR: True, Role.STAFF: True,
    },
    Permission.OVERRIDE_CATEGORY: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: True, Role.OFFICE_MANAGER: True,
        Role.DOCTOR: True, Role.STAFF: True,
    },
    Permission.VIEW_ANALYTICS: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: True, Role.OFFICE_MANAGER: True,
        Role.DOCTOR: True, Role.STAFF: False,
    },
    Permission.VIEW_AUDIT_LOGS: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: True, Role.OFFICE_MANAGER: False,
        Role.DOCTOR: False, Role.STAFF: False,
    },
    Permission.MANAGE_SECURITY: {
        Role.SUPER_ADMIN: True, Role.PRACTICE_ADMIN: True, Role.OFFICE_MANAGER: False,
        Role.DOCTOR: False, Role.STAFF: False,
    },
}


@pytest.mark.parametrize(
    "permission,role,expected",
    [(perm, role, exp) for perm, row in MATRIX.items() for role, exp in row.items()],
    ids=lambda v: getattr(v, "value", str(v)),
)
def test_matrix(permission, role, expected):
    assert has_permission(role, permission) is expected


def test_staff_send_reply_unlocked_by_practice_setting():
    assert not has_permission(Role.STAFF, Permission.SEND_REPLY)
    assert has_permission(Role.STAFF, Permission.SEND_REPLY, staff_can_send_replies=True)
    assert has_permission(Role.OFFICE_MANAGER, Permission.SEND_REPLY, staff_can_send_replies=True)
    # The setting unlocks sending for clinic staff roles only.
    assert not has_permission(Role.PRACTICE_ADMIN, Permission.SEND_REPLY, staff_can_send_replies=True)
