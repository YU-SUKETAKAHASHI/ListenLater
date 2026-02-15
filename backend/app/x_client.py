from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import dotenv_values

from .db import get_x_auth_token
from .models import LikedTweet
from .utils import extract_and_normalize_urls


X_API_BASE = "https://api.x.com/2"


class XApiAccessError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class XClient:
    def __init__(self) -> None:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        env_values = dotenv_values(env_path)

        env_token = (
            env_values.get("X_BEARER_TOKEN")
            or env_values.get("X_ACCESS_TOKEN")
            or os.getenv("X_BEARER_TOKEN")
            or os.getenv("X_ACCESS_TOKEN")
        )
        env_user_id = env_values.get("X_USER_ID") or os.getenv("X_USER_ID")
        db_auth = get_x_auth_token()

        self.token = ((db_auth or {}).get("access_token") if db_auth else None) or env_token
        self.user_id = ((db_auth or {}).get("user_id") if db_auth else None) or env_user_id

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError("X_BEARER_TOKEN or X_ACCESS_TOKEN is not set")
        return {"Authorization": f"Bearer {self.token}"}

    def _resolve_user_id(self) -> str:
        if self.user_id:
            return self.user_id
        resp = requests.get(
            f"{X_API_BASE}/users/me",
            headers=self._headers(),
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        uid = data.get("id")
        if not uid:
            raise RuntimeError("Failed to resolve X user id from /users/me")
        self.user_id = uid
        return uid

    def get_liked_tweets(self, count: int = 5) -> list[LikedTweet]:
        user_id = self._resolve_user_id()
        requested_count = max(count, 1)
        api_max_results = min(max(requested_count, 5), 100)

        params = {
            "max_results": api_max_results,
            "tweet.fields": "created_at,entities",
        }
        resp = requests.get(
            f"{X_API_BASE}/users/{user_id}/liked_tweets",
            headers=self._headers(),
            params=params,
            timeout=30,
        )
        if resp.status_code == 402:
            raise XApiAccessError(
                "X liked_tweets endpoint returned 402 (Payment Required). "
                "Your current X API plan likely does not allow this endpoint.",
                status_code=402,
            )
        if resp.status_code == 400:
            body = (resp.text or "")[:300]
            raise XApiAccessError(
                "X liked_tweets endpoint returned 400 (Bad Request). "
                f"requested_count={requested_count}, api_max_results={api_max_results}, body={body}",
                status_code=400,
            )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data") or []

        out: list[LikedTweet] = []
        for row in rows:
            text = row.get("text") or ""
            urls = extract_and_normalize_urls(text)
            if not urls:
                entities = (row.get("entities") or {}).get("urls") or []
                for ent in entities:
                    expanded = ent.get("expanded_url") or ent.get("url")
                    if expanded:
                        urls.extend(extract_and_normalize_urls(expanded))
                urls = list(dict.fromkeys(urls))

            out.append(
                LikedTweet(
                    tweet_id=row.get("id", ""),
                    text=text,
                    created_at=row.get("created_at"),
                    urls=urls,
                )
            )
        return out[:requested_count]
