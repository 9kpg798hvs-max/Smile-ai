"""Notification center endpoints (screen 15)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import Notification, User, utcnow

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    unread_only: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    rows = db.execute(query.order_by(Notification.created_at.desc())).scalars().all()
    return [
        {"id": n.id, "type": n.type, "title": n.title, "payload": n.payload_json,
         "read": n.read_at is not None, "created_at": n.created_at.isoformat()}
        for n in rows
    ]


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    n.read_at = utcnow()
    return {"ok": True}


@router.post("/read-all")
def mark_all_read(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Notification).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    ).scalars().all()
    for n in rows:
        n.read_at = utcnow()
    return {"ok": True, "marked": len(rows)}
