"""Shared FastAPI dependencies: DB session, current user, permission guard."""

import datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .models import AuthSession, Practice, User, utcnow
from .rbac import Permission, has_permission
from .security import token_hash

SESSION_COOKIE = "smileflow_session"


def as_utc(dt: datetime.datetime) -> datetime.datetime:
    """SQLite returns naive datetimes; treat stored values as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def get_db(request: Request):
    factory = request.app.state.session_factory
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    sess = db.get(AuthSession, token_hash(token))
    if sess is None or sess.revoked_at is not None or as_utc(sess.expires_at) < utcnow():
        raise HTTPException(status_code=401, detail="session expired")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="account disabled")
    return user


def practice_settings(db: Session, user: User) -> dict:
    if not user.practice_id:
        return {}
    practice = db.get(Practice, user.practice_id)
    return practice.settings_json if practice else {}


def require(permission: Permission):
    def dep(
        user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ) -> User:
        settings = practice_settings(db, user)
        if not has_permission(
            user.role,
            permission,
            staff_can_send_replies=bool(settings.get("staff_can_send_replies", False)),
        ):
            raise HTTPException(status_code=403, detail="forbidden")
        return user

    return dep


def check_tenancy(user: User, practice_id: str | None) -> None:
    """404 (not 403) outside the tenant so resources aren't enumerable."""
    if user.practice_id is None:  # super admin
        return
    if practice_id != user.practice_id:
        raise HTTPException(status_code=404, detail="not found")
