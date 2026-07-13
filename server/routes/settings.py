"""Settings endpoints: doctor send-time/tone (screen 14) and practice settings.

Changing a doctor's send time re-schedules their pending (not-yet-sent)
follow-ups — acceptance criterion for module 14.
"""

import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..deps import check_tenancy, get_current_user, get_db, require
from ..models import (
    DoctorProfile,
    FollowUp,
    FollowUpStatus,
    Office,
    Practice,
    Role,
    Tone,
    User,
    Visit,
)
from ..rbac import Permission

router = APIRouter(prefix="/api/v1", tags=["settings"])


class DoctorSettingsPatch(BaseModel):
    display_name: str | None = None
    send_time: str | None = None  # "HH:MM"
    tone: Tone | None = None
    signature: str | None = None
    wider_access: bool | None = None  # admin-only field


class PracticeSettingsPatch(BaseModel):
    staff_can_send_replies: bool | None = None
    name: str | None = None


def _load_doctor(db: Session, user: User, doctor_id: str) -> tuple[User, DoctorProfile]:
    doctor = db.get(User, doctor_id)
    if doctor is None or doctor.role is not Role.DOCTOR:
        raise HTTPException(status_code=404, detail="not found")
    check_tenancy(user, doctor.practice_id)
    if user.role is Role.DOCTOR and user.id != doctor_id:
        raise HTTPException(status_code=403, detail="doctors manage their own settings")
    profile = db.get(DoctorProfile, doctor_id)
    if profile is None:
        profile = DoctorProfile(user_id=doctor_id, display_name=doctor.full_name)
        db.add(profile)
        db.flush()
    return doctor, profile


def _reschedule_pending(db: Session, doctor_id: str, new_time: datetime.time) -> int:
    rows = db.execute(
        select(FollowUp, Visit).join(Visit, FollowUp.visit_id == Visit.id).where(
            Visit.doctor_id == doctor_id,
            FollowUp.status == FollowUpStatus.READY_TO_SEND,
        )
    ).all()
    for follow_up, visit in rows:
        office = db.get(Office, visit.office_id)
        tz = ZoneInfo(office.timezone if office else "America/New_York")
        local = datetime.datetime.combine(visit.visit_date, new_time, tzinfo=tz)
        follow_up.scheduled_send_at = local.astimezone(datetime.timezone.utc)
    return len(rows)


@router.get("/doctors/{doctor_id}/settings")
def get_doctor_settings(
    doctor_id: str,
    user: User = Depends(require(Permission.CONFIGURE_SEND_TIMES)),
    db: Session = Depends(get_db),
):
    doctor, profile = _load_doctor(db, user, doctor_id)
    return {
        "doctor_id": doctor.id,
        "display_name": profile.display_name,
        "send_time": profile.default_send_time.strftime("%H:%M"),
        "tone": profile.tone,
        "signature": profile.signature,
        "wider_access": profile.wider_access,
    }


@router.patch("/doctors/{doctor_id}/settings")
def patch_doctor_settings(
    doctor_id: str,
    body: DoctorSettingsPatch,
    user: User = Depends(require(Permission.CONFIGURE_SEND_TIMES)),
    db: Session = Depends(get_db),
):
    doctor, profile = _load_doctor(db, user, doctor_id)
    changes = body.model_dump(exclude_unset=True)

    rescheduled = 0
    if "wider_access" in changes:
        # Widening a doctor's visibility is an admin decision (docs/03).
        if user.role not in (Role.SUPER_ADMIN, Role.PRACTICE_ADMIN):
            raise HTTPException(status_code=403, detail="wider_access is admin-managed")
        profile.wider_access = bool(changes.pop("wider_access"))
    if "send_time" in changes:
        raw = changes.pop("send_time")
        try:
            hh, mm = raw.split(":")
            new_time = datetime.time(int(hh), int(mm))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="send_time must be HH:MM")
        profile.default_send_time = new_time
        rescheduled = _reschedule_pending(db, doctor_id, new_time)
    for field in ("display_name", "tone", "signature"):
        if field in changes:
            setattr(profile, field, changes[field])

    audit(db, "doctor_settings.updated", user_id=user.id, practice_id=doctor.practice_id,
          entity_type="doctor_profile", entity_id=doctor_id,
          details={**body.model_dump(exclude_unset=True), "rescheduled": rescheduled})
    return {"ok": True, "rescheduled_pending_follow_ups": rescheduled}


@router.get("/practice/settings")
def get_practice_settings(
    user: User = Depends(require(Permission.MANAGE_SECURITY)),
    db: Session = Depends(get_db),
):
    practice = db.get(Practice, user.practice_id) if user.practice_id else None
    if practice is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"name": practice.name, "settings": practice.settings_json}


@router.patch("/practice/settings")
def patch_practice_settings(
    body: PracticeSettingsPatch,
    user: User = Depends(require(Permission.MANAGE_SECURITY)),
    db: Session = Depends(get_db),
):
    practice = db.get(Practice, user.practice_id) if user.practice_id else None
    if practice is None:
        raise HTTPException(status_code=404, detail="not found")
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        practice.name = changes.pop("name")
    if changes:
        practice.settings_json = {**practice.settings_json, **changes}
    audit(db, "practice_settings.updated", user_id=user.id, practice_id=practice.id,
          details=body.model_dump(exclude_unset=True))
    return {"ok": True, "settings": practice.settings_json}
