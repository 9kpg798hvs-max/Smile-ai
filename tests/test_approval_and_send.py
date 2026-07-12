"""Phase 3 tests: doctor approval gate, dedup, scheduling, mock sending.

The load-bearing assertions: zero outbound messages exist before doctor
approval, and duplicates can never produce a second follow-up.
"""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from server.app import create_app
from server.db import Base, make_engine, make_session_factory
from server.models import (
    FollowUp,
    FollowUpStatus,
    Message,
    Notification,
    Patient,
    Role,
    UploadStatus,
    User,
)
from server.scheduler import run_pending
from server.seed import DEFAULT_PASSWORD, seed
from server.sms import MockSMSProvider


@pytest.fixture()
def ctx(tmp_path):
    engine = make_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        data = seed(db)
        # A second doctor to prove cross-doctor approval is impossible.
        from server.security import hash_password
        from server.models import DoctorProfile

        other = User(
            practice_id=data["practice"].id,
            email="doctor2@mock.test",
            password_hash=hash_password(DEFAULT_PASSWORD),
            full_name="Devon Doctor2 (MOCK)",
            role=Role.DOCTOR,
        )
        db.add(other)
        db.flush()
        db.add(DoctorProfile(user_id=other.id, display_name="Dr. Mock II"))
        ids = {
            "office_id": data["office"].id,
            "doctor_id": data["users"]["doctor"].id,
            "doctor2_id": other.id,
            "practice_id": data["practice"].id,
        }
        db.commit()
    sms = MockSMSProvider()
    app = create_app(
        engine=engine, cookie_secure=False, sms_provider=sms,
        uploads_dir=tmp_path / "uploads",
    )
    return TestClient(app), ids, engine, sms


def login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200


def submitted_upload(client, ids, doctor_key="doctor_id"):
    """Staff uploads a sheet, assigns the doctor, submits. Returns upload id."""
    login(client, "staff@mock.test")
    up = client.post(
        "/api/v1/schedule-uploads",
        files={"file": ("sheet.png", b"fake", "image/png")},
        data={"office_id": ids["office_id"]},
    ).json()
    for e in up["entries"]:
        client.patch(
            f"/api/v1/schedule-uploads/{up['id']}/entries/{e['id']}",
            json={"resolved_doctor_id": ids[doctor_key]},
        )
    assert client.post(f"/api/v1/schedule-uploads/{up['id']}/submit").status_code == 200
    return up["id"]


def all_messages(engine):
    with make_session_factory(engine)() as db:
        return db.execute(select(Message)).scalars().all()


def all_follow_ups(engine):
    with make_session_factory(engine)() as db:
        return db.execute(select(FollowUp)).scalars().all()


# --- The gate ------------------------------------------------------------------

def test_no_messages_and_no_follow_ups_before_approval(ctx):
    client, ids, engine, sms = ctx
    submitted_upload(client, ids)
    # Run the sender: absolutely nothing must go out.
    with make_session_factory(engine)() as db:
        stats = run_pending(db, sms, now=_end_of_today())
        db.commit()
    assert stats == {"sent": 0, "failed": 0, "due": 0}
    assert sms.outbox == []
    assert all_messages(engine) == []
    assert all_follow_ups(engine) == []


def test_staff_cannot_approve(ctx):
    client, ids, engine, _ = ctx
    upload_id = submitted_upload(client, ids)
    login(client, "staff@mock.test")
    assert client.post(f"/api/v1/schedule-uploads/{upload_id}/approve").status_code == 403


def test_admin_cannot_approve(ctx):
    client, ids, engine, _ = ctx
    upload_id = submitted_upload(client, ids)
    login(client, "admin@mock.test")
    assert client.post(f"/api/v1/schedule-uploads/{upload_id}/approve").status_code == 403


def test_other_doctor_cannot_approve_someone_elses_list(ctx):
    client, ids, engine, _ = ctx
    upload_id = submitted_upload(client, ids)  # assigned to doctor 1
    login(client, "doctor2@mock.test")
    assert client.post(f"/api/v1/schedule-uploads/{upload_id}/approve").status_code == 403


# --- Approval effects ----------------------------------------------------------

def test_pending_approvals_show_final_message_preview(ctx):
    client, ids, engine, _ = ctx
    submitted_upload(client, ids)
    login(client, "doctor@mock.test")
    pending = client.get("/api/v1/approvals/pending").json()
    assert len(pending) == 1
    previews = [p["message_preview"] for p in pending[0]["patients"]]
    assert any("Hi Pat, this is Dr. Mock checking on you" in p for p in previews)


def test_approval_creates_ready_follow_ups_at_doctor_send_time(ctx):
    client, ids, engine, _ = ctx
    upload_id = submitted_upload(client, ids)
    login(client, "doctor@mock.test")
    r = client.post(f"/api/v1/schedule-uploads/{upload_id}/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["follow_ups_created"] == 2  # third entry is crossed out
    assert body["upload_status"] == "approved"

    fups = all_follow_ups(engine)
    assert len(fups) == 2
    assert all(f.status is FollowUpStatus.READY_TO_SEND for f in fups)
    # Doctor's configured 18:30 America/New_York == 22:30 or 23:30 UTC.
    for f in fups:
        assert f.scheduled_send_at.astimezone(datetime.timezone.utc).hour in (22, 23)
        assert f.approved_by == ids["doctor_id"]


def test_duplicate_visit_across_two_uploads_is_absorbed(ctx):
    client, ids, engine, _ = ctx
    for _ in range(2):  # same sheet submitted twice, same day
        upload_id = submitted_upload(client, ids)
        login(client, "doctor@mock.test")
        r = client.post(f"/api/v1/schedule-uploads/{upload_id}/approve")
        assert r.status_code == 200
    second = r.json()
    assert second["follow_ups_created"] == 0
    assert second["duplicates_excluded"] == 2
    assert len(all_follow_ups(engine)) == 2  # still just the originals


def test_reject_returns_list_to_staff_with_note(ctx):
    client, ids, engine, _ = ctx
    upload_id = submitted_upload(client, ids)
    login(client, "doctor@mock.test")
    r = client.post(
        f"/api/v1/schedule-uploads/{upload_id}/reject",
        json={"note": "Missing this afternoon's patients"},
    )
    assert r.status_code == 200
    login(client, "staff@mock.test")
    up = client.get(f"/api/v1/schedule-uploads/{upload_id}").json()
    assert up["status"] == "in_review"  # editable again
    with make_session_factory(engine)() as db:
        notes = db.execute(select(Notification)).scalars().all()
    assert any(n.type == "list_rejected" for n in notes)


# --- Sending -----------------------------------------------------------------------

def _end_of_today():
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)


def approve_all(client, ids, upload_id):
    login(client, "doctor@mock.test")
    r = client.post(f"/api/v1/schedule-uploads/{upload_id}/approve")
    assert r.status_code == 200


def test_scheduler_sends_only_when_due(ctx):
    client, ids, engine, sms = ctx
    upload_id = submitted_upload(client, ids)
    approve_all(client, ids, upload_id)

    factory = make_session_factory(engine)
    with factory() as db:
        # Before the doctor's send time: nothing goes.
        early = datetime.datetime.now(datetime.timezone.utc).replace(hour=1, minute=0)
        assert run_pending(db, sms, now=early)["sent"] == 0
        # After the send time: both go out via the MOCK provider.
        stats = run_pending(db, sms, now=_end_of_today())
        db.commit()
    assert stats["sent"] == 2
    assert len(sms.outbox) == 2
    assert all(m.from_e164 == "+15550100101" for m in sms.outbox)  # doctor's number
    assert any("Hi Pat" in m.body for m in sms.outbox)

    fups = all_follow_ups(engine)
    assert all(f.status is FollowUpStatus.SENT for f in fups)
    msgs = all_messages(engine)
    assert all(m.approved_by == ids["doctor_id"] for m in msgs)  # provenance


def test_opted_out_patient_never_sent(ctx):
    client, ids, engine, sms = ctx
    upload_id = submitted_upload(client, ids)
    approve_all(client, ids, upload_id)
    factory = make_session_factory(engine)
    with factory() as db:
        pat = db.execute(select(Patient).where(Patient.first_name == "Pat")).scalar_one()
        pat.sms_opt_out = True
        db.commit()
    with factory() as db:
        stats = run_pending(db, sms, now=_end_of_today())
        db.commit()
    assert stats["sent"] == 1  # only Casey
    assert all("Pat" not in m.body.split(",")[0] for m in sms.outbox)


def test_send_failure_marks_failed_and_notifies_doctor(ctx):
    client, ids, engine, sms = ctx
    sms.fail_numbers.add("+15550200100")  # Pat's phone
    upload_id = submitted_upload(client, ids)
    approve_all(client, ids, upload_id)
    with make_session_factory(engine)() as db:
        stats = run_pending(db, sms, now=_end_of_today())
        db.commit()
    assert stats == {"sent": 1, "failed": 1, "due": 2}
    fups = all_follow_ups(engine)
    assert {f.status for f in fups} == {FollowUpStatus.SENT, FollowUpStatus.FAILED}
    with make_session_factory(engine)() as db:
        notes = db.execute(select(Notification)).scalars().all()
    assert any(n.type == "message_failed" for n in notes)


def test_delivery_webhook_advances_status(ctx):
    client, ids, engine, sms = ctx
    upload_id = submitted_upload(client, ids)
    approve_all(client, ids, upload_id)
    with make_session_factory(engine)() as db:
        run_pending(db, sms, now=_end_of_today())
        db.commit()
    pmid = sms.outbox[0].provider_message_id
    r = client.post(
        "/api/v1/webhooks/sms/status",
        json={"provider_message_id": pmid, "event": "delivered"},
    )
    assert r.status_code == 200
    with make_session_factory(engine)() as db:
        msg = db.execute(
            select(Message).where(Message.provider_message_id == pmid)
        ).scalar_one()
        fup = db.get(FollowUp, msg.follow_up_id)
        assert msg.status.value == "delivered"
        assert fup.status is FollowUpStatus.DELIVERED


def test_manual_exclude_before_send(ctx):
    client, ids, engine, sms = ctx
    upload_id = submitted_upload(client, ids)
    approve_all(client, ids, upload_id)
    login(client, "staff@mock.test")
    fups = client.get("/api/v1/follow-ups", params={"status": "ready_to_send"}).json()
    assert len(fups) == 2
    r = client.post(f"/api/v1/follow-ups/{fups[0]['id']}/exclude")
    assert r.status_code == 200
    with make_session_factory(engine)() as db:
        stats = run_pending(db, sms, now=_end_of_today())
        db.commit()
    assert stats["sent"] == 1
