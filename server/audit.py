"""Append-only audit trail helper. Call from every mutating service path."""

from sqlalchemy.orm import Session

from .models import AuditLog


def audit(
    db: Session,
    action: str,
    *,
    user_id: str | None = None,
    practice_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> AuditLog:
    row = AuditLog(
        action=action,
        user_id=user_id,
        practice_id=practice_id,
        entity_type=entity_type,
        entity_id=entity_id,
        details_json=details or {},
        ip=ip,
    )
    db.add(row)
    return row
