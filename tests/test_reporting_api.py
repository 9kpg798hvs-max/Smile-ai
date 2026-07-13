"""Phase 6 tests: dashboard tiles match the DB, analytics on a seeded fixture,
delivery and audit logs."""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from server.app import create_app
from server.db import Base, make_engine, make_session_factory
from server.scheduler import run_pending
from server.seed import DEFAULT_PASSWORD, seed
from server.sms import MockSMSProvider

from .mock_extractor import KeywordExtractor

PAT = "+15550200100"
CASEY = "+15550200101"
OURS = "+15550100101"


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
    assert client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    ).status_code == 200


def full_day(client, ids, engine, sms):
    """Upload → approve → send → one red reply, one green reply."""
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
    with make_session_factory(engine)() as db:
        run_pending(db, sms, now=datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(days=1))
        # The fake "due" clock above stamps sent_at in the future; backdate it
        # so reply timestamps land after the send (as they would in reality).
        from server.models import FollowUp
        for f in db.execute(select(FollowUp)).scalars():
            if f.sent_at is not None:
                f.sent_at = datetime.datetime.now(datetime.timezone.utc) - \
                    datetime.timedelta(minutes=30)
        db.commit()
    for from_e164, body in [
        (PAT, "My cheek has puffed up a bit since this morning"),
        (CASEY, "All good, zero pain, feeling great!"),
    ]:
        client.post("/api/v1/webhooks/sms/inbound", json={
            "from_e164": from_e164, "to_e164": OURS, "body": body,
        })


def test_dashboard_tiles_match_reality(ctx):
    client, ids, engine, sms = ctx
    full_day(client, ids, engine, sms)
    login(client, "doctor@mock.test")
    d = client.get("/api/v1/dashboard").json()
    assert d["todays_follow_ups"] == 2
    assert d["pending_doctor_approval"] == 0
    assert d["replied"] == 1            # Casey (green)
    assert d["needs_attention"] == 1    # Pat (red)
    assert d["unread_messages"] == 2
    assert d["urgent_replies"] == 1
    assert d["swelling_replies"] == 1
    assert d["patients_doing_well"] == 1
    assert d["pain_replies"] == 0


def test_dashboard_counts_pending_approval(ctx):
    client, ids, engine, sms = ctx
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
    assert client.get("/api/v1/dashboard").json()["pending_doctor_approval"] == 1


def test_analytics_summary(ctx):
    client, ids, engine, sms = ctx
    full_day(client, ids, engine, sms)
    login(client, "doctor@mock.test")
    a = client.get("/api/v1/analytics/summary").json()
    assert a["follow_ups"] == 2
    assert a["delivery_rate"] == 1.0
    assert a["response_rate"] == 1.0
    assert a["urgent_cases"] == 1
    assert a["reply_categories_pct"] == {"doing_well": 50.0, "swelling": 50.0}
    assert a["avg_response_minutes"] is not None

    # Filter by procedure narrows the population.
    crown_only = client.get("/api/v1/analytics/summary",
                            params={"procedure": "Crown"}).json()
    assert crown_only["follow_ups"] == 1


def test_staff_cannot_view_analytics(ctx):
    client, ids, engine, sms = ctx
    login(client, "staff@mock.test")
    assert client.get("/api/v1/analytics/summary").status_code == 403


def test_delivery_log_shows_sent_events(ctx):
    client, ids, engine, sms = ctx
    full_day(client, ids, engine, sms)
    login(client, "manager@mock.test")
    events = client.get("/api/v1/logs/delivery").json()
    assert len(events) == 2
    assert all(e["event"] == "sent" and e["provider"] == "mock" for e in events)


def test_audit_log_visible_to_admin_only(ctx):
    client, ids, engine, sms = ctx
    full_day(client, ids, engine, sms)
    login(client, "manager@mock.test")
    assert client.get("/api/v1/audit-log").status_code == 403
    login(client, "admin@mock.test")
    rows = client.get("/api/v1/audit-log").json()
    actions = {r["action"] for r in rows}
    assert {"auth.login", "intake.upload", "approval.approved", "message.sent"} <= actions
    # Filterable by action.
    only_sent = client.get("/api/v1/audit-log", params={"action": "message.sent"}).json()
    assert {r["action"] for r in only_sent} == {"message.sent"}
