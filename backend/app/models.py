from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class LikedTweet:
    tweet_id: str
    text: str
    created_at: str | None
    urls: list[str] = field(default_factory=list)


@dataclass
class ArticleResult:
    url: str
    final_url: str
    title: str
    content: str
    method: str
    ok: bool = True
    error: str | None = None


@dataclass
class JobState:
    job_id: str
    status: str = "queued"
    progress: int = 0
    message: str = "queued"
    result: dict[str, Any] | None = None
    error: str | None = None
    failure_stage: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
