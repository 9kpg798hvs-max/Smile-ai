"""Inbox endpoints (screens 10–12): threads, filters, drafts, urgent queue."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import inbox as inbox_service
from ..audit import audit
from ..deps import check_tenancy, get_current_user, get_db, require
from ..models import (
    AIDraft,
    Conversation,
    ConversationStatus,
    DoctorProfile,
    InternalNote,
    Message,
    MessageDirection,
    Patient,
    ReplyCategory,
    ReplyClassification,
    Role,
    Urgency,
    User,
    utcnow,
)
from ..rbac import Permission

router = APIRouter(prefix="/api/v1", tags=["inbox"])


class NoteBody(BaseModel):
    body: str


class AssignBody(BaseModel):
    user_id: str | None  # None = unassign


class OverrideBody(BaseModel):
    category: ReplyCategory


class ApproveSendBody(BaseModel):
    edited_body: str | None = None


def _doctor_scope_ok(db: Session, user: User, conv: Conversation) -> bool:
    """Doctors see their own patients unless granted wider access (docs/03)."""
    if user.role is not Role.DOCTOR:
        return True
    if conv.doctor_id == user.id:
        return True
    profile = db.get(DoctorProfile, user.id)
    return bool(profile and profile.wider_access)


def _latest_inbound_with_ai(db: Session, conv: Conversation):
    inbound = db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.direction == MessageDirection.IN)
        .order_by(Message.created_at.desc())
    ).scalars().first()
    if inbound is None:
        return None, None
    classification = db.execute(
        select(ReplyClassification).where(ReplyClassification.message_id == inbound.id)
    ).scalars().first()
    return inbound, classification


@router.get("/conversations")
def list_conversations(
    doctor_id: str | None = None,
    office_id: str | None = None,
    category: ReplyCategory | None = None,
    urgency: Urgency | None = None,
    unread_only: bool = False,
    archived: bool = False,
    q: str | None = None,
    user: User = Depends(require(Permission.VIEW_INBOX)),
    db: Session = Depends(get_db),
):
    query = select(Conversation).where(
        Conversation.status
        == (ConversationStatus.ARCHIVED if archived else ConversationStatus.OPEN)
    )
    if user.practice_id is not None:
        query = query.where(Conversation.practice_id == user.practice_id)
    if doctor_id:
        query = query.where(Conversation.doctor_id == doctor_id)
    if office_id:
        query = query.where(Conversation.office_id == office_id)
    if unread_only:
        query = query.where(Conversation.unread_count > 0)
    convs = db.execute(query.order_by(Conversation.last_message_at.desc())).scalars().all()

    out = []
    for conv in convs:
        if not _doctor_scope_ok(db, user, conv):
            continue
        patient = db.get(Patient, conv.patient_id)
        if q and q.lower() not in f"{patient.first_name} {patient.last_name}".lower():
            continue
        inbound, classification = _latest_inbound_with_ai(db, conv)
        # Category/urgency filters apply to the latest inbound reply's AI result.
        if category and (classification is None or (classification.override_category or classification.category) is not category):
            continue
        if urgency and (classification is None or classification.urgency is not urgency):
            continue
        out.append({
            "id": conv.id,
            "patient_name": f"{patient.first_name} {patient.last_name}".strip(),
            "doctor_id": conv.doctor_id,
            "office_id": conv.office_id,
            "assigned_to": conv.assigned_to,
            "unread_count": conv.unread_count,
            "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
            "last_message_preview": inbound.body[:120] if inbound else None,
            "category": (classification.override_category or classification.category) if classification else None,
            "urgency": classification.urgency if classification else None,
            "needs_manual_review": bool(classification and classification.confidence is None),
        })
    return out


def _load_conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="not found")
    check_tenancy(user, conv.practice_id)
    if not _doctor_scope_ok(db, user, conv):
        raise HTTPException(status_code=404, detail="not found")
    return conv


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    user: User = Depends(require(Permission.VIEW_INBOX)),
    db: Session = Depends(get_db),
):
    conv = _load_conversation(db, user, conversation_id)
    patient = db.get(Patient, conv.patient_id)
    messages = db.execute(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
    ).scalars().all()

    thread = []
    for m in messages:
        item = {
            "id": m.id,
            "direction": m.direction,
            "body": m.body,
            "status": m.status,
            "created_at": m.created_at.isoformat(),
            "sent_by": m.sent_by,
            "approved_by": m.approved_by,  # who approved and sent each reply
        }
        if m.direction is MessageDirection.IN:
            classification = db.execute(
                select(ReplyClassification).where(ReplyClassification.message_id == m.id)
            ).scalars().first()
            if classification:
                item["ai"] = {
                    "category": classification.category,
                    "override_category": classification.override_category,
                    "urgency": classification.urgency,
                    "confidence": classification.confidence,
                    "model": classification.model,
                }
            draft = db.execute(
                select(AIDraft).where(AIDraft.message_id == m.id)
            ).scalars().first()
            if draft:
                item["draft"] = {
                    "id": draft.id,
                    "body": draft.body,
                    "status": draft.status,
                    "approved_by": draft.approved_by,
                }
        thread.append(item)

    notes = db.execute(
        select(InternalNote).where(InternalNote.conversation_id == conv.id)
        .order_by(InternalNote.created_at)
    ).scalars().all()

    return {
        "id": conv.id,
        "patient": {
            "id": patient.id,
            "name": f"{patient.first_name} {patient.last_name}".strip(),
            "mobile": patient.mobile_e164,
            "sms_opt_out": patient.sms_opt_out,
        },
        "doctor_id": conv.doctor_id,
        "assigned_to": conv.assigned_to,
        "status": conv.status,
        "messages": thread,
        "internal_notes": [
            {"id": n.id, "author_id": n.author_id, "body": n.body,
             "created_at": n.created_at.isoformat()}
            for n in notes
        ],
    }


@router.post("/conversations/{conversation_id}/read")
def mark_read(
    conversation_id: str,
    user: User = Depends(require(Permission.VIEW_INBOX)),
    db: Session = Depends(get_db),
):
    conv = _load_conversation(db, user, conversation_id)
    conv.unread_count = 0
    return {"ok": True}


@router.post("/conversations/{conversation_id}/notes", status_code=201)
def add_note(
    conversation_id: str,
    body: NoteBody,
    user: User = Depends(require(Permission.MANAGE_CONVERSATION)),
    db: Session = Depends(get_db),
):
    conv = _load_conversation(db, user, conversation_id)
    note = InternalNote(conversation_id=conv.id, author_id=user.id, body=body.body)
    db.add(note)
    db.flush()
    return {"id": note.id}


@router.post("/conversations/{conversation_id}/assign")
def assign(
    conversation_id: str,
    body: AssignBody,
    user: User = Depends(require(Permission.MANAGE_CONVERSATION)),
    db: Session = Depends(get_db),
):
    conv = _load_conversation(db, user, conversation_id)
    conv.assigned_to = body.user_id
    audit(db, "conversation.assigned", user_id=user.id, practice_id=conv.practice_id,
          entity_type="conversation", entity_id=conv.id, details={"assigned_to": body.user_id})
    return {"ok": True}


@router.post("/conversations/{conversation_id}/archive")
def archive(
    conversation_id: str,
    user: User = Depends(require(Permission.MANAGE_CONVERSATION)),
    db: Session = Depends(get_db),
):
    conv = _load_conversation(db, user, conversation_id)
    conv.status = ConversationStatus.ARCHIVED
    audit(db, "conversation.archived", user_id=user.id, practice_id=conv.practice_id,
          entity_type="conversation", entity_id=conv.id)
    return {"ok": True}


@router.post("/replies/{message_id}/override-category")
def override_category(
    message_id: str,
    body: OverrideBody,
    user: User = Depends(require(Permission.OVERRIDE_CATEGORY)),
    db: Session = Depends(get_db),
):
    classification = db.execute(
        select(ReplyClassification).where(ReplyClassification.message_id == message_id)
    ).scalars().first()
    if classification is None:
        raise HTTPException(status_code=404, detail="not found")
    message = db.get(Message, message_id)
    conv = _load_conversation(db, user, message.conversation_id)
    classification.override_category = body.category
    classification.override_by = user.id
    classification.override_at = utcnow()
    # Overrides are ground truth for improving the classifier (SPEC §5.3).
    audit(db, "reply.category_overridden", user_id=user.id, practice_id=conv.practice_id,
          entity_type="reply_classification", entity_id=classification.id,
          details={"from": classification.category.value, "to": body.category.value})
    return {"ok": True, "category": body.category}


@router.post("/drafts/{draft_id}/approve-and-send")
def approve_and_send(
    draft_id: str,
    body: ApproveSendBody,
    request: Request,
    user: User = Depends(require(Permission.SEND_REPLY)),
    db: Session = Depends(get_db),
):
    draft = db.get(AIDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="not found")
    inbound = db.get(Message, draft.message_id)
    _load_conversation(db, user, inbound.conversation_id)  # tenancy + doctor scope
    try:
        out = inbox_service.approve_and_send_draft(
            db, user=user, draft=draft,
            sms_provider=request.app.state.sms_provider,
            edited_body=body.edited_body,
        )
    except inbox_service.InboxError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {"ok": True, "message_id": out.id, "body": out.body}


@router.post("/webhooks/sms/inbound")
async def inbound_webhook(request: Request, db: Session = Depends(get_db)):
    """Inbound SMS callback. Shared-secret verified when configured; Twilio
    signature verification must replace it before internet exposure (docs/05)."""
    from ..hardening import verify_webhook_secret

    verify_webhook_secret(request)
    payload = await request.json()
    message = inbox_service.handle_inbound(
        db,
        from_e164=str(payload.get("from_e164", "")),
        to_e164=str(payload.get("to_e164", "")),
        body=str(payload.get("body", "")),
        provider_message_id=payload.get("provider_message_id"),
        reply_extractor=request.app.state.reply_extractor,
        draft_provider=request.app.state.draft_provider,
    )
    return {"ok": True, "handled": message is not None}
