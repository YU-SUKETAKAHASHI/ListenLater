from __future__ import annotations

import logging
import os
import re

import requests
import trafilatura
from readability import Document

from .models import ArticleResult
from .utils import compact_text


logger = logging.getLogger(__name__)


JS_REQUIRED_PATTERNS = [
    "enable javascript",
    "javascript is required",
    "javascriptを有効",
    "javascript が無効",
    "please enable javascript",
]

X_STATUS_RE = re.compile(r"https?://(?:x|twitter)\.com/.+/status/(\d+)")


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
    endpoint = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=ja"
    try:
        resp = requests.get(endpoint, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json() if resp.text else {}
        text = compact_text((data.get("text") or "").strip(), 10000)
        if len(text) < 20:
            return None
        user = data.get("user") or {}
        screen_name = user.get("screen_name") or ""
        title = f"X投稿 @{screen_name}".strip()
        return ArticleResult(
            url=url,
            final_url=url,
            title=title if title != "X投稿 @" else "X投稿",
            content=text,
            method="x_syndication",
            ok=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("x syndication fallback failed for %s: %s", url, e)
        return None


def fetch_article(url: str, timeout: int = 20) -> ArticleResult:
    x_status_result = _fetch_x_status_text(url, timeout=timeout)
    if x_status_result:
        return x_status_result

    try:
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
    except Exception as e:  # noqa: BLE001
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
            return ArticleResult(
                url=url,
                final_url=final_url,
                title=title,
                content=body,
                method="readability",
                ok=True,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("readability failed for %s: %s", url, e)

    try:
        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if extracted and len(extracted.strip()) > 200:
            return ArticleResult(
                url=url,
                final_url=final_url,
                title=final_url,
                content=compact_text(extracted, 10000),
                method="trafilatura",
                ok=True,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("trafilatura failed for %s: %s", url, e)

    if _looks_like_js_wall(html):
        logger.info("JS wall detected for %s; trying jina reader fallback", final_url)
        text = _fetch_via_jina_reader(final_url, timeout=max(timeout, 25))
        if len(text.strip()) > 200:
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
        return ArticleResult(
            url=url,
            final_url=final_url,
            title=final_url,
            content=text,
            method="jina_reader_fallback",
            ok=True,
        )

    return _extract_title_description(html, final_url)
