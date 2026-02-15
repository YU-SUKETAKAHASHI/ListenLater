from __future__ import annotations

import logging
import os
import re

import requests
import trafilatura
from readability import Document
from dotenv import dotenv_values

try:
    import tweepy
except Exception:  # noqa: BLE001
    tweepy = None

from .models import ArticleResult
from .utils import compact_text


logger = logging.getLogger(__name__)


JS_REQUIRED_PATTERNS = [
    "enable javascript",
    "javascript is required",
    "javascriptを有効",
    "javascript が無効",
    "please enable javascript",
    "JavaScriptを使用できません",
]

X_STATUS_RE = re.compile(r"https?://(?:x|twitter)\.com/.+/status/(\d+)")


def _load_x_tokens() -> tuple[str | None, str | None]:
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    env = dotenv_values(env_path)
    bearer = (
        env.get("X_BEARER_TOKEN")
        or env.get("X_ACCESS_TOKEN")
        or os.getenv("X_BEARER_TOKEN")
        or os.getenv("X_ACCESS_TOKEN")
    )
    return (bearer.strip() if bearer else None, (env.get("X_BEARER_TOKEN") or "").strip() if env.get("X_BEARER_TOKEN") else None)


def _short_url(url: str, max_len: int = 140) -> str:
    if len(url) <= max_len:
        return url
    return f"{url[:max_len]}..."


def _strip_html_tags(html: str) -> str:
    text = re.sub(r"<script[\\s\\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\\s\\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_title_description(html: str, final_url: str) -> ArticleResult:
    title_match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = compact_text(title_match.group(1) if title_match else final_url, 200)

    desc_match = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not desc_match:
        desc_match = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
    description = compact_text(desc_match.group(1) if desc_match else "本文抽出に失敗したため概要のみ。", 800)

    return ArticleResult(
        url=final_url,
        final_url=final_url,
        title=title,
        content=description,
        method="metadata_fallback",
        ok=True,
    )


def _looks_like_js_wall(html: str) -> bool:
    lowered = (html or "").lower()
    return any(p in lowered for p in JS_REQUIRED_PATTERNS)


def _fetch_via_jina_reader(url: str, timeout: int = 25) -> str:
    """Use r.jina.ai reader fallback for JS-heavy pages.
    Returns plain text/markdown when available.
    """
    if os.getenv("JINA_READER_FALLBACK", "1").strip().lower() in {"0", "false", "off"}:
        return ""

    target = f"https://r.jina.ai/http://{url}" if not url.startswith("http://") else f"https://r.jina.ai/{url}"
    try:
        resp = requests.get(
            target,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        return compact_text(resp.text or "", 10000)
    except Exception as e:  # noqa: BLE001
        logger.warning("jina reader fallback failed for %s: %s", url, e)
        return ""


def _fetch_x_status_text(url: str, timeout: int = 20) -> ArticleResult | None:
    m = X_STATUS_RE.search(url)
    if not m:
        return None
    tweet_id = m.group(1)
    logger.info("X status detected tweet_id=%s url=%s", tweet_id, _short_url(url))

    # 1) Preferred path: X API v2 via tweepy with note_tweet field (long-post full text)
    if tweepy is not None:
        try:
            token, _ = _load_x_tokens()
            if token:
                logger.info("trying tweepy v2 for tweet_id=%s", tweet_id)
                client = tweepy.Client(bearer_token=token, wait_on_rate_limit=True)
                resp = client.get_tweet(
                    id=tweet_id,
                    tweet_fields=["created_at", "entities", "lang", "note_tweet", "text"],
                )
                data = getattr(resp, "data", None)
                if data:
                    payload = data.data if hasattr(data, "data") else data
                    note_tweet = (payload.get("note_tweet") or {}) if isinstance(payload, dict) else {}
                    note_text = compact_text((note_tweet.get("text") or "").strip(), 12000)
                    plain_text = compact_text((payload.get("text") or "").strip(), 12000) if isinstance(payload, dict) else ""
                    full_text = note_text or plain_text
                    if full_text:
                        logger.info(
                            "tweepy success tweet_id=%s method=x_tweepy_v2_note_tweet length=%d note_tweet=%s",
                            tweet_id,
                            len(full_text),
                            bool(note_text),
                        )
                        return ArticleResult(
                            url=url,
                            final_url=url,
                            title="X投稿",
                            content=full_text,
                            method="x_tweepy_v2_note_tweet",
                            ok=True,
                        )
                logger.warning("tweepy returned empty data for tweet_id=%s", tweet_id)
            else:
                logger.info("tweepy skipped: X token not set")
        except Exception as e:  # noqa: BLE001
            logger.warning("tweepy failed tweet_id=%s url=%s error=%s", tweet_id, _short_url(url), e)
    else:
        logger.info("tweepy module unavailable; using fallback for tweet_id=%s", tweet_id)

    # 2) Fallback path: syndication endpoint
    endpoint = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=ja"
    try:
        logger.info("trying syndication fallback for tweet_id=%s", tweet_id)
        resp = requests.get(endpoint, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json() if resp.text else {}
        text = compact_text((data.get("text") or "").strip(), 10000)
        if len(text) < 20:
            logger.warning("syndication text too short tweet_id=%s", tweet_id)
            return None
        user = data.get("user") or {}
        screen_name = user.get("screen_name") or ""
        title = f"X投稿 @{screen_name}".strip()
        logger.info("syndication success tweet_id=%s length=%d", tweet_id, len(text))
        return ArticleResult(
            url=url,
            final_url=url,
            title=title if title != "X投稿 @" else "X投稿",
            content=text,
            method="x_syndication",
            ok=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("syndication failed tweet_id=%s url=%s error=%s", tweet_id, _short_url(url), e)
        return None


def fetch_article(url: str, timeout: int = 20) -> ArticleResult:
    logger.info("start url=%s", _short_url(url))
    x_status_result = _fetch_x_status_text(url, timeout=timeout)
    if x_status_result:
        logger.info("done via x_status method=%s", x_status_result.method)
        return x_status_result

    try:
        logger.info("trying regular HTTP fetch url=%s", _short_url(url))
        resp = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            },
        )
        resp.raise_for_status()
        final_url = resp.url or url
        html = resp.text
        logger.info("fetched html final_url=%s size=%d", _short_url(final_url), len(html or ""))
    except Exception as e:  # noqa: BLE001
        logger.warning("request failed url=%s error=%s", _short_url(url), e)
        return ArticleResult(
            url=url,
            final_url=url,
            title=url,
            content="",
            method="request_failed",
            ok=False,
            error=f"request_failed: {e}",
        )

    try:
        doc = Document(html)
        title = compact_text(doc.short_title() or final_url, 200)
        main_html = doc.summary(html_partial=True)
        body = compact_text(_strip_html_tags(main_html), 10000)
        if len(body) > 200:
            logger.info("readability success final_url=%s length=%d", _short_url(final_url), len(body))
            return ArticleResult(
                url=url,
                final_url=final_url,
                title=title,
                content=body,
                method="readability",
                ok=True,
            )
        logger.info("readability too short final_url=%s length=%d", _short_url(final_url), len(body))
    except Exception as e:  # noqa: BLE001
        logger.warning("readability failed url=%s error=%s", _short_url(url), e)

    try:
        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if extracted and len(extracted.strip()) > 200:
            logger.info("trafilatura success final_url=%s length=%d", _short_url(final_url), len(extracted.strip()))
            return ArticleResult(
                url=url,
                final_url=final_url,
                title=final_url,
                content=compact_text(extracted, 10000),
                method="trafilatura",
                ok=True,
            )
        logger.info("trafilatura too short final_url=%s", _short_url(final_url))
    except Exception as e:  # noqa: BLE001
        logger.warning("trafilatura failed url=%s error=%s", _short_url(url), e)

    if _looks_like_js_wall(html):
        logger.info("JS wall detected final_url=%s; trying jina reader fallback", _short_url(final_url))
        text = _fetch_via_jina_reader(final_url, timeout=max(timeout, 25))
        if len(text.strip()) > 200:
            logger.info("jina reader JS fallback success final_url=%s length=%d", _short_url(final_url), len(text.strip()))
            return ArticleResult(
                url=url,
                final_url=final_url,
                title=final_url,
                content=text,
                method="jina_reader_js_fallback",
                ok=True,
            )

    # Try jina reader fallback even when not explicit JS wall, for script-heavy pages.
    text = _fetch_via_jina_reader(final_url, timeout=max(timeout, 25))
    if len(text.strip()) > 300:
        logger.info("jina reader fallback success final_url=%s length=%d", _short_url(final_url), len(text.strip()))
        return ArticleResult(
            url=url,
            final_url=final_url,
            title=final_url,
            content=text,
            method="jina_reader_fallback",
            ok=True,
        )

    logger.info("metadata fallback final_url=%s", _short_url(final_url))
    return _extract_title_description(html, final_url)
