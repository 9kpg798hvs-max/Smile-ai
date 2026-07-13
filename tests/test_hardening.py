"""Phase 7 (partial) tests: rate limiting, security headers, webhook secret."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from server.app import create_app
from server.db import Base, make_engine, make_session_factory
from server.hardening import SlidingWindowLimiter
from server.seed import DEFAULT_PASSWORD, seed


@pytest.fixture()
def client(tmp_path):
    engine = make_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as db:
        seed(db)
        db.commit()
    app = create_app(engine=engine, cookie_secure=False, uploads_dir=tmp_path / "u")
    return TestClient(app)


def test_sliding_window_limiter():
    limiter = SlidingWindowLimiter(max_events=3, window_seconds=10)
    assert all(limiter.check("k", now=t) for t in (0.0, 1.0, 2.0))
    assert limiter.check("k", now=3.0) is False        # 4th inside window
    assert limiter.check("other", now=3.0) is True     # keys independent
    assert limiter.check("k", now=11.5) is True        # window slid


def test_login_rate_limited_after_spray(client):
    for _ in range(20):
        client.post("/api/v1/auth/login",
                    json={"email": "spray@mock.test", "password": "x"})
    r = client.post("/api/v1/auth/login",
                    json={"email": "staff@mock.test", "password": DEFAULT_PASSWORD})
    assert r.status_code == 429


def test_security_headers_present(client):
    r = client.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_webhook_secret_enforced_when_configured(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SHARED_SECRET", "topsecret")
    payload = {"from_e164": "+15550200100", "to_e164": "+15550100101", "body": "ok"}
    assert client.post("/api/v1/webhooks/sms/inbound", json=payload).status_code == 401
    assert client.post(
        "/api/v1/webhooks/sms/inbound", json=payload,
        headers={"X-Webhook-Secret": "wrong"},
    ).status_code == 401
    assert client.post(
        "/api/v1/webhooks/sms/inbound", json=payload,
        headers={"X-Webhook-Secret": "topsecret"},
    ).status_code == 200
    assert client.post(
        "/api/v1/webhooks/sms/status",
        json={"provider_message_id": "x", "event": "delivered"},
    ).status_code == 401  # status webhook covered too


def test_webhook_open_when_secret_unset(client, monkeypatch):
    monkeypatch.delenv("WEBHOOK_SHARED_SECRET", raising=False)
    payload = {"from_e164": "+15550200100", "to_e164": "+15550100101", "body": "ok"}
    assert client.post("/api/v1/webhooks/sms/inbound", json=payload).status_code == 200
