"""Admin endpoints: offices (screen 2), users/doctors/staff (screens 3, 18),
phone numbers (screen 17)."""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..deps import check_tenancy, get_db, require
from ..models import DoctorProfile, Office, PhoneNumber, Role, User, UserOfficeAccess
from ..rbac import Permission
from ..security import hash_password

router = APIRouter(prefix="/api/v1", tags=["admin"])


# --- offices --------------------------------------------------------------------

class OfficeBody(BaseModel):
    name: str
    address: str | None = None
    phone_display: str | None = None
    timezone: str = "America/New_York"


@router.get("/offices")
def list_offices(
    user: User = Depends(require(Permission.VIEW_FOLLOW_UPS)),  # any clinical role can list
    db: Session = Depends(get_db),
):
    query = select(Office)
    if user.practice_id is not None:
        query = query.where(Office.practice_id == user.practice_id)
    return [
        {"id": o.id, "name": o.name, "address": o.address,
         "phone_display": o.phone_display, "timezone": o.timezone}
        for o in db.execute(query).scalars()
    ]


@router.post("/offices", status_code=201)
def create_office(
    body: OfficeBody,
    user: User = Depends(require(Permission.MANAGE_OFFICES)),
    db: Session = Depends(get_db),
):
    office = Office(practice_id=user.practice_id, **body.model_dump())
    db.add(office)
    db.flush()
    audit(db, "office.created", user_id=user.id, practice_id=user.practice_id,
          entity_type="office", entity_id=office.id)
    return {"id": office.id}


@router.patch("/offices/{office_id}")
def update_office(
    office_id: str,
    body: OfficeBody,
    user: User = Depends(require(Permission.MANAGE_OFFICES)),
    db: Session = Depends(get_db),
):
    office = db.get(Office, office_id)
    if office is None:
        raise HTTPException(status_code=404, detail="not found")
    check_tenancy(user, office.practice_id)
    for field, value in body.model_dump().items():
        setattr(office, field, value)
    audit(db, "office.updated", user_id=user.id, practice_id=office.practice_id,
          entity_type="office", entity_id=office.id)
    return {"ok": True}


# Doctors list for assignment dropdowns — available to all clinical roles
# (names only, no admin data).
@router.get("/doctors")
def list_doctors(
    user: User = Depends(require(Permission.VIEW_FOLLOW_UPS)),
    db: Session = Depends(get_db),
):
    query = select(User).where(User.role == Role.DOCTOR, User.is_active)
    if user.practice_id is not None:
        query = query.where(User.practice_id == user.practice_id)
    doctors = db.execute(query).scalars().all()
    out = []
    for d in doctors:
        profile = db.get(DoctorProfile, d.id)
        out.append({"id": d.id, "display_name": profile.display_name if profile else d.full_name})
    return out


# --- users ----------------------------------------------------------------------

class UserBody(BaseModel):
    email: str
    full_name: str
    role: Role
    display_name: str | None = None  # doctors: "Dr. Lastname"
    office_ids: list[str] = []
    password: str | None = None  # omitted -> temp password generated


class UserPatch(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    office_ids: list[str] | None = None


@router.get("/users")
def list_users(
    user: User = Depends(require(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
):
    query = select(User)
    if user.practice_id is not None:
        query = query.where(User.practice_id == user.practice_id)
    return [
        {"id": u.id, "email": u.email, "full_name": u.full_name,
         "role": u.role, "is_active": u.is_active}
        for u in db.execute(query).scalars()
    ]


@router.post("/users", status_code=201)
def create_user(
    body: UserBody,
    user: User = Depends(require(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
):
    if body.role is Role.SUPER_ADMIN and user.role is not Role.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="cannot grant super admin")
    email = body.email.lower().strip()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="email already in use")

    temp_password = body.password or secrets.token_urlsafe(9)
    new_user = User(
        practice_id=user.practice_id,
        email=email,
        password_hash=hash_password(temp_password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(new_user)
    db.flush()
    if body.role is Role.DOCTOR:
        db.add(DoctorProfile(
            user_id=new_user.id,
            display_name=body.display_name or body.full_name,
        ))
    for office_id in body.office_ids:
        db.add(UserOfficeAccess(user_id=new_user.id, office_id=office_id))
    audit(db, "user.created", user_id=user.id, practice_id=user.practice_id,
          entity_type="user", entity_id=new_user.id, details={"role": body.role.value})
    return {
        "id": new_user.id,
        # Returned once so the admin can hand it over; user changes it on login.
        "temp_password": None if body.password else temp_password,
    }


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: UserPatch,
    user: User = Depends(require(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="not found")
    check_tenancy(user, target.practice_id)
    changes = body.model_dump(exclude_unset=True)
    if "office_ids" in changes:
        office_ids = changes.pop("office_ids")
        for row in db.execute(
            select(UserOfficeAccess).where(UserOfficeAccess.user_id == target.id)
        ).scalars():
            db.delete(row)
        for office_id in office_ids:
            db.add(UserOfficeAccess(user_id=target.id, office_id=office_id))
    for field, value in changes.items():
        setattr(target, field, value)
    audit(db, "user.updated", user_id=user.id, practice_id=target.practice_id,
          entity_type="user", entity_id=target.id, details=changes)
    return {"ok": True}


# --- phone numbers ----------------------------------------------------------------

class PhoneBody(BaseModel):
    e164: str
    office_id: str | None = None
    doctor_id: str | None = None
    provider: str = "mock"  # mock | twilio — twilio requires docs/05 checklist
    kind: str = "dedicated"  # dedicated | hosted


@router.get("/phone-numbers")
def list_numbers(
    user: User = Depends(require(Permission.MANAGE_PHONE_NUMBERS)),
    db: Session = Depends(get_db),
):
    query = select(PhoneNumber)
    if user.practice_id is not None:
        query = query.where(PhoneNumber.practice_id == user.practice_id)
    return [
        {"id": n.id, "e164": n.e164, "office_id": n.office_id, "doctor_id": n.doctor_id,
         "provider": n.provider, "kind": n.kind, "status": n.status,
         "mock": n.provider == "mock"}
        for n in db.execute(query).scalars()
    ]


@router.post("/phone-numbers", status_code=201)
def create_number(
    body: PhoneBody,
    user: User = Depends(require(Permission.MANAGE_PHONE_NUMBERS)),
    db: Session = Depends(get_db),
):
    if db.execute(select(PhoneNumber).where(PhoneNumber.e164 == body.e164)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="number already registered")
    number = PhoneNumber(practice_id=user.practice_id, **body.model_dump())
    db.add(number)
    db.flush()
    audit(db, "phone_number.created", user_id=user.id, practice_id=user.practice_id,
          entity_type="phone_number", entity_id=number.id, details={"e164": body.e164})
    return {"id": number.id}


@router.delete("/phone-numbers/{number_id}", status_code=204)
def retire_number(
    number_id: str,
    user: User = Depends(require(Permission.MANAGE_PHONE_NUMBERS)),
    db: Session = Depends(get_db),
):
    number = db.get(PhoneNumber, number_id)
    if number is None:
        raise HTTPException(status_code=404, detail="not found")
    check_tenancy(user, number.practice_id)
    number.status = "retired"  # soft: history keeps pointing at it
    audit(db, "phone_number.retired", user_id=user.id, practice_id=number.practice_id,
          entity_type="phone_number", entity_id=number.id)
