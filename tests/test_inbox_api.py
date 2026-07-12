"""Phase 4 tests: inbound replies → AI triage → drafts → human-approved sends.

Load-bearing assertions: red drafts are escalation-only, the AI can never
send, staff can't send unless the practice enables it, STOP opts out.
"""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from server.app import create_app
from server.db import Base, make_engine, make_session_factory
from server.models import (
    AIDraft,
    FollowUp,
    FollowUpStatus,
    Message,
    MessageDirection,
    Notification,
    Patient,
    Practice,
    ReplyClassification,
)
from server.scheduler import run_pending
from server.seed import DEFAULT_PASSWORD, seed
from server.sms import MockSMSProvider

from .mock_extractor import KeywordExtractor

PATIENT_PHONE = "+15550200100"   # Pat Mockley (seeded)
PRACTICE_NUMBER = "+15550100101"  # doctor's number (seeded)


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
            "staff_id": data["users"]["staff"].id,
            "practice_id": data["practice"].id,
            "patient_id": data["patients"][0].id,
        }
        db.commit()
    sms = MockSMSProvider()
    app = create_app(
        engine=engine, cookie_secure=False, sms_provider=sms,
        reply_extractor=KeywordExtractor(),  # deterministic MOCK AI
        uploads_dir=tmp_path / "uploads",
    )
    return TestClient(app), ids, engine, sms


def login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200


def inbound(client, body, from_e164=PATIENT_PHONE):
    return client.post("/api/v1/webhooks/sms/inbound", json={
        "from_e164": from_e164, "to_e164": PRACTICE_NUMBER, "body": body,
    })


def send_check_in(client, ids, engine, sms):
    """Full pipeline: upload → submit → approve → send, so replies have context."""
    login(client, "staff@mock.test")
    up = client.post(
        "/api/v1/schedule-uploads",
        files={"file": ("s.png", b"x", "image/png")},
        data={"office_id": ids["office_id"]},
    ).json()
    for e in up["entries"]:
        client.patch(
            f"/api/v1/schedule-uploads/{up['id']}/entries/{e['id']}",
            json={"resolved_doctor_id": ids["doctor_id"]},
        )
    client.post(f"/api/v1/schedule-uploads/{up['id']}/submit")
    login(client, "doctor@mock.test")
    assert client.post(f"/api/v1/schedule-uploads/{up['id']}/approve").status_code == 200
    with make_session_factory(engine)() as db:
        run_pending(db, sms, now=datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(days=1))
        db.commit()


# --- inbound pipeline --------------------------------------------------------------

def test_green_reply_classified_and_drafted(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    r = inbound(client, "No pain at all today, so much better than yesterday!")
    assert r.json() == {"ok": True, "handled": True}

    login(client, "doctor@mock.test")
    convs = client.get("/api/v1/conversations", params={"q": "Pat"}).json()
    assert len(convs) == 1
    conv = convs[0]
    assert conv["category"] == "doing_well"
    assert conv["urgency"] == "green"
    assert conv["unread_count"] == 1

    thread = client.get(f"/api/v1/conversations/{conv['id']}").json()
    last = thread["messages"][-1]
    assert last["direction"] == "in"
    assert last["ai"]["urgency"] == "green"
    assert "Glad to hear it" in last["draft"]["body"]


def test_red_reply_urgent_alert_and_escalation_only_draft(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    inbound(client, "Thanks so much for checking in! My cheek has puffed up a bit though.")

    login(client, "doctor@mock.test")
    convs = client.get("/api/v1/conversations", params={"urgency": "red"}).json()
    assert len(convs) == 1
    assert convs[0]["category"] == "swelling"

    thread = client.get(f"/api/v1/conversations/{convs[0]['id']}").json()
    draft = thread["messages"][-1]["draft"]["body"]
    # SPEC §2.4: escalation only — get them on the phone; no advice.
    assert "call the office" in draft.lower()
    for banned in ("ibuprofen", "rinse", "normal", "don't worry", "no worries"):
        assert banned not in draft.lower()

    with make_session_factory(engine)() as db:
        notes = db.execute(select(Notification)).scalars().all()
        fups = db.execute(select(FollowUp)).scalars().all()
    assert any(n.type == "urgent_reply" for n in notes)
    assert any(f.status is FollowUpStatus.NEEDS_ATTENTION for f in fups)


def test_emergency_language_is_emergency_category(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    inbound(client, "My throat is swelling and it is getting hard to swallow")
    login(client, "doctor@mock.test")
    convs = client.get("/api/v1/conversations", params={"category": "emergency"}).json()
    assert len(convs) == 1
    assert convs[0]["urgency"] == "red"


def test_normal_reply_marks_follow_up_replied(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    inbound(client, "All good — ate dinner normally, zero pain.")
    with make_session_factory(engine)() as db:
        fups = db.execute(select(FollowUp)).scalars().all()
        pat_fup = [f for f in fups if f.status is FollowUpStatus.REPLIED]
    assert len(pat_fup) == 1


def test_stop_opts_out_and_cancels_pending(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    r = inbound(client, "STOP")
    assert r.json()["handled"] is True
    with make_session_factory(engine)() as db:
        pat = db.get(Patient, ids["patient_id"])
        assert pat.sms_opt_out is True
        # No classification/draft for STOP messages.
        msgs = db.execute(select(Message).where(Message.direction == MessageDirection.IN)).scalars().all()
        cls = db.execute(select(ReplyClassification)).scalars().all()
    assert len(msgs) == 1 and len(cls) == 0


def test_unknown_practice_number_ignored(ctx):
    client, ids, engine, sms = ctx
    r = client.post("/api/v1/webhooks/sms/inbound", json={
        "from_e164": PATIENT_PHONE, "to_e164": "+19998887777", "body": "hi",
    })
    assert r.json()["handled"] is False


def test_ai_failure_fails_safe_to_yellow_manual_review(ctx, tmp_path):
    class ExplodingExtractor:
        model = "mock-exploding"

        def extract(self, text, context=None):
            raise RuntimeError("boom")

    client, ids, engine, sms = ctx
    app = create_app(
        engine=engine, cookie_secure=False, sms_provider=sms,
        reply_extractor=ExplodingExtractor(), uploads_dir=tmp_path / "u2",
    )
    c2 = TestClient(app)
    send_check_in(c2, ids, engine, sms)
    inbound(c2, "my face is swollen badly")
    login(c2, "doctor@mock.test")
    convs = c2.get("/api/v1/conversations", params={"q": "Pat"}).json()
    assert convs[0]["urgency"] == "yellow"  # never green, never lost
    assert convs[0]["needs_manual_review"] is True


# --- human-approved replies -----------------------------------------------------------

def _get_draft(client):
    convs = client.get("/api/v1/conversations", params={"q": "Pat"}).json()
    thread = client.get(f"/api/v1/conversations/{convs[0]['id']}").json()
    return thread["messages"][-1]["draft"], convs[0]


def test_doctor_can_edit_and_send_draft(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    inbound(client, "When should I take the next ibuprofen, before or after eating?")
    login(client, "doctor@mock.test")
    draft, conv = _get_draft(client)
    before = len(sms.outbox)
    r = client.post(f"/api/v1/drafts/{draft['id']}/approve-and-send", json={
        "edited_body": "Either is fine — with food is gentler on the stomach. — Dr. Mock",
    })
    assert r.status_code == 200
    assert len(sms.outbox) == before + 1
    assert "with food is gentler" in sms.outbox[-1].body

    thread = client.get(f"/api/v1/conversations/{conv['id']}").json()
    outbound = thread["messages"][-1]
    assert outbound["direction"] == "out"
    assert outbound["approved_by"] == ids["doctor_id"]  # provenance shown in thread
    with make_session_factory(engine)() as db:
        d = db.get(AIDraft, draft["id"])
        assert d.edited_body is not None  # doctor-voice training signal captured
        assert d.status.value == "approved"


def test_draft_cannot_be_sent_twice(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    inbound(client, "Is it normal for the tooth to feel high when I bite?")
    login(client, "doctor@mock.test")
    draft, _ = _get_draft(client)
    assert client.post(f"/api/v1/drafts/{draft['id']}/approve-and-send", json={}).status_code == 200
    assert client.post(f"/api/v1/drafts/{draft['id']}/approve-and-send", json={}).status_code == 409


def test_staff_cannot_send_by_default_but_setting_unlocks(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    inbound(client, "Feeling fine! Is coffee ok tomorrow?")
    login(client, "staff@mock.test")
    draft, _ = _get_draft(client)
    assert client.post(
        f"/api/v1/drafts/{draft['id']}/approve-and-send", json={}
    ).status_code == 403  # SPEC §2.1 default: doctor-only

    with make_session_factory(engine)() as db:
        practice = db.get(Practice, ids["practice_id"])
        practice.settings_json = {**practice.settings_json, "staff_can_send_replies": True}
        db.commit()
    r = client.post(f"/api/v1/drafts/{draft['id']}/approve-and-send", json={})
    assert r.status_code == 200
    with make_session_factory(engine)() as db:
        out = db.execute(
            select(Message).where(Message.direction == MessageDirection.OUT,
                                  Message.sent_by.is_not(None))
        ).scalars().all()
    assert out[-1].approved_by == ids["staff_id"]  # audit trail of who sent


def test_cannot_reply_to_opted_out_patient(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    inbound(client, "Quick question, can I eat solid food?")
    login(client, "doctor@mock.test")
    draft, _ = _get_draft(client)
    with make_session_factory(engine)() as db:
        db.get(Patient, ids["patient_id"]).sms_opt_out = True
        db.commit()
    assert client.post(
        f"/api/v1/drafts/{draft['id']}/approve-and-send", json={}
    ).status_code == 409


# --- inbox management -------------------------------------------------------------------

def test_override_category_recorded(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    inbound(client, "It aches when I bite down but otherwise fine")
    login(client, "doctor@mock.test")
    convs = client.get("/api/v1/conversations", params={"q": "Pat"}).json()
    thread = client.get(f"/api/v1/conversations/{convs[0]['id']}").json()
    msg_id = thread["messages"][-1]["id"]
    r = client.post(f"/api/v1/replies/{msg_id}/override-category",
                    json={"category": "appointment_request"})
    assert r.status_code == 200
    convs = client.get("/api/v1/conversations",
                       params={"category": "appointment_request"}).json()
    assert len(convs) == 1  # filters respect the human override


def test_notes_assign_archive_and_read(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    inbound(client, "ok")
    login(client, "staff@mock.test")
    convs = client.get("/api/v1/conversations", params={"q": "Pat"}).json()
    cid = convs[0]["id"]
    assert client.post(f"/api/v1/conversations/{cid}/notes",
                       json={"body": "Called patient, voicemail left"}).status_code == 201
    assert client.post(f"/api/v1/conversations/{cid}/assign",
                       json={"user_id": ids["staff_id"]}).status_code == 200
    assert client.post(f"/api/v1/conversations/{cid}/read").status_code == 200
    assert client.get("/api/v1/conversations", params={"unread_only": True}).json() == []
    assert client.post(f"/api/v1/conversations/{cid}/archive").status_code == 200
    assert client.get("/api/v1/conversations", params={"q": "Pat"}).json() == []
    assert len(client.get("/api/v1/conversations", params={"archived": True}).json()) == 1


def test_doctor_scoping_hides_other_doctors_threads(ctx):
    client, ids, engine, sms = ctx
    send_check_in(client, ids, engine, sms)
    inbound(client, "ok")
    # Second doctor with no wider access sees nothing.
    from server.models import DoctorProfile, Role, User
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
    login(client, "doctor2@mock.test")
    assert client.get("/api/v1/conversations").json() == []

    with make_session_factory(engine)() as db:
        db.get(DoctorProfile, d2_id).wider_access = True
        db.commit()
    assert len(client.get("/api/v1/conversations", params={"q": "Pat"}).json()) == 1
