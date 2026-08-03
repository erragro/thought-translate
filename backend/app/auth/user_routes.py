"""
app/auth/user_routes.py
=========================
User management endpoints. All require admin.admin permission (or
is_super_admin). Ported from kirana_kart_final's user_management.py,
schema-prefix stripped.

Endpoints:
    GET    /users                  — list all users with their permissions
    GET    /users/{id}             — get a single user + permissions
    PATCH  /users/{id}/permissions — update per-module permissions
    PATCH  /users/{id}/activate    — activate an account
    PATCH  /users/{id}/deactivate  — deactivate an account
    DELETE /users/{id}             — hard-delete a user
    PUT    /users/{id}/password    — admin-initiated password reset
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import text

from app.auth.service import ALL_MODULES, UserContext, hash_password, require_permission
from app.db import get_db_session

router = APIRouter(prefix="/users", tags=["user-management"])

_require_admin = require_permission("admin", "admin")


class PermissionUpdate(BaseModel):
    module: str
    can_view: bool = False
    can_edit: bool = False
    can_admin: bool = False


class BulkPermissionUpdate(BaseModel):
    permissions: list[PermissionUpdate]


class PasswordResetRequest(BaseModel):
    new_password: str


def _get_user_with_permissions(user_id: int, session) -> dict | None:
    row = session.execute(
        text("""
            SELECT id, email, full_name, avatar_url, is_active, is_super_admin,
                   oauth_provider, created_at
            FROM users
            WHERE id = :uid
        """),
        {"uid": user_id},
    ).mappings().first()

    if not row:
        return None

    perms_rows = session.execute(
        text("SELECT module, can_view, can_edit, can_admin FROM user_permissions WHERE user_id = :uid"),
        {"uid": user_id},
    ).mappings().all()

    perms = {
        r["module"]: {"view": bool(r["can_view"]), "edit": bool(r["can_edit"]), "admin": bool(r["can_admin"])}
        for r in perms_rows
    }

    return {
        "id": row["id"],
        "email": row["email"],
        "full_name": row["full_name"],
        "avatar_url": row["avatar_url"],
        "is_active": row["is_active"],
        "is_super_admin": row["is_super_admin"],
        "oauth_provider": row["oauth_provider"],
        "created_at": row["created_at"],
        "permissions": perms,
    }


@router.get("")
def list_users(_user: UserContext = Depends(_require_admin)):
    with get_db_session() as session:
        rows = session.execute(
            text("""
                SELECT id, email, full_name, avatar_url, is_active,
                       is_super_admin, oauth_provider, created_at
                FROM users
                ORDER BY created_at DESC
            """)
        ).mappings().all()

        result = []
        for r in rows:
            perms_rows = session.execute(
                text("SELECT module, can_view, can_edit, can_admin FROM user_permissions WHERE user_id = :uid"),
                {"uid": r["id"]},
            ).mappings().all()
            perms = {
                p["module"]: {"view": bool(p["can_view"]), "edit": bool(p["can_edit"]), "admin": bool(p["can_admin"])}
                for p in perms_rows
            }
            result.append({
                "id": r["id"],
                "email": r["email"],
                "full_name": r["full_name"],
                "avatar_url": r["avatar_url"],
                "is_active": r["is_active"],
                "is_super_admin": r["is_super_admin"],
                "oauth_provider": r["oauth_provider"],
                "created_at": r["created_at"],
                "permissions": perms,
            })

    return jsonable_encoder(result)


@router.get("/{user_id}")
def get_user(user_id: int, _user: UserContext = Depends(_require_admin)):
    with get_db_session() as session:
        data = _get_user_with_permissions(user_id, session)
    if not data:
        raise HTTPException(status_code=404, detail="User not found")
    return jsonable_encoder(data)


@router.patch("/{user_id}/permissions")
def update_permissions(user_id: int, payload: BulkPermissionUpdate, _user: UserContext = Depends(_require_admin)):
    with get_db_session() as session:
        exists = session.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id}).scalar()
        if not exists:
            raise HTTPException(status_code=404, detail="User not found")

        for perm in payload.permissions:
            if perm.module not in ALL_MODULES:
                raise HTTPException(status_code=400, detail=f"Unknown module: {perm.module}")
            session.execute(
                text("""
                    INSERT INTO user_permissions (user_id, module, can_view, can_edit, can_admin)
                    VALUES (:uid, :mod, :view, :edit, :admin)
                    ON CONFLICT (user_id, module)
                    DO UPDATE SET can_view = EXCLUDED.can_view, can_edit = EXCLUDED.can_edit, can_admin = EXCLUDED.can_admin
                """),
                {"uid": user_id, "mod": perm.module, "view": perm.can_view, "edit": perm.can_edit, "admin": perm.can_admin},
            )

    return {"status": "ok", "user_id": user_id}


@router.patch("/{user_id}/activate")
def activate_user(user_id: int, _user: UserContext = Depends(_require_admin)):
    with get_db_session() as session:
        row = session.execute(
            text("UPDATE users SET is_active = TRUE, updated_at = NOW() WHERE id = :uid RETURNING id"),
            {"uid": user_id},
        ).scalar()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "activated", "user_id": user_id}


@router.patch("/{user_id}/deactivate")
def deactivate_user(user_id: int, _user: UserContext = Depends(_require_admin)):
    with get_db_session() as session:
        row = session.execute(
            text("UPDATE users SET is_active = FALSE, updated_at = NOW() WHERE id = :uid RETURNING id"),
            {"uid": user_id},
        ).scalar()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "deactivated", "user_id": user_id}


@router.delete("/{user_id}")
def delete_user(user_id: int, current_user: UserContext = Depends(_require_admin)):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    with get_db_session() as session:
        row = session.execute(text("DELETE FROM users WHERE id = :uid RETURNING id"), {"uid": user_id}).scalar()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "deleted", "user_id": user_id}


@router.put("/{user_id}/password")
def reset_user_password(user_id: int, body: PasswordResetRequest, current_user: UserContext = Depends(_require_admin)):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    hashed = hash_password(body.new_password)
    with get_db_session() as session:
        row = session.execute(
            text("UPDATE users SET password_hash = :h WHERE id = :uid RETURNING id"),
            {"h": hashed, "uid": user_id},
        ).scalar()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "password_reset", "user_id": user_id}
