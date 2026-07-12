"""FastAPI application factory.

Wires the engine, session factory, OCR provider, and upload storage onto
app.state, then mounts the routers. Providers default to MOCK — production
deployments pass real adapters explicitly (docs/01).
"""

import os
from pathlib import Path

from fastapi import FastAPI

from .ai import MockDraftProvider
from .db import Base, make_engine, make_session_factory
from .ocr import MockOCRProvider
from .routes import approval as approval_routes
from .routes import auth as auth_routes
from .routes import inbox as inbox_routes
from .routes import intake as intake_routes
from .sms import MockSMSProvider

# Re-exports kept stable for tests and callers.
from .deps import SESSION_COOKIE  # noqa: F401
from .routes.auth import LOCKOUT_MINUTES, LOCKOUT_THRESHOLD, SESSION_TTL  # noqa: F401


def create_app(
    engine=None,
    cookie_secure: bool = True,
    ocr_provider=None,
    sms_provider=None,
    reply_extractor=None,
    draft_provider=None,
    uploads_dir: str | Path | None = None,
    scheduler_interval: float | None = None,
) -> FastAPI:
    engine = engine or make_engine()
    Base.metadata.create_all(engine)

    app = FastAPI(title="SmileFlow AI", version="0.3.0")
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.cookie_secure = cookie_secure
    # MOCK by default: dev/test never talk to a real OCR or SMS backend.
    app.state.ocr_provider = ocr_provider or MockOCRProvider()
    app.state.sms_provider = sms_provider or MockSMSProvider()
    # None = construct the real Claude extractor lazily on first inbound reply
    # (fails safe to yellow + manual review if no API key is configured).
    app.state.reply_extractor = reply_extractor
    app.state.draft_provider = draft_provider or MockDraftProvider()
    app.state.uploads_dir = Path(
        uploads_dir or os.environ.get("UPLOADS_DIR", "var/uploads")
    )

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "mocked": {
                "ocr": app.state.ocr_provider.model == "MOCK",
                "sms": getattr(app.state.sms_provider, "name", "") == "mock",
                "reply_ai": (
                    "lazy-real" if app.state.reply_extractor is None
                    else getattr(app.state.reply_extractor, "model", "unknown")
                ),
                "drafts": getattr(app.state.draft_provider, "name", "") == "mock",
            },
        }

    app.include_router(auth_routes.router)
    app.include_router(intake_routes.router)
    app.include_router(approval_routes.router)
    app.include_router(inbox_routes.router)

    interval = scheduler_interval
    if interval is None:
        interval = float(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "0"))
    if interval > 0:
        _attach_send_worker(app, interval)
    return app


def _attach_send_worker(app: FastAPI, interval: float) -> None:
    """Background loop that sends due, doctor-approved follow-ups."""
    import asyncio
    import logging

    from .scheduler import run_pending

    logger = logging.getLogger("smileflow.scheduler")

    async def worker():
        while True:
            try:
                with app.state.session_factory() as db:
                    stats = run_pending(db, app.state.sms_provider)
                    db.commit()
                    if stats["due"]:
                        logger.info("send worker: %s", stats)
            except Exception:
                logger.exception("send worker iteration failed")
            await asyncio.sleep(interval)

    @app.on_event("startup")
    async def start_worker():
        app.state._send_worker = asyncio.get_event_loop().create_task(worker())

    @app.on_event("shutdown")
    async def stop_worker():
        task = getattr(app.state, "_send_worker", None)
        if task:
            task.cancel()
