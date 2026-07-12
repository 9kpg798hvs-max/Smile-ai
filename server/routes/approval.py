"""Doctor approval endpoints (screen 7) and the follow-up queue (screens 8–9)."""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import approval as approval_service
from ..audit import audit
from ..deps import check_tenancy, get_db, require
from ..models import (
    FollowUp,
    FollowUpStatus,
    Patient,
    ScheduleUpload,
    User,
    Visit,
)
from ..rbac import Permission
from ..scheduler import apply_delivery_status

router = APIRouter(prefix="/api/v1", tags=["approval"])


class RejectBody(BaseModel):
    note: str


@router.get("/approvals/pending")
def pending(
    user: User = Depends(require(Permission.APPROVE_PATIENT_LIST)),
    db: Session = Depends(get_db),
):
    uploads = approval_service.pending_for_doctor(db, user)
    out = []
    for u in uploads:
        mine = [e for e in u.entries if e.resolved_doctor_id == user.id and not e.excluded]
        out.append({
            "upload_id": u.id,
            "office_id": u.office_id,
            "schedule_date": u.schedule_date.isoformat() if u.schedule_date else None,
            "patients": [
                {
                    "entry_id": e.id,
                    "patient_name": e.patient_name_raw,
                    "time": e.time_raw,
                    "procedure": e.procedure_raw,
                    "phone": e.phone_raw,
                    # Final preview before approval (SMS requirement).
                    "message_preview": approval_service.render_preview(db, e, u, user),
                }
                for e in mine
            ],
        })
    return out


@router.post("/schedule-uploads/{upload_id}/approve")
def approve(
    upload_id: str,
    user: User = Depends(require(Permission.APPROVE_PATIENT_LIST)),
    db: Session = Depends(get_db),
):
    upload = db.get(ScheduleUpload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="not found")
    check_tenancy(user, upload.practice_id)
    try:
        return approval_service.approve(db, user, upload)
    except approval_service.ApprovalError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/schedule-uploads/{upload_id}/reject")
def reject(
    upload_id: str,
    body: RejectBody,
    user: User = Depends(require(Permission.APPROVE_PATIENT_LIST)),
    db: Session = Depends(get_db),
):
    upload = db.get(ScheduleUpload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="not found")
    check_tenancy(user, upload.practice_id)
    try:
        approval_service.reject(db, user, upload, body.note)
    except approval_service.ApprovalError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {"ok": True}


@router.get("/follow-ups")
def follow_ups(
    status: FollowUpStatus | None = None,
    date: datetime.date | None = None,
    user: User = Depends(require(Permission.VIEW_FOLLOW_UPS)),
    db: Session = Depends(get_db),
):
    query = select(FollowUp, Visit, Patient).join(Visit, FollowUp.visit_id == Visit.id).join(
        Patient, Visit.patient_id == Patient.id
    )
    if user.practice_id is not None:
        query = query.where(FollowUp.practice_id == user.practice_id)
    if status is not None:
        query = query.where(FollowUp.status == status)
    if date is not None:
        query = query.where(Visit.visit_date == date)
    rows = db.execute(query).all()
    return [
        {
            "id": f.id,
            "status": f.status,
            "patient_name": f"{p.first_name} {p.last_name}".strip(),
            "procedure": v.procedure,
            "visit_date": v.visit_date.isoformat(),
            "doctor_id": v.doctor_id,
            "scheduled_send_at": f.scheduled_send_at.isoformat() if f.scheduled_send_at else None,
            "sent_at": f.sent_at.isoformat() if f.sent_at else None,
            "message_preview": f.rendered_body,
            "failure_reason": f.failure_reason,
        }
        for f, v, p in rows
    ]


@router.post("/follow-ups/{follow_up_id}/exclude")
def exclude_follow_up(
    follow_up_id: str,
    user: User = Depends(require(Permission.VIEW_FOLLOW_UPS)),
    db: Session = Depends(get_db),
):
    f = db.get(FollowUp, follow_up_id)
    if f is None:
        raise HTTPException(status_code=404, detail="not found")
    check_tenancy(user, f.practice_id)
    if f.status not in (FollowUpStatus.PENDING, FollowUpStatus.READY_TO_SEND):
        raise HTTPException(status_code=409, detail=f"cannot exclude a {f.status.value} follow-up")
    f.status = FollowUpStatus.EXCLUDED
    f.failure_reason = "excluded manually before send"
    audit(db, "follow_up.excluded", user_id=user.id, practice_id=f.practice_id,
          entity_type="follow_up", entity_id=f.id)
    return {"ok": True}


@router.post("/webhooks/sms/status")
async def sms_status_webhook(request: Request, db: Session = Depends(get_db)):
    """Provider delivery-status callback.

    MOCK provider: unsigned JSON {provider_message_id, event, ...}.
    Twilio: signature verification is a Phase 7 hardening item (docs/05) and
    MUST land before this endpoint is exposed to the internet.
    """
    payload = await request.json()
    ok = apply_delivery_status(
        db,
        provider_message_id=str(payload.get("provider_message_id", "")),
        event=str(payload.get("event", "")),
        payload=payload,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="unknown message")
    return {"ok": True}
