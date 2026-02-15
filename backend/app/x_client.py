from __future__ import annotations

import os
import re
from pathlib import Path

import requests
from dotenv import dotenv_values

from .db import get_x_auth_token
from .models import LikedTweet
from .utils import extract_and_normalize_urls


X_API_BASE = "https://api.x.com/2"
X_STATUS_URL_RE = re.compile(r"https?://(?:x|twitter)\.com/.+/status/(\d+)")


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

    def _raise_api_error(self, resp: requests.Response, context: str) -> None:
        if resp.status_code == 400:
            body = (resp.text or "")[:300]
            raise XApiAccessError(f"{context}: bad request (400) body={body}", status_code=400)
        if resp.status_code == 401:
            raise XApiAccessError(f"{context}: unauthorized (401). Check token type/scope.", status_code=401)
        if resp.status_code == 403:
            raise XApiAccessError(
                f"{context}: forbidden (403). This endpoint requires OAuth user context and proper scopes.",
                status_code=403,
            )
        if resp.status_code == 404:
            raise XApiAccessError(f"{context}: not found (404). Tweet may be deleted or private.", status_code=404)
        if resp.status_code == 402:
            raise XApiAccessError(
                f"{context}: payment required (402). Your X API plan may not include this endpoint.",
                status_code=402,
            )
        if resp.status_code >= 400:
            body = (resp.text or "")[:500]
            raise XApiAccessError(f"{context}: http {resp.status_code} body={body}", status_code=resp.status_code)


    def get_liked_tweets(self, count: int = 1) -> list[LikedTweet]:
        user_id = self._resolve_user_id()
        requested_count = max(count, 1)
        api_max_results = min(max(requested_count, 5), 100)

        params = {
            "max_results": api_max_results,
            "tweet.fields": "article,created_at,entities,note_tweet",
        }
        resp = requests.get(
            f"{X_API_BASE}/users/{user_id}/liked_tweets",
            headers=self._headers(),
            params=params,
            timeout=30,
        )
        self._raise_api_error(
            resp,
            f"get liked_tweets requested_count={requested_count} api_max_results={api_max_results}",
        )
        payload = resp.json()
        rows = payload.get("data") or []
        out: list[LikedTweet] = []
        print(f"Fetched {len(rows)} liked tweets for user_id={user_id}")
        print(rows)
        for row in rows:
            article = row.get("article") or {}
            article_text = (article.get("plain_text") or article.get("text") or "").strip()
            text = (article_text or (row.get("note_tweet") or {}).get("text") or row.get("text") or "")
            urls = extract_and_normalize_urls(text)

            # Some liked_tweets rows expose long article data in `article`.
            # Add a stable i/article URL when present so downstream can treat it as article source.
            article_id = str(article.get("id") or article.get("article_id") or "").strip()
            article_url = str(article.get("url") or "").strip()
            if article_id:
                urls.append(f"https://x.com/i/article/{article_id}")
            if article_url:
                urls.extend(extract_and_normalize_urls(article_url))

            if not urls:
                entities = (row.get("entities") or {}).get("urls") or []
                for ent in entities:
                    expanded = ent.get("expanded_url") or ent.get("url")
                    if expanded:
                        urls.extend(extract_and_normalize_urls(expanded))

            urls = list(dict.fromkeys(urls))
            print(f"Extracted URLs from liked tweet {row.get('id')}: {urls}")

            out.append(
                LikedTweet(
                    tweet_id=row.get("id", ""),
                    text=text,
                    created_at=row.get("created_at"),
                    urls=urls,
                )
            )
        return out[:requested_count]
