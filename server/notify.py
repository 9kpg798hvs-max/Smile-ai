"""Scheduled notification generators: daily summary and unanswered reminders.

Both are idempotent (self-deduplicating), so the worker can call them every
tick without spamming anyone.
"""

import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Conversation,
    ConversationStatus,
    FollowUp,
    FollowUpStatus,
    Notification,
    Role,
    User,
    Visit,
    utcnow,
)


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def unanswered_reminders(
    db: Session,
    older_than: datetime.timedelta = datetime.timedelta(hours=2),
    now: datetime.datetime | None = None,
) -> int:
    """Remind the doctor about unread patient messages older than the window.

    One open reminder per conversation: no new reminder while an unread one
    exists for the same thread.
    """
    now = now or utcnow()
    cutoff = now - older_than
    convs = db.execute(
        select(Conversation).where(
            Conversation.status == ConversationStatus.OPEN,
            Conversation.unread_count > 0,
        )
    ).scalars().all()

    created = 0
    for conv in convs:
        if conv.last_message_at is None or _as_utc(conv.last_message_at) > cutoff:
            continue
        existing = db.execute(
            select(Notification).where(
                Notification.user_id == conv.doctor_id,
                Notification.type == "unanswered_reminder",
                Notification.read_at.is_(None),
            )
        ).scalars().all()
        if any(n.payload_json.get("conversation_id") == conv.id for n in existing):
            continue
        db.add(Notification(
            user_id=conv.doctor_id,
            type="unanswered_reminder",
            title="A patient message is still waiting for a reply",
            payload_json={"conversation_id": conv.id},
        ))
        created += 1
    return created


def daily_summaries(db: Session, now: datetime.datetime | None = None) -> int:
    """One per doctor per day: today's follow-up counts and unread messages."""
    now = now or utcnow()
    today = now.date()
    doctors = db.execute(select(User).where(User.role == Role.DOCTOR, User.is_active)).scalars().all()

    created = 0
    for doctor in doctors:
        already = db.execute(
            select(Notification).where(
                Notification.user_id == doctor.id,
                Notification.type == "daily_summary",
            )
        ).scalars().all()
        if any(n.payload_json.get("date") == today.isoformat() for n in already):
            continue

        rows = db.execute(
            select(FollowUp).join(Visit, FollowUp.visit_id == Visit.id).where(
                Visit.doctor_id == doctor.id, Visit.visit_date == today
            )
        ).scalars().all()
        by_status: dict[str, int] = {}
        for f in rows:
            by_status[f.status.value] = by_status.get(f.status.value, 0) + 1
        unread = db.execute(
            select(Conversation).where(
                Conversation.doctor_id == doctor.id, Conversation.unread_count > 0
            )
        ).scalars().all()

        db.add(Notification(
            user_id=doctor.id,
            type="daily_summary",
            title=f"Daily summary for {today.isoformat()}",
            payload_json={
                "date": today.isoformat(),
                "follow_ups_by_status": by_status,
                "unread_conversations": len(unread),
            },
        ))
        created += 1
    return created
