"""Role-based access control — the code form of docs/03-roles-and-permissions.md.

Scoping (own-patients, office access, tenancy) is enforced in the query layer
per-resource as those endpoints are built; this module answers "may this role
perform this action at all?" and handles the two practice-setting-gated
permissions.
"""

import enum

from .models import Role


class Permission(str, enum.Enum):
    MANAGE_PRACTICES = "manage_practices"  # platform-level (super admin)
    MANAGE_OFFICES = "manage_offices"
    MANAGE_USERS = "manage_users"
    MANAGE_PHONE_NUMBERS = "manage_phone_numbers"
    MANAGE_TEMPLATES = "manage_templates"
    CONFIGURE_SEND_TIMES = "configure_send_times"
    UPLOAD_SCHEDULE = "upload_schedule"
    REVIEW_OCR = "review_ocr"
    SUBMIT_FOR_APPROVAL = "submit_for_approval"
    APPROVE_PATIENT_LIST = "approve_patient_list"  # doctor only, own list
    VIEW_FOLLOW_UPS = "view_follow_ups"
    VIEW_INBOX = "view_inbox"
    EDIT_DRAFT = "edit_draft"
    SEND_REPLY = "send_reply"  # gated for non-doctors by practice setting
    OVERRIDE_CATEGORY = "override_category"
    MANAGE_CONVERSATION = "manage_conversation"  # assign/notes/archive/photos
    VIEW_ANALYTICS = "view_analytics"
    VIEW_DELIVERY_LOGS = "view_delivery_logs"
    VIEW_AUDIT_LOGS = "view_audit_logs"
    MANAGE_SECURITY = "manage_security"


_COMMON_CLINICAL = {
    Permission.UPLOAD_SCHEDULE,
    Permission.REVIEW_OCR,
    Permission.SUBMIT_FOR_APPROVAL,
    Permission.VIEW_FOLLOW_UPS,
    Permission.VIEW_INBOX,
    Permission.EDIT_DRAFT,
    Permission.OVERRIDE_CATEGORY,
    Permission.MANAGE_CONVERSATION,
}

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.SUPER_ADMIN: set(Permission) - {Permission.APPROVE_PATIENT_LIST, Permission.SEND_REPLY},
    Role.PRACTICE_ADMIN: (
        set(Permission)
        - {
            Permission.MANAGE_PRACTICES,
            Permission.APPROVE_PATIENT_LIST,
            Permission.SEND_REPLY,
        }
    ),
    Role.OFFICE_MANAGER: _COMMON_CLINICAL
    | {
        Permission.MANAGE_TEMPLATES,
        Permission.CONFIGURE_SEND_TIMES,
        Permission.VIEW_ANALYTICS,
        Permission.VIEW_DELIVERY_LOGS,
        # SEND_REPLY only via practice setting (see has_permission)
    },
    Role.DOCTOR: _COMMON_CLINICAL
    | {
        Permission.APPROVE_PATIENT_LIST,
        Permission.SEND_REPLY,
        Permission.MANAGE_TEMPLATES,  # own templates (scoped in query layer)
        Permission.CONFIGURE_SEND_TIMES,  # own settings
        Permission.VIEW_ANALYTICS,  # own patients
        Permission.VIEW_DELIVERY_LOGS,
    },
    Role.STAFF: set(_COMMON_CLINICAL),
}

# Permissions that non-doctor roles gain only when the practice enables
# settings_json["staff_can_send_replies"] (default False per SPEC.md §2.1).
_SETTING_GATED = {
    Role.OFFICE_MANAGER: {Permission.SEND_REPLY},
    Role.STAFF: {Permission.SEND_REPLY},
}


def has_permission(role: Role, permission: Permission, *, staff_can_send_replies: bool = False) -> bool:
    if permission in ROLE_PERMISSIONS.get(role, set()):
        return True
    if staff_can_send_replies and permission in _SETTING_GATED.get(role, set()):
        return True
    return False
