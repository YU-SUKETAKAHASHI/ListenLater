from __future__ import annotations

import os

from openai import OpenAI

from .utils import compact_text


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def compose_podcast_script(materials: list[dict[str, str]]) -> str:
    model = os.getenv("OPENAI_SCRIPT_MODEL", "gpt-4o-mini")
    blocks = []
    for i, m in enumerate(materials, start=1):
        blocks.append(
            (
                f"素材{i}\n"
                f"種別: {m.get('kind', 'unknown')}\n"
                f"タイトル: {m['title']}\n"
                f"URL: {m['url']}\n"
                f"投稿者コメント:\n{compact_text(m.get('tweet_text', ''), 3000)}\n\n"
                f"抽出本文:\n{compact_text(m.get('content', ''), 12000)}"
            )
        )
    material = "\n\n".join(blocks)
    prompt = f"""
あなたは通勤向けポッドキャストの台本作家です。
以下の複数素材（記事本文・投稿コメント）から、自然な語り口の日本語台本を作成してください。

要件:
- 構成: 導入 -> 記事1..N -> まとめ
- 8〜12分で読める分量を目標
- 聞きやすい接続詞、話し言葉
- 入力素材に含まれない新事実を追加しない
- 情報量が薄い素材は無理に膨らませず短く扱う
- 「出典URL」等の、発話した場合に不自然な表現は避ける（例: 「記事のURLは〜」→「詳しくは概要欄を見てね」等）

素材:
{material}
""".strip()

    resp = _client().responses.create(
        model=model,
        temperature=0.7,
        input=prompt,
    )
    return (resp.output_text or "").strip()
