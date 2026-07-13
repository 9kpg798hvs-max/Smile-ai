"""Reporting endpoints: dashboard tiles (screen 8), analytics (screen 16),
delivery logs (screen 19), audit logs (screen 20).

Counts are computed straight from the source tables at request time — at
25 patients/day there is nothing to pre-aggregate, and every tile is
guaranteed to match the queue/inbox view behind it.
"""

import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_db, require
from ..models import (
    AuditLog,
    Conversation,
    DeliveryEvent,
    FollowUp,
    FollowUpStatus,
    Message,
    MessageDirection,
    Patient,
    ReplyCategory,
    ReplyClassification,
    ScheduleUpload,
    UploadStatus,
    Urgency,
    User,
    Visit,
)
from ..rbac import Permission

router = APIRouter(prefix="/api/v1", tags=["reporting"])


def _practice_filter(query, model, user: User):
    if user.practice_id is not None:
        query = query.where(model.practice_id == user.practice_id)
    return query


def _classification_for(db: Session, message_id: str) -> ReplyClassification | None:
    return db.execute(
        select(ReplyClassification).where(ReplyClassification.message_id == message_id)
    ).scalars().first()


@router.get("/dashboard")
def dashboard(
    date: datetime.date | None = None,
    user: User = Depends(require(Permission.VIEW_FOLLOW_UPS)),
    db: Session = Depends(get_db),
):
    """All 14 required tiles. Each maps 1:1 to a filtered screen."""
    today = date or datetime.date.today()

    fups = db.execute(
        _practice_filter(
            select(FollowUp, Visit).join(Visit, FollowUp.visit_id == Visit.id)
            .where(Visit.visit_date == today),
            FollowUp, user,
        )
    ).all()
    by_status: dict[str, int] = {s.value: 0 for s in FollowUpStatus}
    for f, _ in fups:
        by_status[f.status.value] += 1

    pending_approval = db.execute(
        _practice_filter(
            select(ScheduleUpload).where(ScheduleUpload.status == UploadStatus.SUBMITTED),
            ScheduleUpload, user,
        )
    ).scalars().all()

    convs = db.execute(
        _practice_filter(select(Conversation), Conversation, user)
    ).scalars().all()
    unread = sum(1 for c in convs if c.unread_count > 0)

    # Latest inbound classification per conversation drives the reply tiles.
    urgent = pain = swelling = medication = doing_well = 0
    for conv in convs:
        inbound = db.execute(
            select(Message).where(
                Message.conversation_id == conv.id,
                Message.direction == MessageDirection.IN,
            ).order_by(Message.created_at.desc())
        ).scalars().first()
        if inbound is None:
            continue
        cls = _classification_for(db, inbound.id)
        if cls is None:
            continue
        category = cls.override_category or cls.category
        if cls.urgency is Urgency.RED:
            urgent += 1
        if category is ReplyCategory.PAIN:
            pain += 1
        elif category is ReplyCategory.SWELLING:
            swelling += 1
        elif category is ReplyCategory.MEDICATION_QUESTION:
            medication += 1
        elif category is ReplyCategory.DOING_WELL:
            doing_well += 1

    return {
        "date": today.isoformat(),
        "todays_follow_ups": len(fups),
        "pending_doctor_approval": len(pending_approval),
        "ready_to_send": by_status["ready_to_send"],
        "sent": by_status["sent"],
        "delivered": by_status["delivered"],
        "awaiting_reply": by_status["awaiting_reply"] + by_status["delivered"],
        "replied": by_status["replied"],
        "needs_attention": by_status["needs_attention"],
        "unread_messages": unread,
        "urgent_replies": urgent,
        "pain_replies": pain,
        "swelling_replies": swelling,
        "medication_questions": medication,
        "patients_doing_well": doing_well,
    }


@router.get("/analytics/summary")
def analytics_summary(
    days: int = Query(default=30, ge=1, le=365),
    office_id: str | None = None,
    doctor_id: str | None = None,
    procedure: str | None = None,
    user: User = Depends(require(Permission.VIEW_ANALYTICS)),
    db: Session = Depends(get_db),
):
    since_date = datetime.date.today() - datetime.timedelta(days=days)

    rows = db.execute(
        _practice_filter(
            select(FollowUp, Visit).join(Visit, FollowUp.visit_id == Visit.id)
            .where(Visit.visit_date >= since_date),
            FollowUp, user,
        )
    ).all()
    if office_id:
        rows = [(f, v) for f, v in rows if v.office_id == office_id]
    if doctor_id:
        rows = [(f, v) for f, v in rows if v.doctor_id == doctor_id]
    if procedure:
        rows = [(f, v) for f, v in rows if procedure.lower() in (v.procedure or "").lower()]

    attempted = [
        (f, v) for f, v in rows
        if f.status not in (FollowUpStatus.PENDING, FollowUpStatus.READY_TO_SEND,
                            FollowUpStatus.EXCLUDED)
    ]
    delivered_like = [
        (f, v) for f, v in attempted if f.status is not FollowUpStatus.FAILED
    ]
    replied = [
        (f, v) for f, v in rows
        if f.status in (FollowUpStatus.REPLIED, FollowUpStatus.NEEDS_ATTENTION)
    ]

    # Response time: first inbound after the check-in went out.
    response_minutes: list[float] = []
    category_counts: dict[str, int] = {}
    urgent_count = 0
    for f, v in rows:
        if f.sent_at is None:
            continue
        conv = db.execute(
            select(Conversation).where(
                Conversation.patient_id == v.patient_id,
                Conversation.doctor_id == v.doctor_id,
            )
        ).scalars().first()
        if conv is None:
            continue
        inbound = db.execute(
            select(Message).where(
                Message.conversation_id == conv.id,
                Message.direction == MessageDirection.IN,
            ).order_by(Message.created_at)
        ).scalars().first()
        if inbound is None:
            continue
        sent_at = f.sent_at if f.sent_at.tzinfo else f.sent_at.replace(
            tzinfo=datetime.timezone.utc)
        got_at = inbound.created_at if inbound.created_at.tzinfo else inbound.created_at.replace(
            tzinfo=datetime.timezone.utc)
        if got_at >= sent_at:
            response_minutes.append((got_at - sent_at).total_seconds() / 60)
        cls = _classification_for(db, inbound.id)
        if cls:
            category = (cls.override_category or cls.category).value
            category_counts[category] = category_counts.get(category, 0) + 1
            if cls.urgency is Urgency.RED:
                urgent_count += 1

    total_replies = sum(category_counts.values())
    return {
        "window_days": days,
        "filters": {"office_id": office_id, "doctor_id": doctor_id, "procedure": procedure},
        "follow_ups": len(rows),
        "delivery_rate": (len(delivered_like) / len(attempted)) if attempted else None,
        "response_rate": (len(replied) / len(attempted)) if attempted else None,
        "avg_response_minutes": (
            sum(response_minutes) / len(response_minutes) if response_minutes else None
        ),
        "urgent_cases": urgent_count,
        "reply_categories_pct": {
            k: round(100 * n / total_replies, 1) for k, n in sorted(category_counts.items())
        } if total_replies else {},
        "awaiting_response": sum(
            1 for f, _ in rows
            if f.status in (FollowUpStatus.SENT, FollowUpStatus.DELIVERED,
                            FollowUpStatus.AWAITING_REPLY)
        ),
    }


@router.get("/logs/delivery")
def delivery_logs(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(require(Permission.VIEW_DELIVERY_LOGS)),
    db: Session = Depends(get_db),
):
    events = db.execute(
        select(DeliveryEvent, Message).join(Message, DeliveryEvent.message_id == Message.id)
        .order_by(DeliveryEvent.occurred_at.desc()).limit(limit * 3)
    ).all()
    out = []
    for event, message in events:
        if user.practice_id is not None and message.practice_id != user.practice_id:
            continue
        out.append({
            "message_id": message.id,
            "event": event.event,
            "to": message.to_e164,
            "provider": message.provider,
            "provider_message_id": message.provider_message_id,
            "occurred_at": event.occurred_at.isoformat(),
            "detail": event.provider_payload_json,
        })
        if len(out) >= limit:
            break
    return out


@router.get("/audit-log")
def audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = None,
    user_id: str | None = None,
    user: User = Depends(require(Permission.VIEW_AUDIT_LOGS)),
    db: Session = Depends(get_db),
):
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if user.practice_id is not None:
        query = query.where(AuditLog.practice_id == user.practice_id)
    if action:
        query = query.where(AuditLog.action == action)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    return [
        {
            "id": a.id, "action": a.action, "user_id": a.user_id,
            "entity_type": a.entity_type, "entity_id": a.entity_id,
            "details": a.details_json, "ip": a.ip,
            "created_at": a.created_at.isoformat(),
        }
        for a in db.execute(query).scalars()
    ]
