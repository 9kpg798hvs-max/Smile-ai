"""Schema constraint tests — most importantly duplicate follow-up prevention
(workflow requirement #7)."""

import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from server.db import Base, make_engine, make_session_factory
from server.models import FollowUp, Visit
from server.seed import seed


@pytest.fixture()
def db():
    engine = make_engine("sqlite://")  # in-memory
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        yield session


@pytest.fixture()
def seeded(db):
    data = seed(db)
    db.commit()
    return data


def test_duplicate_visit_same_patient_procedure_date_is_blocked(db, seeded):
    doctor = seeded["users"]["doctor"]
    patient = seeded["patients"][0]
    dup = Visit(
        practice_id=seeded["practice"].id,
        office_id=seeded["office"].id,
        patient_id=patient.id,
        doctor_id=doctor.id,
        visit_date=datetime.date.today(),
        procedure="Root canal #19",  # same as seeded visit
    )
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_same_patient_different_procedure_or_date_is_allowed(db, seeded):
    doctor = seeded["users"]["doctor"]
    patient = seeded["patients"][0]
    db.add(Visit(
        practice_id=seeded["practice"].id,
        office_id=seeded["office"].id,
        patient_id=patient.id,
        doctor_id=doctor.id,
        visit_date=datetime.date.today(),
        procedure="Crown seat",  # different procedure, same day: allowed
    ))
    db.add(Visit(
        practice_id=seeded["practice"].id,
        office_id=seeded["office"].id,
        patient_id=patient.id,
        doctor_id=doctor.id,
        visit_date=datetime.date.today() + datetime.timedelta(days=7),
        procedure="Root canal #19",  # same procedure, later date: allowed
    ))
    db.commit()


def test_one_follow_up_per_visit(db, seeded):
    from sqlalchemy import select
    visit = db.execute(select(Visit)).scalars().first()
    db.add(FollowUp(practice_id=seeded["practice"].id, visit_id=visit.id))
    db.commit()
    db.add(FollowUp(practice_id=seeded["practice"].id, visit_id=visit.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_seed_data_is_obviously_mock(seeded):
    for p in seeded["patients"]:
        assert p.mobile_e164.startswith("+1555")
    assert "MOCK" in seeded["practice"].name
