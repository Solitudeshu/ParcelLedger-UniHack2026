import hashlib
import secrets
from datetime import UTC, timedelta
from typing import Annotated

from fastapi import Depends, Request, Response
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.config import settings
from services.api.database import AuthSession, Shipment, User, get_db, now
from services.api.errors import DomainError

passwords = PasswordHash.recommended()
Db = Annotated[Session, Depends(get_db)]
COOKIE = "kc_session"
DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def issue_session(db: Session, user: User, response: Response):
    token = secrets.token_urlsafe(32)
    session = AuthSession(
        id=token_hash(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=now() + timedelta(hours=settings().session_hours),
    )
    db.add(session)
    db.commit()
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings().app_env == "production",
        path="/",
        max_age=settings().session_hours * 3600,
    )
    return {"user": user, "csrf_token": session.csrf_token}


def current_user(request: Request, db: Db) -> User:
    raw = request.cookies.get(COOKIE)
    session = db.get(AuthSession, token_hash(raw)) if raw else None
    if session is None or aware(session.expires_at) <= now():
        raise DomainError("UNAUTHENTICATED", "Vui lòng đăng nhập để mở hồ sơ.", 401)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(supplied, session.csrf_token):
            raise DomainError("CSRF_INVALID", "Phiên xác nhận chưa hợp lệ. Hãy tải lại trang.", 403)
        origin = request.headers.get("origin")
        if origin and origin != settings().web_origin:
            raise DomainError("ORIGIN_INVALID", "Nguồn yêu cầu không hợp lệ.", 403)
    request.state.auth_session = session
    return db.get(User, session.user_id)


Actor = Annotated[User, Depends(current_user)]


def shipment_access(db: Session, shipment_id: str, actor: User, *, lock=False) -> Shipment:
    statement = select(Shipment).where(Shipment.id == shipment_id)
    if lock:
        statement = statement.with_for_update()
    shipment = db.scalar(statement)
    if shipment is None or not (
        shipment.organization_id == actor.organization_id or shipment.buyer_id == actor.id
    ):
        raise DomainError("NOT_FOUND", "Không tìm thấy hồ sơ bạn có quyền truy cập.", 404)
    return shipment


def role(shipment: Shipment, actor: User) -> str:
    return "BUYER" if shipment.buyer_id == actor.id else "MERCHANT"


def require_role(shipment: Shipment, actor: User, expected: str):
    if role(shipment, actor) != expected:
        raise DomainError("FORBIDDEN", "Vai trò của bạn không được thực hiện thao tác này.", 403)
