"""Send scheduler: the ONLY code path that creates outbound patient SMS for
check-ins. It exclusively consumes follow_ups in READY_TO_SEND — a status
reachable only through doctor approval (server/approval.py) — which is what
makes "no messages until the doctor approves" a structural property rather
than a UI promise.
"""

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import audit
from .models import (
    Conversation,
    DeliveryEvent,
    FollowUp,
    FollowUpStatus,
    Message,
    MessageDirection,
    MessageStatus,
    Notification,
    Patient,
    PhoneNumber,
    Visit,
    utcnow,
)
from .sms import SMSError

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def resolve_sender(db: Session, visit: Visit) -> PhoneNumber | None:
    """Doctor's number, else the office's, else any practice number."""
    numbers = db.execute(
        select(PhoneNumber).where(
            PhoneNumber.practice_id == visit.practice_id,
            PhoneNumber.status == "active",
        )
    ).scalars().all()
    for pick in (
        lambda n: n.doctor_id == visit.doctor_id,
        lambda n: n.office_id == visit.office_id and n.doctor_id is None,
        lambda n: n.doctor_id is None and n.office_id is None,
    ):
        for n in numbers:
            if pick(n):
                return n
    return numbers[0] if numbers else None


def _conversation_for(db: Session, visit: Visit) -> Conversation:
    conv = db.execute(
        select(Conversation).where(
            Conversation.patient_id == visit.patient_id,
            Conversation.doctor_id == visit.doctor_id,
        )
    ).scalars().first()
    if conv is None:
        conv = Conversation(
            practice_id=visit.practice_id,
            patient_id=visit.patient_id,
            doctor_id=visit.doctor_id,
            office_id=visit.office_id,
        )
        db.add(conv)
        db.flush()
    return conv


def run_pending(db: Session, sms_provider, now: datetime.datetime | None = None) -> dict:
    """Send every due follow-up. Called by the worker loop and by tests."""
    now = now or utcnow()
    due = db.execute(
        select(FollowUp).where(FollowUp.status == FollowUpStatus.READY_TO_SEND)
    ).scalars().all()
    due = [f for f in due if f.scheduled_send_at and _as_utc(f.scheduled_send_at) <= now]

    sent, failed = 0, 0
    for follow_up in due:
        visit = db.get(Visit, follow_up.visit_id)
        patient = db.get(Patient, visit.patient_id)
        if patient.sms_opt_out:
            follow_up.status = FollowUpStatus.EXCLUDED
            follow_up.failure_reason = "patient opted out of SMS"
            continue
        sender = resolve_sender(db, visit)
        if sender is None:
            follow_up.status = FollowUpStatus.FAILED
            follow_up.failure_reason = "no sending phone number configured"
            failed += 1
            _notify_failure(db, visit, follow_up)
            continue

        conv = _conversation_for(db, visit)
        message = Message(
            practice_id=visit.practice_id,
            conversation_id=conv.id,
            follow_up_id=follow_up.id,
            direction=MessageDirection.OUT,
            body=follow_up.rendered_body or "",
            status=MessageStatus.QUEUED,
            provider=getattr(sms_provider, "name", "mock"),
            from_e164=sender.e164,
            to_e164=patient.mobile_e164,
            approved_by=follow_up.approved_by,  # the doctor who approved the list
        )
        db.add(message)
        db.flush()
        try:
            pmid = sms_provider.send(
                from_e164=sender.e164, to_e164=patient.mobile_e164, body=message.body
            )
        except SMSError as e:
            message.status = MessageStatus.FAILED
            message.failed_reason = str(e)[:300]
            follow_up.status = FollowUpStatus.FAILED
            follow_up.failure_reason = str(e)[:300]
            db.add(DeliveryEvent(message_id=message.id, event="failed",
                                 provider_payload_json={"error": str(e)[:300]}))
            _notify_failure(db, visit, follow_up)
            failed += 1
            continue

        message.provider_message_id = pmid
        message.status = MessageStatus.SENT
        follow_up.status = FollowUpStatus.SENT
        follow_up.sent_at = now
        conv.last_message_at = now
        db.add(DeliveryEvent(message_id=message.id, event="sent", provider_payload_json={}))
        audit(db, "message.sent", user_id=follow_up.approved_by, practice_id=visit.practice_id,
              entity_type="message", entity_id=message.id,
              details={"follow_up_id": follow_up.id})
        sent += 1

    return {"sent": sent, "failed": failed, "due": len(due)}


def _notify_failure(db: Session, visit: Visit, follow_up: FollowUp) -> None:
    db.add(Notification(
        user_id=visit.doctor_id,
        type="message_failed",
        title="A follow-up message failed to send",
        payload_json={"follow_up_id": follow_up.id, "reason": follow_up.failure_reason},
    ))


def apply_delivery_status(db: Session, provider_message_id: str, event: str, payload: dict) -> bool:
    """Provider status webhook → message/follow-up state + delivery log."""
    message = db.execute(
        select(Message).where(Message.provider_message_id == provider_message_id)
    ).scalars().first()
    if message is None:
        return False
    db.add(DeliveryEvent(message_id=message.id, event=event, provider_payload_json=payload))
    follow_up = db.get(FollowUp, message.follow_up_id) if message.follow_up_id else None
    if event == "delivered":
        message.status = MessageStatus.DELIVERED
        message.delivered_at = utcnow()
        if follow_up and follow_up.status is FollowUpStatus.SENT:
            # AWAITING_REPLY / REPLIED transitions happen on inbound (Phase 4).
            follow_up.status = FollowUpStatus.DELIVERED
    elif event in ("failed", "undelivered"):
        message.status = MessageStatus.FAILED
        message.failed_reason = str(payload.get("error", event))[:300]
        if follow_up:
            follow_up.status = FollowUpStatus.FAILED
            follow_up.failure_reason = message.failed_reason
            visit = db.get(Visit, follow_up.visit_id)
            _notify_failure(db, visit, follow_up)
    return True
