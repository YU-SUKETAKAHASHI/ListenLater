from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlencode

import requests
from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from .db import clear_x_auth_token, get_x_auth_token, upsert_x_auth_token


X_AUTH_URL = "https://x.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
X_USERS_ME_URL = "https://api.x.com/2/users/me"


@dataclass
class PendingAuth:
    code_verifier: str
    return_to: str
    created_at: float


router = APIRouter(prefix="/api/auth/x", tags=["x-auth"])
_pending_auth: dict[str, PendingAuth] = {}


def _env(name: str, default: str | None = None) -> str:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    env_values = dotenv_values(env_path)
    value = env_values.get(name)
    if value is None or not str(value).strip():
        value = os.getenv(name, default)
    if value is None or not str(value).strip():
        raise HTTPException(status_code=500, detail=f"{name} is not set")
    return value


def _env_optional(name: str, default: str | None = None) -> str | None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    env_values = dotenv_values(env_path)
    value = env_values.get(name)
    if value is None or not str(value).strip():
        value = os.getenv(name, default)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _cleanup_pending(ttl_sec: int = 600) -> None:
    now = time.time()
    expired = [k for k, v in _pending_auth.items() if now - v.created_at > ttl_sec]
    for key in expired:
        _pending_auth.pop(key, None)


def _build_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _build_basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    encoded = base64.b64encode(raw).decode("utf-8")
    return f"Basic {encoded}"


def _resolve_user_id(access_token: str) -> str | None:
    resp = requests.get(
        X_USERS_ME_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    return data.get("id")


@router.get("/login")
def login(
    return_to: str = Query(default="http://localhost:5173"),
) -> RedirectResponse:
    frontend_url = _env_optional("FRONTEND_URL", "http://localhost:5173") or "http://localhost:5173"
    auth_url = _env_optional("X_AUTH_URL", X_AUTH_URL) or X_AUTH_URL
    _cleanup_pending()
    try:
        client_id = _env("X_CLIENT_ID")
        redirect_uri = _env("X_REDIRECT_URI", "http://localhost:8000/api/auth/x/callback")
    except HTTPException as e:
        reason = str(e.detail).replace(" ", "_")
        return RedirectResponse(url=f"{frontend_url}?x_auth=error&reason={reason}")

    scope = _env_optional("X_SCOPE", "tweet.read users.read like.read offline.access") or "tweet.read users.read like.read offline.access"

    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _build_code_challenge(code_verifier)

    _pending_auth[state] = PendingAuth(
        code_verifier=code_verifier,
        return_to=return_to,
        created_at=time.time(),
    )

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(url=f"{auth_url}?{urlencode(params)}")


@router.get("/callback")
def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    frontend_url = _env_optional("FRONTEND_URL", "http://localhost:5173") or "http://localhost:5173"
    if error:
        return RedirectResponse(url=f"{frontend_url}?x_auth=error&reason={error}")
    if not code or not state:
        return RedirectResponse(url=f"{frontend_url}?x_auth=error&reason=missing_code_or_state")

    pending = _pending_auth.pop(state, None)
    if not pending:
        return RedirectResponse(url=f"{frontend_url}?x_auth=error&reason=invalid_state")

    try:
        client_id = _env("X_CLIENT_ID")
        client_secret = _env_optional("X_CLIENT_SECRET", "") or ""
        redirect_uri = _env("X_REDIRECT_URI", "http://localhost:8000/api/auth/x/callback")

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": pending.code_verifier,
        }

        # Try multiple token exchange patterns because X app config differs by app type.
        attempts: list[tuple[dict[str, str], dict[str, str]]] = [
            ({"Content-Type": "application/x-www-form-urlencoded"}, payload),
        ]
        if client_secret:
            basic_headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": _build_basic_auth_header(client_id, client_secret),
            }
            payload_without_client_id = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": pending.code_verifier,
            }
            attempts.append((basic_headers, payload_without_client_id))
            attempts.append((basic_headers, payload))

        token_resp = None
        for headers, body in attempts:
            token_resp = requests.post(X_TOKEN_URL, headers=headers, data=body, timeout=30)
            if token_resp.status_code < 400:
                break

        assert token_resp is not None

        token_resp.raise_for_status()
        token_payload: dict[str, Any] = token_resp.json()

        access_token = token_payload.get("access_token")
        if not access_token:
            raise RuntimeError("access_token not found in token response")

        user_id = _resolve_user_id(access_token)
        upsert_x_auth_token(
            access_token=access_token,
            refresh_token=token_payload.get("refresh_token"),
            token_type=token_payload.get("token_type"),
            scope=token_payload.get("scope"),
            expires_at=token_payload.get("expires_at"),
            user_id=user_id,
        )
        return RedirectResponse(url=f"{pending.return_to}?x_auth=success")
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        body = ""
        if e.response is not None:
            body = (e.response.text or "")[:300]
        reason = quote_plus(f"HTTPError_{status}:{body}")
        return RedirectResponse(url=f"{frontend_url}?x_auth=error&reason={reason}")
    except Exception as e:  # noqa: BLE001
        reason = quote_plus(f"{type(e).__name__}:{str(e)[:200]}")
        return RedirectResponse(url=f"{frontend_url}?x_auth=error&reason={reason}")


@router.get("/status")
def status() -> dict[str, Any]:
    auth = get_x_auth_token()
    if not auth:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        env_values = dotenv_values(env_path)
        env_token = (
            env_values.get("X_BEARER_TOKEN")
            or env_values.get("X_ACCESS_TOKEN")
            or os.getenv("X_BEARER_TOKEN")
            or os.getenv("X_ACCESS_TOKEN")
        )
        if env_token:
            return {"logged_in": True, "source": "env"}
        return {"logged_in": False}
    return {
        "logged_in": True,
        "source": "db",
        "user_id": auth.get("user_id"),
        "scope": auth.get("scope"),
        "updated_at": auth.get("updated_at"),
    }


@router.post("/logout")
def logout() -> dict[str, bool]:
    clear_x_auth_token()
    return {"ok": True}
