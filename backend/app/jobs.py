from __future__ import annotations

import asyncio
import logging
import os
import re
import traceback
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .article_fetch import fetch_article
from .db import create_episode, finalize_episode, get_episode
from .models import JobState
from .summarize import compose_podcast_script
from .tts import synthesize_speech
from .x_client import XApiAccessError, XClient


logger = logging.getLogger(__name__)


def _fallback_urls_from_env() -> list[str]:
    raw = os.getenv("FALLBACK_SOURCE_URLS", "")
    if not raw.strip():
        return []
    parts = [p.strip() for p in re.split(r"[\n,]+", raw) if p.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _fallback_script(materials: list[dict[str, str]]) -> str:
    lines = [
        "おはようございます。通勤向けニュースダイジェストです。",
        "本日は、いいねした投稿と記事から情報をじっくりお届けします。",
        "",
    ]
    for i, m in enumerate(materials, start=1):
        lines.append(f"【素材{i}】{m.get('title', '無題')}")
        content = (m.get("content") or "").strip()
        if not content:
            content = (m.get("tweet_text") or "").strip()
        lines.append(content[:1200] if content else "本文を十分に取得できませんでした。")
        lines.append(f"出典: {m.get('url', '')}")
        lines.append("")
    lines.append("以上、通勤向けニュースダイジェストでした。")
    return "\n".join(lines)


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, JobState] = {}

    def create_job(self) -> JobState:
        job_id = str(uuid.uuid4())
        state = JobState(job_id=job_id)
        self.jobs[job_id] = state
        return state

    def get(self, job_id: str) -> JobState | None:
        return self.jobs.get(job_id)

    def as_dict(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if not job:
            return None
        out = asdict(job)
        out["created_at"] = job.created_at.isoformat()
        return out

    def update(self, job_id: str, **kwargs: Any) -> None:
        job = self.get(job_id)
        if not job:
            return
        for k, v in kwargs.items():
            setattr(job, k, v)

    def add_event(self, job_id: str, stage: str, message: str, level: str = "info", detail: Any | None = None) -> None:
        job = self.get(job_id)
        if not job:
            return
        event: dict[str, Any] = {"stage": stage, "level": level, "message": message}
        if detail is not None:
            event["detail"] = detail
        job.events.append(event)

    def set_stage(self, job_id: str, stage: str) -> None:
        self.update(job_id, failure_stage=stage)

    async def run_pipeline(self, job_id: str, count: int, audio_dir: Path) -> None:
        try:
            self.set_stage(job_id, "fetch_likes")
            self.add_event(job_id, "fetch_likes", "start")
            self.update(job_id, status="running", progress=5, message="Fetching liked tweets")
            x = XClient()
            liked = x.get_liked_tweets(count=count)
            self.add_event(job_id, "fetch_likes", "done", detail={"liked_count": len(liked)})

            self.set_stage(job_id, "extract_sources")
            self.update(job_id, progress=15, message="Extracting source materials")

            work_items: list[dict[str, Any]] = []
            unique_urls: list[str] = []
            seen_urls: set[str] = set()
            tweet_only_candidates = 0

            for t in liked:
                tweet_text = (t.text or "").strip()
                tweet_url = f"https://x.com/i/web/status/{t.tweet_id}" if t.tweet_id else ""
                if t.urls:
                    for u in t.urls:
                        if u in seen_urls:
                            continue
                        seen_urls.add(u)
                        unique_urls.append(u)
                        work_items.append(
                            {
                                "type": "url",
                                "url": u,
                                "tweet_text": tweet_text,
                                "tweet_url": tweet_url,
                                "tweet_id": t.tweet_id,
                            }
                        )
                elif tweet_text:
                    tweet_only_candidates += 1
                    work_items.append(
                        {
                            "type": "tweet",
                            "url": tweet_url or f"x://tweet/{t.tweet_id}",
                            "tweet_text": tweet_text,
                            "tweet_url": tweet_url,
                            "tweet_id": t.tweet_id,
                        }
                    )

            self.add_event(
                job_id,
                "extract_sources",
                "done",
                detail={
                    "url_count": len(unique_urls),
                    "tweet_only_candidates": tweet_only_candidates,
                    "work_item_count": len(work_items),
                },
            )

            if not work_items:
                fallback_urls = _fallback_urls_from_env()
                if fallback_urls:
                    work_items = [{"type": "url", "url": u, "tweet_text": "", "tweet_url": "", "tweet_id": ""} for u in fallback_urls]
                    unique_urls = fallback_urls
                    self.update(job_id, progress=20, message="No processable likes found. Using FALLBACK_SOURCE_URLS")
                else:
                    raise RuntimeError("No processable liked tweets found")

            materials: list[dict[str, str]] = []
            skipped: list[dict[str, str]] = []
            total = len(work_items)
            for idx, item in enumerate(work_items, start=1):
                progress = 15 + int((idx / total) * 45)
                item_type = item.get("type")

                if item_type == "url":
                    url = str(item.get("url") or "")
                    tweet_text = str(item.get("tweet_text") or "")

                    self.set_stage(job_id, "fetch_article")
                    self.update(job_id, progress=progress, message=f"Fetching source {idx}/{total}")
                    article = fetch_article(url)

                    if article.ok and (article.content or "").strip():
                        materials.append(
                            {
                                "kind": "article",
                                "title": article.title,
                                "url": article.final_url,
                                "tweet_text": tweet_text,
                                "content": article.content,
                                "method": article.method,
                            }
                        )
                        continue

                    if tweet_text:
                        materials.append(
                            {
                                "kind": "tweet_comment_fallback",
                                "title": "X投稿コメント",
                                "url": url,
                                "tweet_text": tweet_text,
                                "content": tweet_text,
                                "method": "tweet_fallback",
                            }
                        )
                        self.add_event(
                            job_id,
                            "fetch_article",
                            "article_failed_tweet_fallback_used",
                            level="warning",
                            detail={"url": url, "reason": article.error or "empty_content"},
                        )
                        continue

                    skipped.append({"url": url, "reason": article.error or "content_too_short"})
                    continue

                if item_type == "tweet":
                    tweet_text = str(item.get("tweet_text") or "").strip()
                    tweet_url = str(item.get("tweet_url") or item.get("url") or "")
                    tweet_id = str(item.get("tweet_id") or "")
                    if not tweet_text:
                        skipped.append({"url": tweet_url or f"x://tweet/{tweet_id}", "reason": "tweet_text_empty"})
                        continue
                    materials.append(
                        {
                            "kind": "tweet",
                            "title": f"X投稿 {tweet_id}" if tweet_id else "X投稿",
                            "url": tweet_url or f"x://tweet/{tweet_id}",
                            "tweet_text": tweet_text,
                            "content": tweet_text,
                            "method": "tweet_text",
                        }
                    )

            if not materials:
                raise RuntimeError("No rich source materials could be extracted")

            self.set_stage(job_id, "compose_script")
            self.update(job_id, progress=70, message="Composing podcast script")
            try:
                script = compose_podcast_script(materials)
            except Exception as e:  # noqa: BLE001
                logger.exception("compose_podcast_script failed, fallback used: %s", e)
                self.add_event(job_id, "compose_script", "compose_failed_fallback_used", level="warning", detail={"reason": str(e)})
                script = _fallback_script(materials)

            self.set_stage(job_id, "save_episode")
            self.update(job_id, progress=80, message="Saving episode draft")
            source_urls = list(dict.fromkeys([m.get("url", "") for m in materials if m.get("url")]))
            episode_id = create_episode(script=script, source_urls=source_urls, status="processing", skipped=skipped)

            self.set_stage(job_id, "tts")
            self.update(job_id, progress=90, message="Generating TTS audio")
            audio_path = audio_dir / f"{episode_id}.mp3"
            synthesize_speech(script, audio_path)

            rel_audio_path = f"/static/audio/{episode_id}.mp3"
            finalize_episode(episode_id, rel_audio_path, "done")
            episode = get_episode(episode_id)

            self.update(
                job_id,
                status="done",
                progress=100,
                message="Completed",
                result={
                    "episode": episode,
                    "materials": materials,
                    "skipped": skipped,
                    "liked_count": len(liked),
                    "url_count": len(unique_urls),
                },
                failure_stage=None,
            )
            self.add_event(job_id, "completed", "job_done", detail={"episode_id": episode_id})
        except Exception as e:  # noqa: BLE001
            if isinstance(e, XApiAccessError) and getattr(e, "status_code", None) == 402:
                fallback_urls = _fallback_urls_from_env()
                if fallback_urls:
                    self.update(job_id, status="running", progress=20, message="X liked_tweets unavailable. Using FALLBACK_SOURCE_URLS")
                    work_materials: list[dict[str, str]] = []
                    skipped: list[dict[str, str]] = []
                    for u in fallback_urls:
                        article = fetch_article(u)
                        if article.ok and (article.content or "").strip():
                            work_materials.append(
                                {
                                    "kind": "article",
                                    "title": article.title,
                                    "url": article.final_url,
                                    "tweet_text": "",
                                    "content": article.content,
                                    "method": article.method,
                                }
                            )
                        else:
                            skipped.append({"url": u, "reason": article.error or "content_too_short"})

                    if work_materials:
                        script = compose_podcast_script(work_materials)
                        episode_id = create_episode(
                            script=script,
                            source_urls=list(dict.fromkeys([m.get("url", "") for m in work_materials if m.get("url")])),
                            status="processing",
                            skipped=skipped,
                        )
                        audio_path = audio_dir / f"{episode_id}.mp3"
                        synthesize_speech(script, audio_path)
                        rel_audio_path = f"/static/audio/{episode_id}.mp3"
                        finalize_episode(episode_id, rel_audio_path, "done")
                        episode = get_episode(episode_id)
                        self.update(
                            job_id,
                            status="done",
                            progress=100,
                            message="Completed with fallback URLs",
                            result={
                                "episode": episode,
                                "materials": work_materials,
                                "skipped": skipped,
                                "liked_count": 0,
                                "url_count": len(fallback_urls),
                                "note": "X liked_tweets returned 402. Used FALLBACK_SOURCE_URLS.",
                            },
                            failure_stage=None,
                        )
                        return

            logger.error("Job %s failed: %s", job_id, e)
            logger.debug(traceback.format_exc())
            current_stage = self.get(job_id).failure_stage if self.get(job_id) else None
            self.add_event(job_id, current_stage or "unknown", "job_failed", level="error", detail={"error": str(e)})
            self.update(job_id, status="error", progress=100, message="Failed", error=str(e))


job_manager = JobManager()


def launch_pipeline(job_id: str, count: int, audio_dir: Path) -> None:
    asyncio.create_task(job_manager.run_pipeline(job_id=job_id, count=count, audio_dir=audio_dir))
