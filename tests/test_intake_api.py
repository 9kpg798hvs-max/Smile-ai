"""Schedule intake API tests (screens 4–6) — workflow requirements 1–5."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from server.app import create_app
from server.db import Base, make_engine, make_session_factory
from server.models import AuditLog, Notification, Practice, UploadStatus, User
from server.seed import DEFAULT_PASSWORD, seed


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
    app = create_app(engine=engine, cookie_secure=False, uploads_dir=tmp_path / "uploads")
    client = TestClient(app)
    return client, ids, engine


def login(client, email="staff@mock.test"):
    r = client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 200
    return r


def do_upload(client, ids, filename="daysheet.png", mime="image/png"):
    return client.post(
        "/api/v1/schedule-uploads",
        files={"file": (filename, b"fake-image-bytes", mime)},
        data={"office_id": ids["office_id"]},
    )


def test_staff_can_upload_and_ocr_extracts_entries(ctx):
    client, ids, _ = ctx
    login(client)
    r = do_upload(client, ids)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "extracted"
    assert body["ocr_model"] == "MOCK"  # labeled mocked integration
    assert len(body["entries"]) == 3


def test_crossed_out_patient_arrives_excluded_from_ocr(ctx):
    client, ids, _ = ctx
    login(client)
    entries = do_upload(client, ids).json()["entries"]
    crossed = [e for e in entries if e["ocr_crossed_out"]]
    assert len(crossed) == 1
    assert crossed[0]["excluded"] is True
    assert crossed[0]["exclusion_source"] == "ocr"


def test_low_confidence_phone_is_reported_for_ui_flagging(ctx):
    client, ids, _ = ctx
    login(client)
    entries = do_upload(client, ids).json()["entries"]
    assert any(e["ocr_confidence"].get("phone", 1.0) < 0.7 for e in entries)


def test_unsupported_file_type_rejected(ctx):
    client, ids, _ = ctx
    login(client)
    r = do_upload(client, ids, filename="notes.txt", mime="text/plain")
    assert r.status_code == 415


def test_staff_can_override_ocr_exclusion(ctx):
    client, ids, _ = ctx
    login(client)
    up = do_upload(client, ids).json()
    crossed = next(e for e in up["entries"] if e["ocr_crossed_out"])
    r = client.patch(
        f"/api/v1/schedule-uploads/{up['id']}/entries/{crossed['id']}",
        json={"excluded": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["excluded"] is False
    assert body["exclusion_source"] == "staff"  # override recorded (workflow req #3)


def test_edit_add_remove_entries(ctx):
    client, ids, _ = ctx
    login(client)
    up = do_upload(client, ids).json()
    first = up["entries"][0]

    r = client.patch(
        f"/api/v1/schedule-uploads/{up['id']}/entries/{first['id']}",
        json={"phone_raw": "+15550209999"},
    )
    assert r.status_code == 200 and r.json()["phone"] == "+15550209999"

    r = client.post(
        f"/api/v1/schedule-uploads/{up['id']}/entries",
        json={"patient_name_raw": "Added Mockson", "phone_raw": "+15550208888"},
    )
    assert r.status_code == 201

    r = client.delete(
        f"/api/v1/schedule-uploads/{up['id']}/entries/{up['entries'][1]['id']}"
    )
    assert r.status_code == 204

    remaining = client.get(f"/api/v1/schedule-uploads/{up['id']}").json()["entries"]
    names = {e["patient_name"] for e in remaining}
    assert "Added Mockson" in names and len(remaining) == 3


def test_submit_requires_resolved_doctor_then_succeeds_and_notifies(ctx):
    client, ids, engine = ctx
    login(client)
    up = do_upload(client, ids).json()

    r = client.post(f"/api/v1/schedule-uploads/{up['id']}/submit")
    assert r.status_code == 400  # no treating doctor assigned yet

    for e in up["entries"]:
        client.patch(
            f"/api/v1/schedule-uploads/{up['id']}/entries/{e['id']}",
            json={"resolved_doctor_id": ids["doctor_id"]},
        )
    r = client.post(f"/api/v1/schedule-uploads/{up['id']}/submit")
    assert r.status_code == 200 and r.json()["status"] == "submitted"

    factory = make_session_factory(engine)
    with factory() as db:
        notes = db.execute(
            select(Notification).where(Notification.user_id == ids["doctor_id"])
        ).scalars().all()
    assert any(n.type == "doctor_approval_required" for n in notes)


def test_entries_locked_after_submission(ctx):
    client, ids, _ = ctx
    login(client)
    up = do_upload(client, ids).json()
    for e in up["entries"]:
        client.patch(
            f"/api/v1/schedule-uploads/{up['id']}/entries/{e['id']}",
            json={"resolved_doctor_id": ids["doctor_id"]},
        )
    client.post(f"/api/v1/schedule-uploads/{up['id']}/submit")
    r = client.patch(
        f"/api/v1/schedule-uploads/{up['id']}/entries/{up['entries'][0]['id']}",
        json={"phone_raw": "+15550207777"},
    )
    assert r.status_code == 409


def test_tenancy_other_practice_cannot_see_upload(ctx):
    client, ids, engine = ctx
    login(client)
    up = do_upload(client, ids).json()

    # Create a user in a different practice directly in the DB.
    from server.security import hash_password
    from server.models import Role

    factory = make_session_factory(engine)
    with factory() as db:
        other = Practice(name="Other Practice (MOCK)")
        db.add(other)
        db.flush()
        db.add(User(
            practice_id=other.id,
            email="outsider@mock.test",
            password_hash=hash_password(DEFAULT_PASSWORD),
            full_name="Otto Outsider (MOCK)",
            role=Role.PRACTICE_ADMIN,
        ))
        db.commit()

    outsider = TestClient(client.app)
    login(outsider, email="outsider@mock.test")
    assert outsider.get(f"/api/v1/schedule-uploads/{up['id']}").status_code == 404


def test_unauthenticated_cannot_upload(ctx):
    client, ids, _ = ctx
    assert do_upload(client, ids).status_code == 401


def test_ocr_failure_leaves_upload_usable_for_manual_entry(ctx, tmp_path):
    class ExplodingOCR:
        model = "MOCK"
        prompt_version = "mock"

        def extract(self, content, mime):
            raise RuntimeError("simulated OCR failure")

    client, ids, engine = ctx
    app = create_app(
        engine=engine, cookie_secure=False,
        ocr_provider=ExplodingOCR(), uploads_dir=tmp_path / "u2",
    )
    c2 = TestClient(app)
    login(c2)
    r = do_upload(c2, ids)
    assert r.status_code == 201
    body = r.json()
    assert body["entries"] == []  # no rows, but the upload survives
    # Staff can proceed manually on the same upload.
    r = c2.post(
        f"/api/v1/schedule-uploads/{body['id']}/entries",
        json={
            "patient_name_raw": "Manual Mockson",
            "phone_raw": "+15550206666",
            "resolved_doctor_id": ids["doctor_id"],
        },
    )
    assert r.status_code == 201
    r = c2.post(f"/api/v1/schedule-uploads/{body['id']}/submit")
    assert r.status_code == 200


def test_intake_actions_are_audited(ctx):
    client, ids, engine = ctx
    login(client)
    up = do_upload(client, ids).json()
    client.patch(
        f"/api/v1/schedule-uploads/{up['id']}/entries/{up['entries'][0]['id']}",
        json={"excluded": True, "exclusion_reason": "patient asked not to be texted"},
    )
    factory = make_session_factory(engine)
    with factory() as db:
        actions = {a.action for a in db.execute(select(AuditLog)).scalars()}
    assert {"intake.upload", "intake.entry_edited"} <= actions
