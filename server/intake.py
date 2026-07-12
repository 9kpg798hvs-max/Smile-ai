"""Schedule intake service: upload → OCR → editable entries → submit.

State machine (docs/02): processing → extracted → in_review → submitted →
approved | rejected. OCR runs synchronously in the MVP (25 rows/day); the
upload is persisted first so an OCR crash leaves a visible `processing` row
staff can retry or fill manually, never a lost sheet.
"""

import datetime
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from .audit import audit
from .models import (
    ExclusionSource,
    Notification,
    ScheduleEntry,
    ScheduleUpload,
    UploadStatus,
    User,
    utcnow,
)
from .ocr import OCRResult

ALLOWED_MIMES = {"image/png", "image/jpeg", "image/webp", "application/pdf"}

EDITABLE_ENTRY_FIELDS = {
    "patient_name_raw",
    "time_raw",
    "doctor_name_raw",
    "procedure_raw",
    "phone_raw",
    "matched_patient_id",
    "resolved_doctor_id",
}

# Statuses in which staff may still change entries.
_MUTABLE = {UploadStatus.EXTRACTED, UploadStatus.IN_REVIEW}


class IntakeError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


def store_file(uploads_dir: Path, practice_id: str, filename: str, content: bytes) -> Path:
    ext = Path(filename).suffix.lower() or ".bin"
    target_dir = uploads_dir / practice_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{uuid.uuid4()}{ext}"
    path.write_bytes(content)
    return path


def create_upload(
    db: Session,
    user: User,
    *,
    office_id: str,
    filename: str,
    content: bytes,
    mime: str,
    ocr_provider,
    uploads_dir: Path,
) -> ScheduleUpload:
    if mime not in ALLOWED_MIMES:
        raise IntakeError(415, f"unsupported file type {mime!r}; use PNG, JPEG, WebP, or PDF")

    path = store_file(uploads_dir, user.practice_id, filename, content)
    upload = ScheduleUpload(
        practice_id=user.practice_id,
        office_id=office_id,
        uploaded_by=user.id,
        file_path=str(path),
        mime=mime,
        status=UploadStatus.PROCESSING,
        ocr_model=getattr(ocr_provider, "model", None),
        ocr_prompt_version=getattr(ocr_provider, "prompt_version", None),
    )
    db.add(upload)
    db.flush()
    audit(db, "intake.upload", user_id=user.id, practice_id=user.practice_id,
          entity_type="schedule_upload", entity_id=upload.id,
          details={"mime": mime, "filename": filename})

    try:
        result: OCRResult = ocr_provider.extract(content, mime)
    except Exception as e:
        # Upload survives; staff can enter patients manually on the review screen.
        upload.status = UploadStatus.EXTRACTED
        audit(db, "intake.ocr_failed", user_id=user.id, practice_id=user.practice_id,
              entity_type="schedule_upload", entity_id=upload.id,
              details={"error": str(e)[:300]})
        return upload

    if result.schedule_date:
        try:
            upload.schedule_date = datetime.date.fromisoformat(result.schedule_date)
        except ValueError:
            pass
    for i, row in enumerate(result.entries):
        db.add(ScheduleEntry(
            upload_id=upload.id,
            row_index=i,
            patient_name_raw=row.patient_name,
            time_raw=row.time,
            doctor_name_raw=row.doctor_name,
            procedure_raw=row.procedure,
            phone_raw=row.phone,
            ocr_crossed_out=row.crossed_out,
            ocr_confidence_json=row.confidence,
            # Workflow req #3: crossed-out patients arrive pre-excluded,
            # staff can override below.
            excluded=row.crossed_out,
            exclusion_source=ExclusionSource.OCR if row.crossed_out else None,
            exclusion_reason="crossed out on schedule" if row.crossed_out else None,
        ))
    upload.status = UploadStatus.EXTRACTED
    return upload


def _mutable_upload(db: Session, upload: ScheduleUpload) -> None:
    if upload.status not in _MUTABLE:
        raise IntakeError(409, f"upload is {upload.status.value}; entries are locked")


def update_entry(
    db: Session, user: User, upload: ScheduleUpload, entry: ScheduleEntry, changes: dict
) -> ScheduleEntry:
    _mutable_upload(db, upload)
    applied = {}
    for field, value in changes.items():
        if field in EDITABLE_ENTRY_FIELDS:
            applied[field] = {"from": getattr(entry, field), "to": value}
            setattr(entry, field, value)
    if "excluded" in changes:
        entry.excluded = bool(changes["excluded"])
        entry.exclusion_source = ExclusionSource.STAFF  # staff override (req #3)
        entry.exclusion_reason = changes.get("exclusion_reason")
        applied["excluded"] = entry.excluded
    if not applied:
        raise IntakeError(400, "no editable fields in request")
    entry.edited_by = user.id
    entry.edited_at = utcnow()
    upload.status = UploadStatus.IN_REVIEW
    audit(db, "intake.entry_edited", user_id=user.id, practice_id=user.practice_id,
          entity_type="schedule_entry", entity_id=entry.id, details=applied)
    return entry


def add_manual_entry(
    db: Session, user: User, upload: ScheduleUpload, fields: dict
) -> ScheduleEntry:
    _mutable_upload(db, upload)
    entry = ScheduleEntry(
        upload_id=upload.id,
        row_index=len(upload.entries),
        edited_by=user.id,
        edited_at=utcnow(),
        **{k: v for k, v in fields.items() if k in EDITABLE_ENTRY_FIELDS},
    )
    db.add(entry)
    db.flush()
    upload.status = UploadStatus.IN_REVIEW
    audit(db, "intake.entry_added", user_id=user.id, practice_id=user.practice_id,
          entity_type="schedule_entry", entity_id=entry.id)
    return entry


def remove_entry(db: Session, user: User, upload: ScheduleUpload, entry: ScheduleEntry) -> None:
    _mutable_upload(db, upload)
    db.delete(entry)
    upload.status = UploadStatus.IN_REVIEW
    audit(db, "intake.entry_removed", user_id=user.id, practice_id=user.practice_id,
          entity_type="schedule_entry", entity_id=entry.id,
          details={"patient_name_raw": entry.patient_name_raw})


def submit_for_approval(db: Session, user: User, upload: ScheduleUpload) -> ScheduleUpload:
    if upload.status not in _MUTABLE:
        raise IntakeError(409, f"upload is {upload.status.value}; cannot submit")

    included = [e for e in upload.entries if not e.excluded]
    if not included:
        raise IntakeError(400, "no included patients to submit")
    missing_doctor = [e.id for e in included if not e.resolved_doctor_id]
    if missing_doctor:
        raise IntakeError(
            400,
            f"{len(missing_doctor)} included row(s) have no treating doctor assigned",
        )

    upload.status = UploadStatus.SUBMITTED
    audit(db, "intake.submitted", user_id=user.id, practice_id=user.practice_id,
          entity_type="schedule_upload", entity_id=upload.id,
          details={"included": len(included)})

    # Doctor approval required notification (one per distinct treating doctor).
    for doctor_id in {e.resolved_doctor_id for e in included}:
        db.add(Notification(
            user_id=doctor_id,
            type="doctor_approval_required",
            title="Patient list awaiting your approval",
            payload_json={"schedule_upload_id": upload.id, "patients": len(included)},
        ))
    return upload
