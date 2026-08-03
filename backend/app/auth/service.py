"""
app/auth/service.py
====================
JWT authentication service — token creation, validation, and FastAPI
dependency injection helpers for protecting routes with RBAC.

Ported from kirana_kart_final's app/admin/services/auth_service.py:
same JWT/bcrypt/refresh-token-rotation/RBAC design, adapted to this
project's own module list and plain (non-namespaced) tables.

Exported FastAPI dependencies:
    get_current_user    — decodes JWT, returns UserContext
    require_permission  — factory that returns a dependency checking
                          a specific module+action permission

Database helpers (called once at startup):
    ensure_auth_tables      — creates users / user_permissions / refresh_tokens
    ensure_bootstrap_admin  — inserts an admin user if no users exist
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import text

from app.config import settings
from app.db import get_db_session

logger = logging.getLogger("thought_translate.auth")

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Modules that carry per-user permissions. Extend as real features land
# (e.g. "translate" today; a future "billing" or "team" module later).
ALL_MODULES: list[str] = ["translate", "admin"]

# Modules where a new user's default view = False (admin must grant explicitly)
ADMIN_ONLY_MODULES: set[str] = {"admin"}


@dataclass
class UserContext:
    id: int
    email: str
    full_name: str
    avatar_url: str | None
    is_super_admin: bool
    permissions: dict[str, dict[str, bool]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def _permissions_from_db(user_id: int) -> dict[str, dict[str, bool]]:
    with get_db_session() as session:
        rows = session.execute(
            text("""
                SELECT module, can_view, can_edit, can_admin
                FROM user_permissions
                WHERE user_id = :uid
            """),
            {"uid": user_id},
        ).mappings().all()

    return {
        r["module"]: {
            "view": bool(r["can_view"]),
            "edit": bool(r["can_edit"]),
            "admin": bool(r["can_admin"]),
        }
        for r in rows
    }


def create_access_token(user: UserContext) -> str:
    """Short-lived JWT access token embedding the user's permissions."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_access_expire_minutes)

    payload: dict[str, Any] = {
        "sub": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
        "is_super_admin": user.is_super_admin,
        "permissions": user.permissions,
        "iat": now,
        "exp": expire,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int) -> tuple[str, str]:
    """Returns (raw_token, token_hash) — store the hash, hand the raw to the client."""
    raw = secrets.token_urlsafe(64)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, token_hash


def _hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def store_refresh_token(user_id: int, token_hash: str) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expire_days)
    with get_db_session() as session:
        session.execute(
            text("""
                INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
                VALUES (:uid, :hash, :exp)
            """),
            {"uid": user_id, "hash": token_hash, "exp": expires_at},
        )


def validate_and_rotate_refresh_token(raw_token: str) -> tuple[int, str]:
    """Validate a refresh token, delete it, return (user_id, new_raw_token)."""
    token_hash = _hash_refresh_token(raw_token)
    now = datetime.now(timezone.utc)

    with get_db_session() as session:
        row = session.execute(
            text("""
                DELETE FROM refresh_tokens
                WHERE token_hash = :hash AND expires_at > :now
                RETURNING user_id
            """),
            {"hash": token_hash, "now": now},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    new_raw, new_hash = create_refresh_token(row["user_id"])
    store_refresh_token(row["user_id"], new_hash)
    return row["user_id"], new_raw


def invalidate_refresh_token(raw_token: str) -> None:
    token_hash = _hash_refresh_token(raw_token)
    with get_db_session() as session:
        session.execute(
            text("DELETE FROM refresh_tokens WHERE token_hash = :hash"),
            {"hash": token_hash},
        )


# ---------------------------------------------------------------------------
# FastAPI dependency: get_current_user
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> UserContext:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Access token has expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid access token")

    return UserContext(
        id=int(payload["sub"]),
        email=payload["email"],
        full_name=payload.get("full_name", ""),
        avatar_url=payload.get("avatar_url"),
        is_super_admin=bool(payload.get("is_super_admin", False)),
        permissions=payload.get("permissions", {}),
    )


# ---------------------------------------------------------------------------
# FastAPI dependency factory: require_permission
# ---------------------------------------------------------------------------


def require_permission(module: str, action: str):
    """
    Usage:
        @router.get("/translations")
        def list_translations(user: UserContext = Depends(require_permission("translate", "view"))):
            ...
    """

    def checker(user: UserContext = Depends(get_current_user)) -> UserContext:
        if user.is_super_admin:
            return user
        perms = user.permissions.get(module, {})
        if not perms.get(action, False):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: {module}.{action} required",
            )
        return user

    return checker


# ---------------------------------------------------------------------------
# DB bootstrap helpers
# ---------------------------------------------------------------------------


def ensure_auth_tables() -> None:
    """Create the three auth tables if they don't exist yet. Called once at startup."""
    ddl = """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            full_name VARCHAR(255) NOT NULL DEFAULT '',
            password_hash VARCHAR(255),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            oauth_provider VARCHAR(50),
            oauth_id VARCHAR(255),
            avatar_url TEXT,
            is_super_admin BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(oauth_provider, oauth_id)
        );

        CREATE TABLE IF NOT EXISTS user_permissions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            module VARCHAR(50) NOT NULL,
            can_view BOOLEAN NOT NULL DEFAULT FALSE,
            can_edit BOOLEAN NOT NULL DEFAULT FALSE,
            can_admin BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE(user_id, module)
        );

        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash VARCHAR(255) NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """
    try:
        with get_db_session() as session:
            session.execute(text(ddl))
        logger.info("Auth tables verified / created.")
    except Exception as exc:
        logger.error("Failed to ensure auth tables: %s", exc)


def assign_default_permissions(user_id: int, session) -> None:
    """New user gets view+edit on 'translate' (the core product). Admin-only
    modules start at all-False — must be granted explicitly."""
    for module in ALL_MODULES:
        is_default_on = module not in ADMIN_ONLY_MODULES
        session.execute(
            text("""
                INSERT INTO user_permissions (user_id, module, can_view, can_edit, can_admin)
                VALUES (:uid, :mod, :on, :on, FALSE)
                ON CONFLICT (user_id, module) DO NOTHING
            """),
            {"uid": user_id, "mod": module, "on": is_default_on},
        )


def ensure_bootstrap_admin() -> None:
    """Create an admin user on first startup if no users exist yet."""
    email = settings.bootstrap_admin_email.strip()
    password = settings.bootstrap_admin_password.strip()
    if not email or not password:
        return

    try:
        with get_db_session() as session:
            count = session.execute(text("SELECT COUNT(*) FROM users")).scalar()
            if count and count > 0:
                return

            hashed = hash_password(password)
            row = session.execute(
                text("""
                    INSERT INTO users (email, full_name, password_hash, is_active, is_super_admin)
                    VALUES (:email, :name, :hash, TRUE, TRUE)
                    RETURNING id
                """),
                {"email": email, "name": settings.bootstrap_admin_name, "hash": hashed},
            ).mappings().first()

            if row:
                for module in ALL_MODULES:
                    session.execute(
                        text("""
                            INSERT INTO user_permissions (user_id, module, can_view, can_edit, can_admin)
                            VALUES (:uid, :mod, TRUE, TRUE, TRUE)
                            ON CONFLICT (user_id, module) DO NOTHING
                        """),
                        {"uid": row["id"], "mod": module},
                    )
                logger.info("Bootstrap admin created: %s", email)

    except Exception as exc:
        logger.error("Failed to create bootstrap admin: %s", exc)


def build_user_context_from_db(user_id: int) -> UserContext:
    with get_db_session() as session:
        row = session.execute(
            text("""
                SELECT id, email, full_name, avatar_url, is_super_admin, is_active
                FROM users
                WHERE id = :uid
            """),
            {"uid": user_id},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    permissions = _permissions_from_db(user_id)
    return UserContext(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        avatar_url=row["avatar_url"],
        is_super_admin=bool(row["is_super_admin"]),
        permissions=permissions,
    )
