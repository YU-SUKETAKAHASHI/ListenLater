from __future__ import annotations

import os
from pathlib import Path

from openai import OpenAI


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def synthesize_speech(script: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    voice = os.getenv("OPENAI_TTS_VOICE", "alloy")

    response = _client().audio.speech.create(
        model=model,
        voice=voice,
        input=script,
        response_format="mp3",
    )
    response.stream_to_file(output_path)
