"""Schedule intake endpoints (screens 4–6): upload, review, edit, submit."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import intake
from ..deps import check_tenancy, get_db, require
from ..models import Office, ScheduleEntry, ScheduleUpload, User
from ..rbac import Permission

router = APIRouter(prefix="/api/v1/schedule-uploads", tags=["intake"])


class EntryPatch(BaseModel):
    patient_name_raw: str | None = None
    time_raw: str | None = None
    doctor_name_raw: str | None = None
    procedure_raw: str | None = None
    phone_raw: str | None = None
    matched_patient_id: str | None = None
    resolved_doctor_id: str | None = None
    excluded: bool | None = None
    exclusion_reason: str | None = None


class ManualEntry(BaseModel):
    patient_name_raw: str
    phone_raw: str
    time_raw: str = ""
    doctor_name_raw: str = ""
    procedure_raw: str = ""
    resolved_doctor_id: str | None = None


def _entry_out(e: ScheduleEntry) -> dict:
    return {
        "id": e.id,
        "row_index": e.row_index,
        "patient_name": e.patient_name_raw,
        "time": e.time_raw,
        "doctor_name": e.doctor_name_raw,
        "procedure": e.procedure_raw,
        "phone": e.phone_raw,
        "ocr_crossed_out": e.ocr_crossed_out,
        "ocr_confidence": e.ocr_confidence_json,
        "excluded": e.excluded,
        "exclusion_source": e.exclusion_source,
        "exclusion_reason": e.exclusion_reason,
        "matched_patient_id": e.matched_patient_id,
        "resolved_doctor_id": e.resolved_doctor_id,
        "edited_by": e.edited_by,
    }


def _upload_out(u: ScheduleUpload) -> dict:
    return {
        "id": u.id,
        "office_id": u.office_id,
        "status": u.status,
        "schedule_date": u.schedule_date.isoformat() if u.schedule_date else None,
        "ocr_model": u.ocr_model,  # "MOCK" until a real provider is configured
        "created_at": u.created_at.isoformat(),
        "entries": [_entry_out(e) for e in sorted(u.entries, key=lambda e: e.row_index)],
    }


def _load_upload(db: Session, user: User, upload_id: str) -> ScheduleUpload:
    upload = db.get(ScheduleUpload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="not found")
    check_tenancy(user, upload.practice_id)
    return upload


def _load_entry(db: Session, upload: ScheduleUpload, entry_id: str) -> ScheduleEntry:
    entry = db.get(ScheduleEntry, entry_id)
    if entry is None or entry.upload_id != upload.id:
        raise HTTPException(status_code=404, detail="not found")
    return entry


@router.post("", status_code=201)
async def upload_schedule(
    request: Request,
    file: UploadFile = File(...),
    office_id: str = Form(...),
    user: User = Depends(require(Permission.UPLOAD_SCHEDULE)),
    db: Session = Depends(get_db),
):
    office = db.get(Office, office_id)
    if office is None:
        raise HTTPException(status_code=404, detail="office not found")
    check_tenancy(user, office.practice_id)
    content = await file.read()
    try:
        upload = intake.create_upload(
            db,
            user,
            office_id=office_id,
            filename=file.filename or "upload",
            content=content,
            mime=file.content_type or "application/octet-stream",
            ocr_provider=request.app.state.ocr_provider,
            uploads_dir=request.app.state.uploads_dir,
        )
    except intake.IntakeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    db.flush()
    db.refresh(upload)
    return _upload_out(upload)


@router.get("/{upload_id}")
def get_upload(
    upload_id: str,
    user: User = Depends(require(Permission.REVIEW_OCR)),
    db: Session = Depends(get_db),
):
    return _upload_out(_load_upload(db, user, upload_id))


@router.patch("/{upload_id}/entries/{entry_id}")
def patch_entry(
    upload_id: str,
    entry_id: str,
    body: EntryPatch,
    user: User = Depends(require(Permission.REVIEW_OCR)),
    db: Session = Depends(get_db),
):
    upload = _load_upload(db, user, upload_id)
    entry = _load_entry(db, upload, entry_id)
    try:
        intake.update_entry(db, user, upload, entry, body.model_dump(exclude_unset=True))
    except intake.IntakeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return _entry_out(entry)


@router.post("/{upload_id}/entries", status_code=201)
def add_entry(
    upload_id: str,
    body: ManualEntry,
    user: User = Depends(require(Permission.REVIEW_OCR)),
    db: Session = Depends(get_db),
):
    upload = _load_upload(db, user, upload_id)
    try:
        entry = intake.add_manual_entry(db, user, upload, body.model_dump())
    except intake.IntakeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return _entry_out(entry)


@router.delete("/{upload_id}/entries/{entry_id}", status_code=204)
def delete_entry(
    upload_id: str,
    entry_id: str,
    user: User = Depends(require(Permission.REVIEW_OCR)),
    db: Session = Depends(get_db),
):
    upload = _load_upload(db, user, upload_id)
    entry = _load_entry(db, upload, entry_id)
    try:
        intake.remove_entry(db, user, upload, entry)
    except intake.IntakeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/{upload_id}/submit")
def submit(
    upload_id: str,
    user: User = Depends(require(Permission.SUBMIT_FOR_APPROVAL)),
    db: Session = Depends(get_db),
):
    upload = _load_upload(db, user, upload_id)
    try:
        intake.submit_for_approval(db, user, upload)
    except intake.IntakeError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {"ok": True, "status": upload.status}
