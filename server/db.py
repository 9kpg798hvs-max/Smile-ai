"""Database engine/session setup.

Dev/test: SQLite. Production: PostgreSQL via DATABASE_URL. Models are written
to be portable across both (String UUIDs, JSON columns, UTC datetimes).
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///smileflow.db")


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None, **kwargs):
    url = url or DATABASE_URL
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    return create_engine(url, **kwargs)


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
