import hashlib
import os
import secrets
import re
import uuid
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import LoginSession, User, now

COOKIE = "flamoris.studio"
CSRF_COOKIE = "flamoris.studio.csrf"
hasher = PasswordHasher()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def database(request: Request):
    with request.app.state.session_factory() as session:
        yield session


def current_user(request: Request, db: Session = Depends(database)) -> uuid.UUID:
    token = request.cookies.get(COOKIE, "")
    if not token:
        raise HTTPException(401)
    login = db.get(LoginSession, digest(token))
    if login is None or login.expires_at <= now() or db.get(User, login.user_id) is None:
        raise HTTPException(401)
    return login.user_id


def start_session(response, db: Session, user_id: uuid.UUID):
    token = secrets.token_urlsafe(48)
    db.add(LoginSession(token_hash=digest(token), user_id=user_id, expires_at=now() + timedelta(hours=12)))
    db.commit()
    secure = os.getenv("STUDIO_DEV_INSECURE_COOKIE") != "1"
    response.set_cookie(COOKIE, token, httponly=True, secure=secure, samesite="strict", max_age=43200)


def new_csrf(response, existing: str | None = None):
    # Tabs share a browser cookie jar. Reuse a well-formed token so one tab's
    # session refresh does not invalidate another tab's cached header token.
    if existing and re.fullmatch(r"[A-Za-z0-9_-]{43}", existing):
        return existing
    value = secrets.token_urlsafe(32)
    response.set_cookie(CSRF_COOKIE, value, httponly=True,
                        secure=os.getenv("STUDIO_DEV_INSECURE_COOKIE") != "1", samesite="strict")
    return value


def require_csrf(request: Request):
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("X-CSRF-TOKEN")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(403, "Invalid CSRF token")
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Invalid origin")


def clear_session(response, db: Session, token: str):
    if token:
        login = db.get(LoginSession, digest(token))
        if login:
            db.delete(login)
            db.commit()
    response.delete_cookie(COOKIE)
