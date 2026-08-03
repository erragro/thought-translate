"""
app/auth/routes.py
====================
Authentication endpoints: signup, login, logout, token refresh, OAuth flows.

Ported from kirana_kart_final's auth_routes.py. Kept: rate limiting
(slowapi), Redis-backed account lockout, password policy, HttpOnly +
Secure + SameSite cookies, PII-safe logging (user_id not email), OAuth
tokens delivered via cookie not URL. Dropped: DPDP consent_records
insert (not a requirement here — revisit if this ever needs India-specific
compliance).

Endpoints:
    POST /auth/signup
    POST /auth/login
    POST /auth/refresh
    POST /auth/logout
    GET  /auth/me
    GET  /auth/oauth/{github,google,microsoft}
    GET  /auth/oauth/{github,google,microsoft}/callback
"""

from __future__ import annotations

import logging
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import text

from app.auth.oauth_service import (
    OAuthUserInfo,
    exchange_github_code,
    exchange_google_code,
    exchange_microsoft_code,
    get_github_oauth_url,
    get_google_oauth_url,
    get_microsoft_oauth_url,
)
from app.auth.service import (
    UserContext,
    assign_default_permissions,
    build_user_context_from_db,
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_password,
    invalidate_refresh_token,
    store_refresh_token,
    validate_and_rotate_refresh_token,
    verify_password,
)
from app.config import settings
from app.db import get_db_session
from app.rate_limiter import limiter
from app.redis_client import get_redis

logger = logging.getLogger("thought_translate.auth_routes")

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------

_MIN_PASSWORD_LENGTH = 12
_PASSWORD_POLICY_MSG = (
    "Password must be at least 12 characters and contain an uppercase letter, "
    "a digit, and a special character (!@#$%^&*()-_+=)"
)

# ---------------------------------------------------------------------------
# Account lockout
# ---------------------------------------------------------------------------

_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_SECONDS = 15 * 60
_LOCKOUT_KEY_PREFIX = "login_fail:"
_LOCKOUT_FLAG_PREFIX = "login_locked:"

# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------

_ACCESS_COOKIE = "tt_access"
_REFRESH_COOKIE = "tt_refresh"
_COOKIE_SECURE = settings.deployment_env == "production"
_COOKIE_SAMESITE = "strict"


class SignupRequest(BaseModel):
    email: str
    password: str
    full_name: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email address")
        return v

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < _MIN_PASSWORD_LENGTH:
            raise ValueError(_PASSWORD_POLICY_MSG)
        if not re.search(r"[A-Z]", v):
            raise ValueError(_PASSWORD_POLICY_MSG)
        if not re.search(r"[0-9]", v):
            raise ValueError(_PASSWORD_POLICY_MSG)
        if not re.search(r"[!@#$%^&*()\-_+=]", v):
            raise ValueError(_PASSWORD_POLICY_MSG)
        return v


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return v.strip().lower()


# ---------------------------------------------------------------------------
# Account lockout helpers
# ---------------------------------------------------------------------------


def _check_lockout(email: str) -> None:
    try:
        r = get_redis()
        if r.get(f"{_LOCKOUT_FLAG_PREFIX}{email}"):
            raise HTTPException(
                status_code=429,
                detail="Account temporarily locked due to too many failed attempts. Try again in 15 minutes.",
                headers={"Retry-After": str(_LOCKOUT_SECONDS)},
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Redis unavailable — fail open


def _record_failed_attempt(email: str) -> None:
    try:
        r = get_redis()
        key = f"{_LOCKOUT_KEY_PREFIX}{email}"
        count = r.incr(key)
        r.expire(key, _LOCKOUT_SECONDS)
        if count >= _MAX_FAILED_ATTEMPTS:
            r.setex(f"{_LOCKOUT_FLAG_PREFIX}{email}", _LOCKOUT_SECONDS, "1")
    except Exception:
        pass


def _clear_failed_attempts(email: str) -> None:
    try:
        r = get_redis()
        r.delete(f"{_LOCKOUT_KEY_PREFIX}{email}")
        r.delete(f"{_LOCKOUT_FLAG_PREFIX}{email}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key=_ACCESS_COOKIE,
        value=access_token,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite=_COOKIE_SAMESITE,
        max_age=settings.jwt_access_expire_minutes * 60,
        path="/",
    )
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite=_COOKIE_SAMESITE,
        max_age=settings.jwt_refresh_expire_days * 86400,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(_ACCESS_COOKIE, path="/")
    response.delete_cookie(_REFRESH_COOKIE, path="/")


def _token_response(user: UserContext, refresh_raw: str, response: Response) -> dict:
    access_token = create_access_token(user)
    _set_auth_cookies(response, access_token, refresh_raw)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": refresh_raw,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "avatar_url": user.avatar_url,
            "is_super_admin": user.is_super_admin,
            "permissions": user.permissions,
        },
    }


# ---------------------------------------------------------------------------
# Sign up
# ---------------------------------------------------------------------------


@router.post("/signup")
@limiter.limit("5/minute")
def signup(payload: SignupRequest, request: Request, response: Response):
    with get_db_session() as session:
        existing = session.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": payload.email},
        ).scalar()
        if existing:
            raise HTTPException(status_code=409, detail="An account with this email already exists")

        hashed = hash_password(payload.password)
        row = session.execute(
            text("""
                INSERT INTO users (email, full_name, password_hash, is_active, is_super_admin)
                VALUES (:email, :name, :hash, TRUE, FALSE)
                RETURNING id
            """),
            {"email": payload.email, "name": payload.full_name, "hash": hashed},
        ).mappings().first()

        user_id = row["id"]
        assign_default_permissions(user_id, session)

    user = build_user_context_from_db(user_id)
    refresh_raw, refresh_hash = create_refresh_token(user_id)
    store_refresh_token(user_id, refresh_hash)

    logger.info("New user registered [id=%d]", user_id)
    return _token_response(user, refresh_raw, response)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@router.post("/login")
@limiter.limit("10/minute")
def login(payload: LoginRequest, request: Request, response: Response):
    _check_lockout(payload.email)

    with get_db_session() as session:
        row = session.execute(
            text("""
                SELECT id, password_hash, is_active
                FROM users
                WHERE email = :email AND oauth_provider IS NULL
            """),
            {"email": payload.email},
        ).mappings().first()

    if not row:
        _record_failed_attempt(payload.email)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Account is deactivated. Contact your administrator.")
    if not row["password_hash"] or not verify_password(payload.password, row["password_hash"]):
        _record_failed_attempt(payload.email)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    _clear_failed_attempts(payload.email)

    user = build_user_context_from_db(row["id"])
    refresh_raw, refresh_hash = create_refresh_token(row["id"])
    store_refresh_token(row["id"], refresh_hash)

    return _token_response(user, refresh_raw, response)


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


@router.post("/refresh")
def refresh_token(request: Request, response: Response):
    """Reads the refresh token from the HttpOnly cookie; falls back to
    JSON body for API clients that can't use cookies."""
    raw_refresh = request.cookies.get(_REFRESH_COOKIE)
    if not raw_refresh:
        import json
        try:
            body = request.scope.get("body", b"")
            if body:
                data = json.loads(body)
                raw_refresh = data.get("refresh_token")
        except Exception:
            pass
    if not raw_refresh:
        raise HTTPException(status_code=401, detail="Refresh token required")

    user_id, new_refresh_raw = validate_and_rotate_refresh_token(raw_refresh)
    user = build_user_context_from_db(user_id)
    new_access = create_access_token(user)

    _set_auth_cookies(response, new_access, new_refresh_raw)

    return {
        "access_token": new_access,
        "token_type": "bearer",
        "refresh_token": new_refresh_raw,
    }


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.post("/logout")
def logout(request: Request, response: Response, _user: UserContext = Depends(get_current_user)):
    raw_refresh = request.cookies.get(_REFRESH_COOKIE)
    if raw_refresh:
        try:
            invalidate_refresh_token(raw_refresh)
        except Exception:
            pass
    _clear_auth_cookies(response)
    return {"status": "logged out"}


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


@router.get("/me")
def me(user: UserContext = Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
        "is_super_admin": user.is_super_admin,
        "permissions": user.permissions,
    }


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------


def _upsert_oauth_user(info: OAuthUserInfo) -> int:
    with get_db_session() as session:
        row = session.execute(
            text("SELECT id FROM users WHERE oauth_provider = :provider AND oauth_id = :oid"),
            {"provider": info.provider, "oid": info.oauth_id},
        ).mappings().first()

        if row:
            session.execute(
                text("""
                    UPDATE users
                    SET email = :email, full_name = :name, avatar_url = :avatar, updated_at = NOW()
                    WHERE id = :uid
                """),
                {"email": info.email, "name": info.full_name, "avatar": info.avatar_url, "uid": row["id"]},
            )
            return row["id"]

        email_row = session.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": info.email},
        ).mappings().first()

        if email_row:
            session.execute(
                text("""
                    UPDATE users
                    SET oauth_provider = :provider, oauth_id = :oid, avatar_url = :avatar, updated_at = NOW()
                    WHERE id = :uid
                """),
                {"provider": info.provider, "oid": info.oauth_id, "avatar": info.avatar_url, "uid": email_row["id"]},
            )
            return email_row["id"]

        new_row = session.execute(
            text("""
                INSERT INTO users (email, full_name, oauth_provider, oauth_id, avatar_url, is_active)
                VALUES (:email, :name, :provider, :oid, :avatar, TRUE)
                RETURNING id
            """),
            {
                "email": info.email,
                "name": info.full_name,
                "provider": info.provider,
                "oid": info.oauth_id,
                "avatar": info.avatar_url,
            },
        ).mappings().first()

        user_id = new_row["id"]
        assign_default_permissions(user_id, session)
        logger.info("New OAuth user registered [id=%d via %s]", user_id, info.provider)
        return user_id


def _oauth_complete(user_id: int) -> RedirectResponse:
    user = build_user_context_from_db(user_id)
    refresh_raw, refresh_hash = create_refresh_token(user_id)
    store_refresh_token(user_id, refresh_hash)
    access_token = create_access_token(user)

    response = RedirectResponse(url=f"{settings.frontend_url}/auth/callback")
    _set_auth_cookies(response, access_token, refresh_raw)
    return response


def _oauth_error_redirect(detail: str) -> RedirectResponse:
    from urllib.parse import urlencode
    params = urlencode({"error": detail})
    return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{params}")


# ---------------------------------------------------------------------------
# GitHub OAuth
# ---------------------------------------------------------------------------


@router.get("/oauth/github")
def github_login():
    state = secrets.token_urlsafe(16)
    return RedirectResponse(url=get_github_oauth_url(state))


@router.get("/oauth/github/callback")
def github_callback(code: str | None = None, error: str | None = None):
    if error or not code:
        return _oauth_error_redirect(error or "GitHub login cancelled")
    try:
        info = exchange_github_code(code)
        user_id = _upsert_oauth_user(info)
        return _oauth_complete(user_id)
    except HTTPException as exc:
        return _oauth_error_redirect(exc.detail)
    except Exception as exc:
        logger.error("GitHub OAuth error: %s", exc)
        return _oauth_error_redirect("GitHub authentication failed")


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------


@router.get("/oauth/google")
def google_login():
    state = secrets.token_urlsafe(16)
    return RedirectResponse(url=get_google_oauth_url(state))


@router.get("/oauth/google/callback")
def google_callback(code: str | None = None, error: str | None = None):
    if error or not code:
        return _oauth_error_redirect(error or "Google login cancelled")
    try:
        info = exchange_google_code(code)
        user_id = _upsert_oauth_user(info)
        return _oauth_complete(user_id)
    except HTTPException as exc:
        return _oauth_error_redirect(exc.detail)
    except Exception as exc:
        logger.error("Google OAuth error: %s", exc)
        return _oauth_error_redirect("Google authentication failed")


# ---------------------------------------------------------------------------
# Microsoft OAuth
# ---------------------------------------------------------------------------


@router.get("/oauth/microsoft")
def microsoft_login():
    state = secrets.token_urlsafe(16)
    return RedirectResponse(url=get_microsoft_oauth_url(state))


@router.get("/oauth/microsoft/callback")
def microsoft_callback(code: str | None = None, error: str | None = None):
    if error or not code:
        return _oauth_error_redirect(error or "Microsoft login cancelled")
    try:
        info = exchange_microsoft_code(code)
        user_id = _upsert_oauth_user(info)
        return _oauth_complete(user_id)
    except HTTPException as exc:
        return _oauth_error_redirect(exc.detail)
    except Exception as exc:
        logger.error("Microsoft OAuth error: %s", exc)
        return _oauth_error_redirect("Microsoft authentication failed")
