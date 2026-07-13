"""Security hardening: rate limiting, security headers, webhook verification.

Webhook note: the shared-secret check below protects dev/staging and any
non-Twilio provider. Before the real Twilio webhook goes internet-facing,
provider signature verification (X-Twilio-Signature HMAC) MUST replace it —
tracked on the docs/05 checklist.
"""

import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class SlidingWindowLimiter:
    """Small in-memory per-key rate limiter (single-process MVP scale)."""

    def __init__(self, max_events: int, window_seconds: float):
        self.max_events = max_events
        self.window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        q = self._events[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.max_events:
            return False
        q.append(now)
        return True


def make_login_limiter() -> SlidingWindowLimiter:
    # 20 login attempts per IP per 5 minutes (account lockout is separate and
    # per-user; this throttles spraying across accounts). Lives on app.state
    # so each app instance — and each test — gets its own window.
    return SlidingWindowLimiter(max_events=20, window_seconds=300)


def enforce_login_rate_limit(request: Request) -> None:
    limiter: SlidingWindowLimiter = request.app.state.login_limiter
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(f"login:{ip}"):
        raise HTTPException(status_code=429, detail="too many attempts; try again later")


def verify_webhook_secret(request: Request) -> None:
    """Require X-Webhook-Secret when WEBHOOK_SHARED_SECRET is configured.

    Unset (dev/test with MOCK providers): open, as before. Set: constant-time
    compare. Production Twilio: replace with signature verification.
    """
    expected = os.environ.get("WEBHOOK_SHARED_SECRET", "")
    if not expected:
        return
    provided = request.headers.get("x-webhook-secret", "")
    if not hmac.compare_digest(expected.encode(), provided.encode()):
        raise HTTPException(status_code=401, detail="invalid webhook credentials")


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; frame-ancestors 'none'"
    ),
}


def install_security_headers(app) -> None:
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response
