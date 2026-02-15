from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).resolve().parent / "data.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                script TEXT NOT NULL,
                audio_path TEXT,
                source_urls TEXT NOT NULL,
                status TEXT NOT NULL,
                skipped TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS x_auth (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                token_type TEXT,
                scope TEXT,
                expires_at INTEGER,
                user_id TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )


def create_episode(
    script: str,
    source_urls: list[str],
    status: str,
    skipped: list[dict[str, Any]],
) -> int:
    created_at = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO episodes (script, audio_path, source_urls, status, skipped, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                script,
                None,
                json.dumps(source_urls, ensure_ascii=False),
                status,
                json.dumps(skipped, ensure_ascii=False),
                created_at,
            ),
        )
        return int(cur.lastrowid)


def finalize_episode(episode_id: int, audio_path: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE episodes SET audio_path = ?, status = ? WHERE id = ?",
            (audio_path, status, episode_id),
        )


def get_episode(episode_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "script": row["script"],
            "audio_path": row["audio_path"] or "",
            "source_urls": json.loads(row["source_urls"]),
            "status": row["status"],
            "skipped": json.loads(row["skipped"] or "[]"),
            "created_at": row["created_at"],
        }


def upsert_x_auth_token(
    access_token: str,
    refresh_token: str | None,
    token_type: str | None,
    scope: str | None,
    expires_at: int | None,
    user_id: str | None,
) -> None:
    updated_at = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO x_auth (id, access_token, refresh_token, token_type, scope, expires_at, user_id, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                token_type=excluded.token_type,
                scope=excluded.scope,
                expires_at=excluded.expires_at,
                user_id=excluded.user_id,
                updated_at=excluded.updated_at
            """,
            (access_token, refresh_token, token_type, scope, expires_at, user_id, updated_at),
        )


def get_x_auth_token() -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM x_auth WHERE id = 1").fetchone()
        if not row:
            return None
        return {
            "access_token": row["access_token"],
            "refresh_token": row["refresh_token"],
            "token_type": row["token_type"],
            "scope": row["scope"],
            "expires_at": row["expires_at"],
            "user_id": row["user_id"],
            "updated_at": row["updated_at"],
        }


def clear_x_auth_token() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM x_auth WHERE id = 1")
