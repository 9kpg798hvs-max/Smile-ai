"""Template editor endpoints (screen 13). Doctors manage their own templates;
managers and admins manage all. Preview always shows the final rendered text."""

import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..deps import check_tenancy, get_db, require
from ..models import DoctorProfile, Office, Role, Template, Tone, User
from ..rbac import Permission
from ..rendering import PLACEHOLDERS, render_template

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


class TemplateBody(BaseModel):
    name: str
    body: str
    tone: Tone = Tone.FRIENDLY
    doctor_id: str | None = None
    office_id: str | None = None
    procedure: str | None = None
    is_default: bool = False


class TemplatePatch(BaseModel):
    name: str | None = None
    body: str | None = None
    tone: Tone | None = None
    procedure: str | None = None
    is_default: bool | None = None


def _out(t: Template) -> dict:
    return {
        "id": t.id, "name": t.name, "body": t.body, "tone": t.tone,
        "doctor_id": t.doctor_id, "office_id": t.office_id,
        "procedure": t.procedure, "is_default": t.is_default,
        "updated_by": t.updated_by, "updated_at": t.updated_at.isoformat(),
    }


def _doctor_scope(user: User, doctor_id: str | None) -> str | None:
    """Doctors may only touch templates bound to themselves (docs/03 'own')."""
    if user.role is Role.DOCTOR:
        if doctor_id not in (None, user.id):
            raise HTTPException(status_code=403, detail="doctors manage their own templates")
        return user.id
    return doctor_id


@router.get("")
def list_templates(
    user: User = Depends(require(Permission.MANAGE_TEMPLATES)),
    db: Session = Depends(get_db),
):
    query = select(Template)
    if user.practice_id is not None:
        query = query.where(Template.practice_id == user.practice_id)
    templates = db.execute(query).scalars().all()
    if user.role is Role.DOCTOR:
        templates = [t for t in templates if t.doctor_id in (None, user.id)]
    return [_out(t) for t in templates]


@router.post("", status_code=201)
def create_template(
    body: TemplateBody,
    user: User = Depends(require(Permission.MANAGE_TEMPLATES)),
    db: Session = Depends(get_db),
):
    doctor_id = _doctor_scope(user, body.doctor_id)
    t = Template(
        practice_id=user.practice_id,
        name=body.name,
        body=body.body,
        tone=body.tone,
        doctor_id=doctor_id,
        office_id=body.office_id,
        procedure=body.procedure,
        is_default=body.is_default,
        updated_by=user.id,
    )
    db.add(t)
    db.flush()
    audit(db, "template.created", user_id=user.id, practice_id=user.practice_id,
          entity_type="template", entity_id=t.id)
    return _out(t)


def _load(db: Session, user: User, template_id: str) -> Template:
    t = db.get(Template, template_id)
    if t is None:
        raise HTTPException(status_code=404, detail="not found")
    check_tenancy(user, t.practice_id)
    if user.role is Role.DOCTOR and t.doctor_id not in (None, user.id):
        raise HTTPException(status_code=404, detail="not found")
    return t


@router.patch("/{template_id}")
def update_template(
    template_id: str,
    body: TemplatePatch,
    user: User = Depends(require(Permission.MANAGE_TEMPLATES)),
    db: Session = Depends(get_db),
):
    t = _load(db, user, template_id)
    if user.role is Role.DOCTOR and t.doctor_id is None:
        raise HTTPException(status_code=403, detail="practice-wide templates are managed by admins")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(t, field, value)
    t.updated_by = user.id
    audit(db, "template.updated", user_id=user.id, practice_id=t.practice_id,
          entity_type="template", entity_id=t.id, details=changes)
    return _out(t)


@router.delete("/{template_id}", status_code=204)
def delete_template(
    template_id: str,
    user: User = Depends(require(Permission.MANAGE_TEMPLATES)),
    db: Session = Depends(get_db),
):
    t = _load(db, user, template_id)
    if user.role is Role.DOCTOR and t.doctor_id is None:
        raise HTTPException(status_code=403, detail="practice-wide templates are managed by admins")
    audit(db, "template.deleted", user_id=user.id, practice_id=t.practice_id,
          entity_type="template", entity_id=t.id, details={"name": t.name})
    db.delete(t)


@router.post("/{template_id}/preview")
def preview_template(
    template_id: str,
    user: User = Depends(require(Permission.MANAGE_TEMPLATES)),
    db: Session = Depends(get_db),
):
    t = _load(db, user, template_id)
    office = db.get(Office, t.office_id) if t.office_id else db.execute(
        select(Office).where(Office.practice_id == t.practice_id)
    ).scalars().first()
    doctor_name = "Dr. Example"
    if t.doctor_id:
        profile = db.get(DoctorProfile, t.doctor_id)
        if profile:
            doctor_name = profile.display_name
    rendered = render_template(t.body, {
        "first_name": "Sam",
        "doctor_name": doctor_name,
        "office_name": office.name if office else "Main Office",
        "procedure": t.procedure or "Root canal",
        "treatment_date": datetime.date.today().isoformat(),
        "office_phone": (office.phone_display if office else None) or "(555) 010-0100",
    })
    unresolved = [p for p in PLACEHOLDERS if "{" + p + "}" in rendered]
    return {"rendered": rendered, "unresolved_placeholders": unresolved,
            "sample_note": "Preview uses sample patient data (Sam)."}
