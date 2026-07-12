"""Seed obviously-fake MOCK data for development and tests.

Every patient here is fictional (555 numbers, 'Mock' surnames). Real patient
data must never be seeded — see docs/05-security-hipaa.md.

Usage: python -m server.seed
"""

import datetime

from sqlalchemy.orm import Session

from .db import Base, make_engine, make_session_factory
from .models import (
    DoctorProfile,
    Office,
    Patient,
    PhoneNumber,
    Practice,
    Role,
    Template,
    User,
)
from .security import hash_password

DEFAULT_PASSWORD = "mock-password-123"  # MOCK ONLY — dev/test convenience

CHECK_IN_TEMPLATE = (
    "Hi {first_name}, this is {doctor_name} checking on you. How are you feeling?"
)


def seed(db: Session) -> dict:
    practice = Practice(name="Mock Dental Group (MOCK DATA)", settings_json={
        "staff_can_send_replies": False,
    })
    db.add(practice)
    db.flush()

    office = Office(
        practice_id=practice.id,
        name="Main Office (MOCK)",
        phone_display="(555) 010-0100",
        timezone="America/New_York",
    )
    db.add(office)
    db.flush()

    def user(email: str, name: str, role: Role, practice_id=practice.id) -> User:
        u = User(
            practice_id=practice_id,
            email=email,
            password_hash=hash_password(DEFAULT_PASSWORD),
            full_name=name,
            role=role,
        )
        db.add(u)
        db.flush()
        return u

    superadmin = user("super@mock.test", "Sam Super (MOCK)", Role.SUPER_ADMIN, practice_id=None)
    admin = user("admin@mock.test", "Alex Admin (MOCK)", Role.PRACTICE_ADMIN)
    manager = user("manager@mock.test", "Morgan Manager (MOCK)", Role.OFFICE_MANAGER)
    doctor = user("doctor@mock.test", "Dana Doctor (MOCK)", Role.DOCTOR)
    staff = user("staff@mock.test", "Frankie Frontdesk (MOCK)", Role.STAFF)

    db.add(DoctorProfile(
        user_id=doctor.id,
        display_name="Dr. Mock",
        default_send_time=datetime.time(18, 30),
    ))
    db.add(PhoneNumber(
        practice_id=practice.id,
        office_id=office.id,
        doctor_id=doctor.id,
        e164="+15550100101",
        provider="mock",
    ))
    db.add(Template(
        practice_id=practice.id,
        name="Default check-in",
        body=CHECK_IN_TEMPLATE,
        is_default=True,
        updated_by=admin.id,
    ))

    patients = []
    for i, (first, last) in enumerate(
        [("Pat", "Mockley"), ("Casey", "Mockford"), ("Jamie", "Mocksmith")]
    ):
        p = Patient(
            practice_id=practice.id,
            first_name=first,
            last_name=last,
            mobile_e164=f"+1555020010{i}",
        )
        db.add(p)
        db.flush()
        patients.append(p)

    return {
        "practice": practice,
        "office": office,
        "users": {
            "super_admin": superadmin,
            "practice_admin": admin,
            "office_manager": manager,
            "doctor": doctor,
            "staff": staff,
        },
        "patients": patients,
    }


def main():
    engine = make_engine()
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        seed(db)
        db.commit()
    print("Seeded MOCK data. Logins: *@mock.test /", DEFAULT_PASSWORD)


if __name__ == "__main__":
    main()
