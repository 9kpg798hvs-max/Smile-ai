"""Inbox services: inbound SMS handling and human-approved reply sending."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai import classify_and_draft
from .audit import audit
from .models import (
    AIDraft,
    Conversation,
    DoctorProfile,
    DraftStatus,
    FollowUp,
    FollowUpStatus,
    Message,
    MessageDirection,
    MessageStatus,
    Notification,
    Office,
    Patient,
    PhoneNumber,
    ReplyClassification,
    Urgency,
    User,
    Visit,
    utcnow,
)
from .sms import SMSError

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}


class InboxError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


def _latest_open_follow_up(db: Session, patient_id: str, doctor_id: str) -> FollowUp | None:
    rows = db.execute(
        select(FollowUp, Visit)
        .join(Visit, FollowUp.visit_id == Visit.id)
        .where(
            Visit.patient_id == patient_id,
            Visit.doctor_id == doctor_id,
            FollowUp.status.in_([
                FollowUpStatus.SENT,
                FollowUpStatus.DELIVERED,
                FollowUpStatus.AWAITING_REPLY,
            ]),
        )
    ).all()
    if not rows:
        return None
    return max(rows, key=lambda r: r[0].sent_at or utcnow())[0]


def handle_inbound(
    db: Session,
    *,
    from_e164: str,
    to_e164: str,
    body: str,
    reply_extractor,
    draft_provider,
    provider_message_id: str | None = None,
) -> Message | None:
    """Inbound SMS → thread + message (+ classification + draft + alerts).

    Returns None for messages that can't be attributed to a practice number.
    """
    our_number = db.execute(
        select(PhoneNumber).where(PhoneNumber.e164 == to_e164)
    ).scalars().first()
    if our_number is None:
        return None  # not one of our numbers; ignore

    patient = db.execute(
        select(Patient).where(
            Patient.practice_id == our_number.practice_id,
            Patient.mobile_e164 == from_e164,
        )
    ).scalars().first()
    if patient is None:
        # Unknown sender texting a practice number: keep it visible, don't drop.
        patient = Patient(
            practice_id=our_number.practice_id,
            first_name="Unknown",
            last_name=f"Sender {from_e164[-4:]}",
            mobile_e164=from_e164,
        )
        db.add(patient)
        db.flush()

    # Route to the doctor: the number's doctor, else the doctor of the most
    # recent visit, else any doctor conversation this patient already has.
    doctor_id = our_number.doctor_id
    if doctor_id is None:
        last_visit = db.execute(
            select(Visit).where(Visit.patient_id == patient.id).order_by(Visit.visit_date.desc())
        ).scalars().first()
        doctor_id = last_visit.doctor_id if last_visit else None
    if doctor_id is None:
        conv = db.execute(
            select(Conversation).where(Conversation.patient_id == patient.id)
        ).scalars().first()
        doctor_id = conv.doctor_id if conv else None
    if doctor_id is None:
        return None  # nothing to attach to; provider-level logging only

    conv = db.execute(
        select(Conversation).where(
            Conversation.patient_id == patient.id, Conversation.doctor_id == doctor_id
        )
    ).scalars().first()
    if conv is None:
        conv = Conversation(
            practice_id=our_number.practice_id,
            patient_id=patient.id,
            doctor_id=doctor_id,
            office_id=our_number.office_id or "",
        )
        db.add(conv)
        db.flush()

    message = Message(
        practice_id=our_number.practice_id,
        conversation_id=conv.id,
        direction=MessageDirection.IN,
        body=body,
        status=MessageStatus.RECEIVED,
        provider=our_number.provider,
        provider_message_id=provider_message_id,
        from_e164=from_e164,
        to_e164=to_e164,
    )
    db.add(message)
    db.flush()
    conv.last_message_at = utcnow()
    conv.unread_count += 1

    # Regulatory STOP handling: record opt-out, cancel queued sends, no AI.
    if body.strip().lower() in STOP_WORDS:
        patient.sms_opt_out = True
        pending = db.execute(
            select(FollowUp).join(Visit, FollowUp.visit_id == Visit.id).where(
                Visit.patient_id == patient.id,
                FollowUp.status.in_([FollowUpStatus.PENDING, FollowUpStatus.READY_TO_SEND]),
            )
        ).scalars().all()
        for f in pending:
            f.status = FollowUpStatus.EXCLUDED
            f.failure_reason = "patient opted out (STOP)"
        audit(db, "patient.sms_opt_out", practice_id=patient.practice_id,
              entity_type="patient", entity_id=patient.id)
        return message

    doctor = db.get(User, doctor_id)
    office = db.get(Office, conv.office_id) if conv.office_id else None
    classification = classify_and_draft(
        db,
        message=message,
        patient=patient,
        doctor=doctor,
        office=office,
        reply_extractor=reply_extractor,
        draft_provider=draft_provider,
    )

    follow_up = _latest_open_follow_up(db, patient.id, doctor_id)
    urgent = classification.urgency is Urgency.RED
    if follow_up is not None:
        follow_up.status = (
            FollowUpStatus.NEEDS_ATTENTION if urgent else FollowUpStatus.REPLIED
        )

    db.add(Notification(
        user_id=doctor_id,
        type="urgent_reply" if urgent else "new_reply",
        title=(
            f"URGENT reply from {patient.first_name} {patient.last_name}"
            if urgent else f"New reply from {patient.first_name} {patient.last_name}"
        ),
        payload_json={
            "conversation_id": conv.id,
            "message_id": message.id,
            "category": classification.category.value,
            "urgency": classification.urgency.value,
        },
    ))
    if urgent:
        # High-priority symptom alert also goes to the assigned staff member.
        if conv.assigned_to and conv.assigned_to != doctor_id:
            db.add(Notification(
                user_id=conv.assigned_to,
                type="high_priority_symptom",
                title=f"Urgent symptoms reported by {patient.first_name} {patient.last_name}",
                payload_json={"conversation_id": conv.id, "message_id": message.id},
            ))
    return message


def approve_and_send_draft(
    db: Session,
    *,
    user: User,
    draft: AIDraft,
    sms_provider,
    edited_body: str | None = None,
) -> Message:
    """The ONLY path that turns an AI draft into an outbound SMS. Requires an
    approving human; records approver and sender on the message."""
    if draft.status is not DraftStatus.SUGGESTED:
        raise InboxError(409, f"draft already {draft.status.value}")

    inbound = db.get(Message, draft.message_id)
    conv = db.get(Conversation, inbound.conversation_id)
    patient = db.get(Patient, conv.patient_id)

    if patient.sms_opt_out:
        raise InboxError(409, "patient has opted out of SMS")

    body = (edited_body or draft.body).strip()
    if not body:
        raise InboxError(400, "reply body is empty")
    if edited_body and edited_body.strip() != draft.body.strip():
        draft.edited_body = edited_body.strip()  # doctor-voice training signal

    from .scheduler import resolve_sender

    visit = db.execute(
        select(Visit).where(
            Visit.patient_id == patient.id, Visit.doctor_id == conv.doctor_id
        ).order_by(Visit.visit_date.desc())
    ).scalars().first()
    sender_number = None
    if visit is not None:
        sender_number = resolve_sender(db, visit)
    if sender_number is None:
        sender_number = db.execute(
            select(PhoneNumber).where(PhoneNumber.practice_id == conv.practice_id)
        ).scalars().first()
    if sender_number is None:
        raise InboxError(409, "no sending phone number configured")

    out = Message(
        practice_id=conv.practice_id,
        conversation_id=conv.id,
        direction=MessageDirection.OUT,
        body=body,
        status=MessageStatus.QUEUED,
        provider=getattr(sms_provider, "name", "mock"),
        from_e164=sender_number.e164,
        to_e164=patient.mobile_e164,
        sent_by=user.id,
        approved_by=user.id,
    )
    db.add(out)
    db.flush()
    try:
        pmid = sms_provider.send(
            from_e164=sender_number.e164, to_e164=patient.mobile_e164, body=body
        )
    except SMSError as e:
        out.status = MessageStatus.FAILED
        out.failed_reason = str(e)[:300]
        raise InboxError(502, f"send failed: {e}")

    out.provider_message_id = pmid
    out.status = MessageStatus.SENT
    draft.status = DraftStatus.APPROVED
    draft.approved_by = user.id
    draft.sent_message_id = out.id
    conv.last_message_at = utcnow()
    audit(db, "reply.sent", user_id=user.id, practice_id=conv.practice_id,
          entity_type="message", entity_id=out.id,
          details={"draft_id": draft.id, "edited": bool(draft.edited_body)})
    return out
