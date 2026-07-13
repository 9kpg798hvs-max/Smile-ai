"""Phase 5 tests: templates, doctor settings (reschedule), admin CRUD,
notifications (incl. daily summary + unanswered reminder generators)."""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from server.app import create_app
from server.db import Base, make_engine, make_session_factory
from server.models import FollowUp, FollowUpStatus, Notification, User
from server.notify import daily_summaries, unanswered_reminders
from server.seed import DEFAULT_PASSWORD, seed
from server.sms import MockSMSProvider

from .mock_extractor import KeywordExtractor


@pytest.fixture()
def ctx(tmp_path):
    engine = make_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        data = seed(db)
        ids = {
            "office_id": data["office"].id,
            "doctor_id": data["users"]["doctor"].id,
            "admin_id": data["users"]["practice_admin"].id,
            "practice_id": data["practice"].id,
        }
        db.commit()
    sms = MockSMSProvider()
    app = create_app(
        engine=engine, cookie_secure=False, sms_provider=sms,
        reply_extractor=KeywordExtractor(), uploads_dir=tmp_path / "uploads",
    )
    return TestClient(app), ids, engine, sms


def login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200


# --- templates (screen 13) -----------------------------------------------------

def test_template_crud_and_preview_renders_all_placeholders(ctx):
    client, ids, _, _ = ctx
    login(client, "manager@mock.test")
    t = client.post("/api/v1/templates", json={
        "name": "Crown check-in",
        "body": ("Hi {first_name}, {doctor_name} here from {office_name}. "
                 "Checking on your {procedure} from {treatment_date}. "
                 "Questions? Call {office_phone}."),
        "procedure": "Crown",
        "doctor_id": ids["doctor_id"],
    }).json()
    preview = client.post(f"/api/v1/templates/{t['id']}/preview").json()
    assert preview["unresolved_placeholders"] == []
    assert "Hi Sam, Dr. Mock here from Main Office (MOCK)" in preview["rendered"]

    r = client.patch(f"/api/v1/templates/{t['id']}", json={"name": "Crown check-in v2"})
    assert r.json()["name"] == "Crown check-in v2"
    assert client.delete(f"/api/v1/templates/{t['id']}").status_code == 204


def test_preview_exposes_placeholder_typos(ctx):
    client, ids, _, _ = ctx
    login(client, "manager@mock.test")
    t = client.post("/api/v1/templates", json={
        "name": "Typo", "body": "Hi {first_name}, from {ofice_name}!",
    }).json()
    preview = client.post(f"/api/v1/templates/{t['id']}/preview").json()
    assert "{ofice_name}" in preview["rendered"]  # visible, never silently blank


def test_doctor_can_only_manage_own_templates(ctx):
    client, ids, _, _ = ctx
    login(client, "doctor@mock.test")
    r = client.post("/api/v1/templates", json={
        "name": "Mine", "body": "Hi {first_name}", "doctor_id": None,
    })
    assert r.status_code == 201
    assert r.json()["doctor_id"] == ids["doctor_id"]  # forced to self

    # Cannot edit the practice-wide default template.
    login(client, "manager@mock.test")
    all_templates = client.get("/api/v1/templates").json()
    default = next(t for t in all_templates if t["is_default"])
    login(client, "doctor@mock.test")
    assert client.patch(
        f"/api/v1/templates/{default['id']}", json={"body": "hacked"}
    ).status_code == 403


def test_staff_cannot_manage_templates(ctx):
    client, _, _, _ = ctx
    login(client, "staff@mock.test")
    assert client.get("/api/v1/templates").status_code == 403


# --- doctor settings (screen 14) ---------------------------------------------------

def test_send_time_change_reschedules_pending_follow_ups(ctx):
    client, ids, engine, sms = ctx
    # Create a ready-to-send follow-up via the real pipeline.
    login(client, "staff@mock.test")
    up = client.post(
        "/api/v1/schedule-uploads",
        files={"file": ("s.png", b"x", "image/png")},
        data={"office_id": ids["office_id"]},
    ).json()
    for e in up["entries"]:
        client.patch(f"/api/v1/schedule-uploads/{up['id']}/entries/{e['id']}",
                     json={"resolved_doctor_id": ids["doctor_id"]})
    client.post(f"/api/v1/schedule-uploads/{up['id']}/submit")
    login(client, "doctor@mock.test")
    client.post(f"/api/v1/schedule-uploads/{up['id']}/approve")

    r = client.patch(f"/api/v1/doctors/{ids['doctor_id']}/settings",
                     json={"send_time": "07:15"})
    assert r.status_code == 200
    assert r.json()["rescheduled_pending_follow_ups"] == 2

    with make_session_factory(engine)() as db:
        fups = db.execute(
            select(FollowUp).where(FollowUp.status == FollowUpStatus.READY_TO_SEND)
        ).scalars().all()
    # 07:15 America/New_York == 11:15 or 12:15 UTC.
    for f in fups:
        assert f.scheduled_send_at.astimezone(datetime.timezone.utc).hour in (11, 12)

    settings = client.get(f"/api/v1/doctors/{ids['doctor_id']}/settings").json()
    assert settings["send_time"] == "07:15"


def test_doctor_cannot_edit_other_doctors_settings(ctx):
    client, ids, engine, _ = ctx
    from server.models import DoctorProfile, Role
    from server.security import hash_password

    with make_session_factory(engine)() as db:
        d2 = User(practice_id=ids["practice_id"], email="doctor2@mock.test",
                  password_hash=hash_password(DEFAULT_PASSWORD),
                  full_name="Devon Doctor2 (MOCK)", role=Role.DOCTOR)
        db.add(d2)
        db.flush()
        db.add(DoctorProfile(user_id=d2.id, display_name="Dr. Mock II"))
        db.commit()
        d2_id = d2.id
    login(client, "doctor@mock.test")
    assert client.patch(f"/api/v1/doctors/{d2_id}/settings",
                        json={"send_time": "09:00"}).status_code == 403


def test_wider_access_is_admin_only(ctx):
    client, ids, _, _ = ctx
    login(client, "doctor@mock.test")
    assert client.patch(f"/api/v1/doctors/{ids['doctor_id']}/settings",
                        json={"wider_access": True}).status_code == 403
    login(client, "admin@mock.test")
    assert client.patch(f"/api/v1/doctors/{ids['doctor_id']}/settings",
                        json={"wider_access": True}).status_code == 200


# --- admin (screens 2, 3, 17, 18) ----------------------------------------------------

def test_admin_office_and_user_crud(ctx):
    client, ids, _, _ = ctx
    login(client, "admin@mock.test")
    office = client.post("/api/v1/offices", json={"name": "Second Office (MOCK)"})
    assert office.status_code == 201

    created = client.post("/api/v1/users", json={
        "email": "newdoc@mock.test", "full_name": "Nia Newdoc (MOCK)",
        "role": "doctor", "display_name": "Dr. Newdoc",
        "office_ids": [office.json()["id"]],
    }).json()
    assert created["temp_password"]  # returned exactly once

    r = client.post("/api/v1/auth/login", json={
        "email": "newdoc@mock.test", "password": created["temp_password"],
    })
    assert r.status_code == 200
    me = client.get("/api/v1/auth/me").json()
    assert me["role"] == "doctor"

    login(client, "admin@mock.test")
    assert client.post("/api/v1/users", json={
        "email": "newdoc@mock.test", "full_name": "Dup", "role": "staff",
    }).status_code == 409

    assert client.patch(f"/api/v1/users/{created['id']}",
                        json={"is_active": False}).status_code == 200
    r = client.post("/api/v1/auth/login", json={
        "email": "newdoc@mock.test", "password": created["temp_password"],
    })
    assert r.status_code == 401  # deactivated


def test_manager_cannot_manage_users_or_offices(ctx):
    client, _, _, _ = ctx
    login(client, "manager@mock.test")
    assert client.post("/api/v1/offices", json={"name": "X"}).status_code == 403
    assert client.post("/api/v1/users", json={
        "email": "x@mock.test", "full_name": "X", "role": "staff",
    }).status_code == 403


def test_phone_number_admin(ctx):
    client, ids, _, _ = ctx
    login(client, "admin@mock.test")
    r = client.post("/api/v1/phone-numbers", json={
        "e164": "+15550100199", "office_id": ids["office_id"],
    })
    assert r.status_code == 201
    numbers = client.get("/api/v1/phone-numbers").json()
    assert any(n["e164"] == "+15550100199" and n["mock"] for n in numbers)
    assert client.post("/api/v1/phone-numbers", json={
        "e164": "+15550100199",
    }).status_code == 409
    assert client.delete(f"/api/v1/phone-numbers/{r.json()['id']}").status_code == 204


def test_practice_setting_toggle_flows_to_permissions(ctx):
    client, _, _, _ = ctx
    login(client, "admin@mock.test")
    r = client.patch("/api/v1/practice/settings", json={"staff_can_send_replies": True})
    assert r.status_code == 200 and r.json()["settings"]["staff_can_send_replies"] is True
    login(client, "staff@mock.test")
    assert "send_reply" in client.get("/api/v1/auth/me").json()["permissions"]


# --- notifications (screen 15) ----------------------------------------------------------

def test_notification_center_list_and_read(ctx):
    client, ids, engine, _ = ctx
    with make_session_factory(engine)() as db:
        db.add(Notification(user_id=ids["doctor_id"], type="new_reply",
                            title="Test note", payload_json={}))
        db.commit()
    login(client, "doctor@mock.test")
    notes = client.get("/api/v1/notifications", params={"unread_only": True}).json()
    assert len(notes) == 1
    assert client.post(f"/api/v1/notifications/{notes[0]['id']}/read").status_code == 200
    assert client.get("/api/v1/notifications", params={"unread_only": True}).json() == []


def test_notifications_are_private_to_the_user(ctx):
    client, ids, engine, _ = ctx
    with make_session_factory(engine)() as db:
        db.add(Notification(user_id=ids["doctor_id"], type="new_reply",
                            title="Doctor's note", payload_json={}))
        db.commit()
    login(client, "staff@mock.test")
    assert client.get("/api/v1/notifications").json() == []


def test_daily_summary_generated_once_per_doctor_per_day(ctx):
    _, ids, engine, _ = ctx
    with make_session_factory(engine)() as db:
        assert daily_summaries(db) == 1  # one seeded doctor
        assert daily_summaries(db) == 0  # idempotent
        db.commit()
        note = db.execute(
            select(Notification).where(Notification.type == "daily_summary")
        ).scalar_one()
        assert note.user_id == ids["doctor_id"]


def test_unanswered_reminder_fires_once_per_stale_thread(ctx):
    _, ids, engine, _ = ctx
    from server.models import Conversation, Patient

    with make_session_factory(engine)() as db:
        patient = db.execute(select(Patient)).scalars().first()
        stale = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5)
        db.add(Conversation(
            practice_id=ids["practice_id"], patient_id=patient.id,
            doctor_id=ids["doctor_id"], office_id=ids["office_id"],
            unread_count=2, last_message_at=stale,
        ))
        db.commit()
        assert unanswered_reminders(db) == 1
        assert unanswered_reminders(db) == 0  # deduped while unread
        db.commit()
