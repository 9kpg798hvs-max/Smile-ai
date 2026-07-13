"""Auth endpoints: login (with lockout), logout, me."""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..deps import SESSION_COOKIE, get_current_user, get_db, practice_settings
from ..models import AuthSession, Role, User, utcnow
from ..rbac import Permission, has_permission
from ..security import new_session_token, token_hash, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SESSION_TTL = datetime.timedelta(hours=12)
LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15


class LoginRequest(BaseModel):
    # Email is the login identifier; deliverability validation (EmailStr)
    # deliberately not applied — it rejects reserved test domains.
    email: str
    password: str


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: Role
    practice_id: str | None
    permissions: list[Permission]


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    from ..hardening import enforce_login_rate_limit

    enforce_login_rate_limit(request)
    user = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    now = utcnow()

    if user is not None and user.locked_until is not None and _as_utc(user.locked_until) > now:
        raise HTTPException(status_code=423, detail="account temporarily locked")

    if user is None or not verify_password(body.password, user.password_hash):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= LOCKOUT_THRESHOLD:
                user.locked_until = now + datetime.timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_logins = 0
            audit(db, "auth.login_failed", user_id=user.id, practice_id=user.practice_id,
                  ip=_client_ip(request))
            # The HTTPException below triggers a rollback in get_db;
            # the failure counter and audit row must survive it.
            db.commit()
        raise HTTPException(status_code=401, detail="invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="invalid credentials")

    user.failed_logins = 0
    user.locked_until = None
    token = new_session_token()
    db.add(AuthSession(
        token_hash=token_hash(token),
        user_id=user.id,
        expires_at=now + SESSION_TTL,
        ip=_client_ip(request),
    ))
    audit(db, "auth.login", user_id=user.id, practice_id=user.practice_id, ip=_client_ip(request))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=bool(getattr(request.app.state, "cookie_secure", True)),
        samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return {"ok": True, "role": user.role}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sess = db.get(AuthSession, token_hash(token))
        if sess is not None:
            sess.revoked_at = utcnow()
            audit(db, "auth.logout", user_id=sess.user_id)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = practice_settings(db, user)
    staff_can_send = bool(settings.get("staff_can_send_replies", False))
    perms = [
        p for p in Permission
        if has_permission(user.role, p, staff_can_send_replies=staff_can_send)
    ]
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        practice_id=user.practice_id,
        permissions=perms,
    )
