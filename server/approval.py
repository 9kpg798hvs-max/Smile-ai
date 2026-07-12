"""Doctor approval: the hard gate between an OCR'd list and any outbound SMS.

Approval is per treating doctor: a doctor approves the included entries that
name them. An entry is "approved" when a Visit exists for it (visits carry
schedule_entry_id); the upload flips to APPROVED once every included entry is
covered. Duplicate visits (same patient+doctor+date+procedure — requirement
#7) are absorbed via the unique constraint: the entry is marked excluded
(source: system) instead of producing a second follow-up.
"""

import datetime
import re
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import audit
from .models import (
    DoctorProfile,
    ExclusionSource,
    FollowUp,
    FollowUpStatus,
    Notification,
    Office,
    Patient,
    ScheduleEntry,
    ScheduleUpload,
    Template,
    UploadStatus,
    User,
    Visit,
    utcnow,
)
from .rendering import render_template


class ApprovalError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return f"+1{digits}"  # US assumption for MVP (docs/06)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return f"+{digits}"
    return None


def split_name(raw: str) -> tuple[str, str]:
    parts = (raw or "").strip().split()
    if not parts:
        return ("Patient", "")
    return (parts[0], " ".join(parts[1:]))


def resolve_template(db: Session, practice_id: str, doctor_id: str, procedure: str) -> Template | None:
    """Most specific wins: doctor+procedure > doctor > procedure > default."""
    templates = db.execute(
        select(Template).where(Template.practice_id == practice_id)
    ).scalars().all()

    def score(t: Template) -> tuple:
        return (
            t.doctor_id == doctor_id,
            bool(t.procedure and procedure and t.procedure.lower() in procedure.lower()),
            t.is_default,
        )

    candidates = [t for t in templates if
                  (t.doctor_id in (None, doctor_id))]
    if not candidates:
        return None
    return max(candidates, key=score)


def render_preview(db: Session, entry: ScheduleEntry, upload: ScheduleUpload, doctor: User) -> str:
    profile = db.get(DoctorProfile, doctor.id)
    office = db.get(Office, upload.office_id)
    template = resolve_template(db, upload.practice_id, doctor.id, entry.procedure_raw)
    body = template.body if template else (
        "Hi {first_name}, this is {doctor_name} checking on you. How are you feeling?"
    )
    first, _ = split_name(entry.patient_name_raw)
    date = upload.schedule_date or datetime.date.today()
    return render_template(body, {
        "first_name": first,
        "doctor_name": profile.display_name if profile else doctor.full_name,
        "office_name": office.name if office else "",
        "procedure": entry.procedure_raw,
        "treatment_date": date.isoformat(),
        "office_phone": office.phone_display if office else "",
    })


def _scheduled_send_at(db: Session, doctor: User, office: Office, visit_date: datetime.date) -> datetime.datetime:
    profile = db.get(DoctorProfile, doctor.id)
    send_time = profile.default_send_time if profile else datetime.time(18, 0)
    tz = ZoneInfo(office.timezone if office else "America/New_York")
    local = datetime.datetime.combine(visit_date, send_time, tzinfo=tz)
    return local.astimezone(datetime.timezone.utc)


def _find_or_create_patient(db: Session, practice_id: str, entry: ScheduleEntry) -> Patient:
    phone = normalize_phone(entry.phone_raw)
    if phone is None:
        raise ApprovalError(400, f"row {entry.row_index}: phone {entry.phone_raw!r} is not a valid number")
    existing = db.execute(
        select(Patient).where(Patient.practice_id == practice_id, Patient.mobile_e164 == phone)
    ).scalars().first()
    if existing:
        return existing
    first, last = split_name(entry.patient_name_raw)
    patient = Patient(practice_id=practice_id, first_name=first, last_name=last, mobile_e164=phone)
    db.add(patient)
    db.flush()
    return patient


def pending_for_doctor(db: Session, doctor: User) -> list[ScheduleUpload]:
    uploads = db.execute(
        select(ScheduleUpload).where(
            ScheduleUpload.practice_id == doctor.practice_id,
            ScheduleUpload.status == UploadStatus.SUBMITTED,
        )
    ).scalars().all()
    return [
        u for u in uploads
        if any(e.resolved_doctor_id == doctor.id and not e.excluded for e in u.entries)
    ]


def approve(db: Session, doctor: User, upload: ScheduleUpload) -> dict:
    if upload.status is not UploadStatus.SUBMITTED:
        raise ApprovalError(409, f"upload is {upload.status.value}; not awaiting approval")

    mine = [e for e in upload.entries if e.resolved_doctor_id == doctor.id and not e.excluded]
    if not mine:
        raise ApprovalError(403, "no patients on this list are assigned to you")

    office = db.get(Office, upload.office_id)
    visit_date = upload.schedule_date or datetime.date.today()
    created, duplicates, skipped_opt_out = [], [], []

    for entry in mine:
        patient = _find_or_create_patient(db, upload.practice_id, entry)
        visit = Visit(
            practice_id=upload.practice_id,
            office_id=upload.office_id,
            patient_id=patient.id,
            doctor_id=doctor.id,
            visit_date=visit_date,
            procedure=entry.procedure_raw or "",
            source="ocr",
            schedule_entry_id=entry.id,
        )
        nested = db.begin_nested()  # savepoint: absorb the dedup constraint
        try:
            db.add(visit)
            nested.commit()
        except IntegrityError:
            nested.rollback()
            # Requirement #7: same patient+doctor+date+procedure already
            # followed up — never message twice.
            entry.excluded = True
            entry.exclusion_source = ExclusionSource.SYSTEM
            entry.exclusion_reason = "duplicate: follow-up already exists for this patient/procedure/date"
            duplicates.append(entry.id)
            continue

        if patient.sms_opt_out:
            db.add(FollowUp(
                practice_id=upload.practice_id,
                visit_id=visit.id,
                status=FollowUpStatus.EXCLUDED,
                failure_reason="patient opted out of SMS",
                approved_by=doctor.id,
                approved_at=utcnow(),
            ))
            skipped_opt_out.append(entry.id)
            continue

        db.add(FollowUp(
            practice_id=upload.practice_id,
            visit_id=visit.id,
            status=FollowUpStatus.READY_TO_SEND,
            rendered_body=render_preview(db, entry, upload, doctor),
            scheduled_send_at=_scheduled_send_at(db, doctor, office, visit_date),
            approved_by=doctor.id,
            approved_at=utcnow(),
        ))
        created.append(entry.id)

    # Upload is fully approved once every included entry has a visit.
    included = [e for e in upload.entries if not e.excluded]
    covered = {
        v.schedule_entry_id
        for v in db.execute(
            select(Visit).where(Visit.schedule_entry_id.in_([e.id for e in upload.entries]))
        ).scalars()
    }
    if all(e.id in covered for e in included):
        upload.status = UploadStatus.APPROVED
        upload.approved_by = doctor.id
        upload.approved_at = utcnow()

    audit(db, "approval.approved", user_id=doctor.id, practice_id=upload.practice_id,
          entity_type="schedule_upload", entity_id=upload.id,
          details={"follow_ups": len(created), "duplicates": len(duplicates),
                   "opted_out": len(skipped_opt_out)})
    return {
        "follow_ups_created": len(created),
        "duplicates_excluded": len(duplicates),
        "opted_out": len(skipped_opt_out),
        "upload_status": upload.status,
    }


def reject(db: Session, doctor: User, upload: ScheduleUpload, note: str) -> None:
    if upload.status is not UploadStatus.SUBMITTED:
        raise ApprovalError(409, f"upload is {upload.status.value}; not awaiting approval")
    mine = [e for e in upload.entries if e.resolved_doctor_id == doctor.id and not e.excluded]
    if not mine:
        raise ApprovalError(403, "no patients on this list are assigned to you")
    # Back to staff for correction (docs/04 flow: reject returns the list).
    upload.status = UploadStatus.IN_REVIEW
    upload.rejection_note = note
    audit(db, "approval.rejected", user_id=doctor.id, practice_id=upload.practice_id,
          entity_type="schedule_upload", entity_id=upload.id, details={"note": note})
    db.add(Notification(
        user_id=upload.uploaded_by,
        type="list_rejected",
        title="Patient list returned by the doctor",
        payload_json={"schedule_upload_id": upload.id, "note": note},
    ))
