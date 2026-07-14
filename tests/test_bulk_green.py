"""Bulk-approve greens (SPEC §2.1 'All at once').

Load-bearing assertion: bulk sending only ever touches GREEN replies — a red
or yellow can never be swept in, even if its draft id is passed explicitly.
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
    DraftStatus,
    Message,
    MessageDirection,
    Patient,
    Practice,
    ReplyClassification,
    Urgency,
)
from server.scheduler import run_pending
from server.seed import DEFAULT_PASSWORD, seed
from server.sms import MockSMSProvider

from .mock_extractor import KeywordExtractor

OURS = "+15550100101"


@pytest.fixture()
def ctx(tmp_path):
    engine = make_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as db:
        data = seed(db)
        ids = {
            "office_id": data["office"].id,
            "doctor_id": data["users"]["doctor"].id,
            "practice_id": data["practice"].id,
            "patients": [p.id for p in data["patients"]],
        }
        db.commit()
    sms = MockSMSProvider()
    app = create_app(
        engine=engine, cookie_secure=False, sms_provider=sms,
        reply_extractor=KeywordExtractor(), uploads_dir=tmp_path / "u",
    )
    return TestClient(app), ids, engine, sms


def login(client, email):
    assert client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    ).status_code == 200


def setup_replies(client, ids, engine, sms):
    """Send check-ins to all three seeded patients, then have them reply:
    two green, one red."""
    login(client, "staff@mock.test")
    up = client.post(
        "/api/v1/schedule-uploads",
        files={"file": ("s.png", b"x", "image/png")},
        data={"office_id": ids["office_id"]},
    ).json()
    # Un-exclude the crossed-out third patient so all three get check-ins.
    for e in up["entries"]:
        client.patch(f"/api/v1/schedule-uploads/{up['id']}/entries/{e['id']}",
                     json={"resolved_doctor_id": ids["doctor_id"], "excluded": False})
    client.post(f"/api/v1/schedule-uploads/{up['id']}/submit")
    login(client, "doctor@mock.test")
    client.post(f"/api/v1/schedule-uploads/{up['id']}/approve")
    with make_session_factory(engine)() as db:
        run_pending(db, sms, now=datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(days=1))
        db.commit()
    replies = [
        ("+15550200100", "All good, zero pain, feeling great!"),        # green
        ("+15550200101", "No pain at all, doing good, thank you!"),     # green
        ("+15550200102", "My cheek is swelling since this morning"),    # red
    ]
    for frm, body in replies:
        client.post("/api/v1/webhooks/sms/inbound",
                    json={"from_e164": frm, "to_e164": OURS, "body": body})


def outbound_count(engine):
    with make_session_factory(engine)() as db:
        return len(db.execute(
            select(Message).where(Message.direction == MessageDirection.OUT,
                                  Message.sent_by.is_not(None))
        ).scalars().all())


def test_bulk_sends_only_greens(ctx):
    client, ids, engine, sms = ctx
    setup_replies(client, ids, engine, sms)
    login(client, "doctor@mock.test")

    r = client.post("/api/v1/drafts/bulk-approve-green", json={})
    assert r.status_code == 200
    assert r.json()["sent"] == 2                 # the two greens
    assert outbound_count(engine) == 2

    with make_session_factory(engine)() as db:
        # The red draft is still suggested — untouched.
        drafts = db.execute(
            select(AIDraft, ReplyClassification)
            .join(ReplyClassification, AIDraft.message_id == ReplyClassification.message_id)
        ).all()
        red = [d for d, c in drafts if c.urgency is Urgency.RED]
        assert red and all(d.status is DraftStatus.SUGGESTED for d in red)
        greens = [d for d, c in drafts if c.urgency is Urgency.GREEN]
        assert greens and all(d.status is DraftStatus.APPROVED for d in greens)


def test_red_cannot_be_forced_into_bulk_by_id(ctx):
    client, ids, engine, sms = ctx
    setup_replies(client, ids, engine, sms)

    with make_session_factory(engine)() as db:
        red_draft = db.execute(
            select(AIDraft)
            .join(ReplyClassification, AIDraft.message_id == ReplyClassification.message_id)
            .where(ReplyClassification.urgency == Urgency.RED)
        ).scalars().first()
        red_id = red_draft.id

    login(client, "doctor@mock.test")
    # Explicitly ask to bulk-send the red draft's id.
    r = client.post("/api/v1/drafts/bulk-approve-green", json={"draft_ids": [red_id]})
    assert r.status_code == 200
    assert r.json()["sent"] == 0                 # server refuses — green-only
    with make_session_factory(engine)() as db:
        assert db.get(AIDraft, red_id).status is DraftStatus.SUGGESTED


def test_opted_out_green_is_skipped(ctx):
    client, ids, engine, sms = ctx
    setup_replies(client, ids, engine, sms)
    with make_session_factory(engine)() as db:
        db.get(Patient, ids["patients"][0]).sms_opt_out = True
        db.commit()
    login(client, "doctor@mock.test")
    r = client.post("/api/v1/drafts/bulk-approve-green", json={}).json()
    assert r["sent"] == 1
    assert any("opted out" in s["reason"] for s in r["skipped"])


def test_staff_cannot_bulk_send_by_default(ctx):
    client, ids, engine, sms = ctx
    setup_replies(client, ids, engine, sms)
    login(client, "staff@mock.test")
    assert client.post("/api/v1/drafts/bulk-approve-green", json={}).status_code == 403


def test_staff_bulk_send_unlocked_by_practice_setting(ctx):
    client, ids, engine, sms = ctx
    setup_replies(client, ids, engine, sms)
    with make_session_factory(engine)() as db:
        p = db.get(Practice, ids["practice_id"])
        p.settings_json = {**p.settings_json, "staff_can_send_replies": True}
        db.commit()
    login(client, "staff@mock.test")
    assert client.post("/api/v1/drafts/bulk-approve-green", json={}).json()["sent"] == 2


def test_bulk_send_is_audited(ctx):
    client, ids, engine, sms = ctx
    setup_replies(client, ids, engine, sms)
    login(client, "doctor@mock.test")
    client.post("/api/v1/drafts/bulk-approve-green", json={})
    from server.models import AuditLog
    with make_session_factory(engine)() as db:
        actions = {a.action for a in db.execute(select(AuditLog)).scalars()}
    assert "reply.bulk_sent_green" in actions
    assert "reply.sent" in actions  # each individual send still audited too
