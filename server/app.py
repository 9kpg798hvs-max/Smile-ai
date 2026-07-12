"""FastAPI application factory.

Wires the engine, session factory, OCR provider, and upload storage onto
app.state, then mounts the routers. Providers default to MOCK — production
deployments pass real adapters explicitly (docs/01).
"""

import os
from pathlib import Path

from fastapi import FastAPI

from .db import Base, make_engine, make_session_factory
from .ocr import MockOCRProvider
from .routes import auth as auth_routes
from .routes import intake as intake_routes

# Re-exports kept stable for tests and callers.
from .deps import SESSION_COOKIE  # noqa: F401
from .routes.auth import LOCKOUT_MINUTES, LOCKOUT_THRESHOLD, SESSION_TTL  # noqa: F401


def create_app(
    engine=None,
    cookie_secure: bool = True,
    ocr_provider=None,
    uploads_dir: str | Path | None = None,
) -> FastAPI:
    engine = engine or make_engine()
    Base.metadata.create_all(engine)

    app = FastAPI(title="SmileFlow AI", version="0.2.0")
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.cookie_secure = cookie_secure
    # MOCK by default: dev/test never talk to a real OCR backend.
    app.state.ocr_provider = ocr_provider or MockOCRProvider()
    app.state.uploads_dir = Path(
        uploads_dir or os.environ.get("UPLOADS_DIR", "var/uploads")
    )

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "mocked": {"ocr": app.state.ocr_provider.model == "MOCK"},
        }

    app.include_router(auth_routes.router)
    app.include_router(intake_routes.router)
    return app
