"""API tests for login/logout/me, lockout, and session semantics."""

import pytest
from fastapi.testclient import TestClient

from server.app import LOCKOUT_THRESHOLD, SESSION_COOKIE, create_app
from server.db import Base, make_engine, make_session_factory
from server.seed import DEFAULT_PASSWORD, seed
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def client():
    engine = make_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        seed(db)
        db.commit()
    app = create_app(engine=engine, cookie_secure=False)
    return TestClient(app)


def login(client, email="doctor@mock.test", password=DEFAULT_PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_health_reports_mock_providers(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["mocked"]["ocr"] is True  # dev/test must never use a real backend


def test_login_success_sets_httponly_cookie(client):
    r = login(client)
    assert r.status_code == 200
    set_cookie = r.headers["set-cookie"]
    assert SESSION_COOKIE in set_cookie
    assert "HttpOnly" in set_cookie


def test_wrong_password_rejected(client):
    r = login(client, password="wrong")
    assert r.status_code == 401


def test_unknown_user_rejected_with_same_error(client):
    r = login(client, email="nobody@mock.test")
    assert r.status_code == 401
    assert r.json() == login(client, password="wrong").json()  # no user enumeration


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_returns_role_and_permissions(client):
    login(client)
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "doctor"
    assert "approve_patient_list" in body["permissions"]
    assert "send_reply" in body["permissions"]
    assert "view_audit_logs" not in body["permissions"]


def test_staff_permissions_exclude_send_reply_by_default(client):
    login(client, email="staff@mock.test")
    perms = client.get("/api/v1/auth/me").json()["permissions"]
    assert "send_reply" not in perms
    assert "upload_schedule" in perms


def test_logout_revokes_session_server_side(client):
    login(client)
    assert client.get("/api/v1/auth/me").status_code == 200
    cookie = client.cookies.get(SESSION_COOKIE)
    client.post("/api/v1/auth/logout")
    # Re-present the old cookie: must be rejected (revoked in DB, not just cleared).
    r = client.get("/api/v1/auth/me", cookies={SESSION_COOKIE: cookie})
    assert r.status_code == 401


def test_lockout_after_repeated_failures(client):
    for _ in range(LOCKOUT_THRESHOLD):
        login(client, password="wrong")
    r = login(client)  # correct password, but now locked
    assert r.status_code == 423


def test_inactive_user_cannot_login(client):
    # Deactivate the staff user directly in the DB.
    from server.models import User
    from sqlalchemy import select

    engine = client.app.state.engine
    factory = make_session_factory(engine)
    with factory() as db:
        user = db.execute(select(User).where(User.email == "staff@mock.test")).scalar_one()
        user.is_active = False
        db.commit()
    assert login(client, email="staff@mock.test").status_code == 401


def test_login_and_logout_are_audited(client):
    login(client)
    client.post("/api/v1/auth/logout")
    from server.models import AuditLog
    from sqlalchemy import select

    factory = make_session_factory(client.app.state.engine)
    with factory() as db:
        actions = {a.action for a in db.execute(select(AuditLog)).scalars()}
    assert {"auth.login", "auth.logout"} <= actions
