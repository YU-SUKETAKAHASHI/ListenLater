from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from .utils import compact_text


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

MODE_SPECS: dict[str, str] = {
    "QuickDigest": "テンポを優先し、短めの往復で要点中心に進めてください。",
    "CuriosityTalk": "好奇心を刺激する問いを織り交ぜ、理解と発見のバランスを取ってください。",
    "DeepDive": "背景と論点を丁寧に掘り下げ、少し長めに検討過程を示してください。",
}


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def _load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if not path.exists():
        raise RuntimeError(f"prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def _extract_json_text(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= 0 and end > start:
        return text[start : end + 1]
    return text


def structure_extract_content(content: str) -> dict[str, Any]:
    model = os.getenv("OPENAI_STRUCTURE_MODEL", os.getenv("OPENAI_SCRIPT_MODEL", "gpt-4o-mini"))
    temperature = float(os.getenv("OPENAI_STRUCTURE_TEMPERATURE", "0.0"))

    prompt_template = _load_prompt("summarize.txt")
    prompt = prompt_template.replace("{{ARTICLE_OR_POST_TEXT}}", compact_text(content or "", 14000))

    resp = _client().responses.create(
        model=model,
        temperature=temperature,
        input=prompt,
    )
    raw = (resp.output_text or "").strip()
    json_text = _extract_json_text(raw)
    try:
        data = json.loads(json_text)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"failed to parse Stage1 JSON: {e}") from e

    if not isinstance(data, dict):
        raise RuntimeError("Stage1 output is not JSON object")
    return data


def generate_podcast_script_from_structured(
    structured_items: list[dict[str, Any]],
    mode: str = "CuriosityTalk",
) -> str:
    model = os.getenv("OPENAI_SCRIPT_MODEL", "gpt-4o-mini")
    temperature = float(os.getenv("OPENAI_SCRIPT_TEMPERATURE", "0.7"))

    prompt_template = _load_prompt("podcast.txt")
    mode_spec = MODE_SPECS.get(mode, MODE_SPECS["CuriosityTalk"])
    structured_json = json.dumps(structured_items, ensure_ascii=False, indent=2)

    prompt = (
        prompt_template
        .replace("{{MODE_SPEC}}", mode_spec)
        .replace("{{STRUCTURED_JSON}}", structured_json)
    )

    resp = _client().responses.create(
        model=model,
        temperature=temperature,
        input=prompt,
    )
    return (resp.output_text or "").strip()


def compose_podcast_script(materials: list[dict[str, str]], mode: str = "CuriosityTalk") -> str:
    structured_items: list[dict[str, Any]] = []

    for i, m in enumerate(materials, start=1):
        source_block = (
            f"素材{i}\n"
            f"種別: {m.get('kind', 'unknown')}\n"
            f"タイトル: {m.get('title', '')}\n"
            f"URL: {m.get('url', '')}\n"
            f"投稿者コメント:\n{compact_text(m.get('tweet_text', ''), 12000)}\n\n"
            f"抽出本文:\n{compact_text(m.get('content', ''), 12000)}"
        )
        try:
            structured = structure_extract_content(source_block)
        except Exception:  # noqa: BLE001
            structured = {
                "title": m.get("title", f"素材{i}"),
                "summary_one_sentence": compact_text(m.get("content") or m.get("tweet_text") or "", 220),
                "core_question": "このトピックで最も重要な論点は何か",
                "why_it_matters": "日々の情報理解と意思決定に関わるため",
                "key_points": [],
                "background_context": "",
                "implications": "",
                "controversies_or_limitations": "",
                "technical_terms": [],
            }
        structured_items.append(structured)

    return generate_podcast_script_from_structured(structured_items, mode=mode)
