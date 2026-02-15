from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests


logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s)\]>'\"]+")


def remove_tracking_params(url: str) -> str:
    parsed = urlparse(url)
    filtered_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
    ]
    clean_query = urlencode(filtered_query, doseq=True)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, "")
    )


def try_expand_url(url: str, timeout: int = 8) -> str:
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True)
        return resp.url or url
    except Exception as e:  # noqa: BLE001
        logger.warning("URL expand failed for %s: %s", url, e)
        return url


def extract_and_normalize_urls(text: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in URL_RE.findall(text or ""):
        normalized = remove_tracking_params(raw)
        if "t.co/" in normalized:
            normalized = try_expand_url(normalized)
            normalized = remove_tracking_params(normalized)
        if normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def compact_text(value: str, max_chars: int = 8000) -> str:
    v = re.sub(r"\s+", " ", (value or "")).strip()
    return v[:max_chars]
