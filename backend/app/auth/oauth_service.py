"""
app/auth/oauth_service.py
==========================
OAuth 2.0 flow helpers for GitHub, Google, and Microsoft.

Ported verbatim from kirana_kart_final's oauth_service.py — this code
is already provider-agnostic and has no schema/table coupling, so no
adaptation was needed beyond the import path.

Each provider exposes:
    get_{provider}_oauth_url(state) → redirect URL string
    exchange_{provider}_code(code)  → OAuthUserInfo

Flow:
  1. Frontend links to GET /auth/oauth/{provider}
  2. This endpoint calls get_{provider}_oauth_url() and returns RedirectResponse
  3. Provider redirects to GET /auth/oauth/{provider}/callback?code=...&state=...
  4. Callback calls exchange_{provider}_code(code), upserts user, issues JWT
  5. Redirects to {FRONTEND_URL}/auth/callback with tokens set as HttpOnly cookies
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger("thought_translate.oauth")


@dataclass
class OAuthUserInfo:
    provider: str
    oauth_id: str
    email: str
    full_name: str
    avatar_url: str | None = None


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


def get_github_oauth_url(state: str) -> str:
    callback = f"{settings.oauth_redirect_base_url}/auth/oauth/github/callback"
    params = (
        f"client_id={settings.github_client_id}"
        f"&redirect_uri={callback}"
        f"&scope=user:email"
        f"&state={state}"
    )
    return f"{GITHUB_AUTH_URL}?{params}"


def exchange_github_code(code: str) -> OAuthUserInfo:
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(status_code=501, detail="GitHub OAuth is not configured")

    callback = f"{settings.oauth_redirect_base_url}/auth/oauth/github/callback"

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": callback,
            },
            headers={"Accept": "application/json"},
        )

    if resp.status_code != 200:
        logger.error("GitHub token exchange failed: %s", resp.text)
        raise HTTPException(status_code=400, detail="GitHub token exchange failed")

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access token from GitHub")

    auth_headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    with httpx.Client(timeout=10) as client:
        user_resp = client.get(GITHUB_USER_URL, headers=auth_headers)
        emails_resp = client.get(GITHUB_EMAILS_URL, headers=auth_headers)

    if user_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch GitHub profile")

    user_data = user_resp.json()
    email = user_data.get("email")

    if not email and emails_resp.status_code == 200:
        for e in emails_resp.json():
            if e.get("primary") and e.get("verified"):
                email = e["email"]
                break

    if not email:
        raise HTTPException(status_code=400, detail="No verified email found in GitHub account")

    return OAuthUserInfo(
        provider="github",
        oauth_id=str(user_data["id"]),
        email=email,
        full_name=user_data.get("name") or user_data.get("login") or email.split("@")[0],
        avatar_url=user_data.get("avatar_url"),
    )


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def get_google_oauth_url(state: str) -> str:
    callback = f"{settings.oauth_redirect_base_url}/auth/oauth/google/callback"
    params = (
        f"client_id={settings.google_client_id}"
        f"&redirect_uri={callback}"
        f"&response_type=code"
        f"&scope=openid%20email%20profile"
        f"&state={state}"
        f"&access_type=offline"
    )
    return f"{GOOGLE_AUTH_URL}?{params}"


def exchange_google_code(code: str) -> OAuthUserInfo:
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured")

    callback = f"{settings.oauth_redirect_base_url}/auth/oauth/google/callback"

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": callback,
                "grant_type": "authorization_code",
            },
        )

    if resp.status_code != 200:
        logger.error("Google token exchange failed: %s", resp.text)
        raise HTTPException(status_code=400, detail="Google token exchange failed")

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access token from Google")

    with httpx.Client(timeout=10) as client:
        user_resp = client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if user_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch Google profile")

    ud = user_resp.json()
    email = ud.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email in Google profile")

    return OAuthUserInfo(
        provider="google",
        oauth_id=ud["sub"],
        email=email,
        full_name=ud.get("name") or email.split("@")[0],
        avatar_url=ud.get("picture"),
    )


# ---------------------------------------------------------------------------
# Microsoft
# ---------------------------------------------------------------------------

MICROSOFT_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MICROSOFT_GRAPH_URL = "https://graph.microsoft.com/v1.0/me"


def get_microsoft_oauth_url(state: str) -> str:
    callback = f"{settings.oauth_redirect_base_url}/auth/oauth/microsoft/callback"
    params = (
        f"client_id={settings.microsoft_client_id}"
        f"&redirect_uri={callback}"
        f"&response_type=code"
        f"&scope=openid%20email%20profile%20User.Read"
        f"&state={state}"
        f"&response_mode=query"
    )
    return f"{MICROSOFT_AUTH_URL}?{params}"


def exchange_microsoft_code(code: str) -> OAuthUserInfo:
    if not settings.microsoft_client_id or not settings.microsoft_client_secret:
        raise HTTPException(status_code=501, detail="Microsoft OAuth is not configured")

    callback = f"{settings.oauth_redirect_base_url}/auth/oauth/microsoft/callback"

    with httpx.Client(timeout=10) as client:
        resp = client.post(
            MICROSOFT_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.microsoft_client_id,
                "client_secret": settings.microsoft_client_secret,
                "redirect_uri": callback,
                "grant_type": "authorization_code",
                "scope": "openid email profile User.Read",
            },
        )

    if resp.status_code != 200:
        logger.error("Microsoft token exchange failed: %s", resp.text)
        raise HTTPException(status_code=400, detail="Microsoft token exchange failed")

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access token from Microsoft")

    with httpx.Client(timeout=10) as client:
        user_resp = client.get(
            MICROSOFT_GRAPH_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if user_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch Microsoft profile")

    ud = user_resp.json()
    email = ud.get("mail") or ud.get("userPrincipalName")
    if not email:
        raise HTTPException(status_code=400, detail="No email in Microsoft profile")

    full_name = ud.get("displayName") or email.split("@")[0]

    return OAuthUserInfo(
        provider="microsoft",
        oauth_id=ud["id"],
        email=email,
        full_name=full_name,
        avatar_url=None,
    )
