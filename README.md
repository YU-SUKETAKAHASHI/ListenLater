# DigLIKE PoC (Local Only)

Xでいいねした投稿のURLを集めて、記事本文・投稿コメントを抽出し、要約せずに通勤向け台本化→TTS音声化→ブラウザ再生までをローカルで動かすPoCです。

## 構成

```
backend/  FastAPI + SQLite + OpenAI + article fetch
frontend/ React(Vite)+TypeScript の最小UI
```

## 事前準備

- Python 3.11
- Node.js / npm
- X APIトークン（Bearer または Access token）
- OpenAI APIキー
- tweepy（`pip install -r backend/requirements.txt` で導入）

## セキュリティ注意

- `backend/.env` に秘密情報を置いてください。
- `.env` は `.gitignore` 済みで、**コミットしない**でください。

## 1) Backend 起動

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
```

`backend/.env` に以下を設定:

```env
X_BEARER_TOKEN=...         # または X_ACCESS_TOKEN
X_ACCESS_TOKEN=
X_USER_ID=                 # 未設定なら /users/me から取得を試みる

OPENAI_API_KEY=...
OPENAI_SUMMARY_MODEL=gpt-4o-mini
OPENAI_SCRIPT_MODEL=gpt-4o-mini
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=alloy

# OAuth login (推奨)
X_CLIENT_ID=...
X_REDIRECT_URI=http://localhost:8000/api/auth/x/callback
X_SCOPE=tweet.read users.read like.read offline.access
X_AUTH_URL=https://x.com/i/oauth2/authorize
FRONTEND_URL=http://localhost:5173
```

### X OAuthログインを使う場合（Postman不要）

X Developer Portal 側で以下を設定して保存してください。

- Callback URI: `http://localhost:8000/api/auth/x/callback`
- Website URL: `http://localhost:5173`
- Scope: `tweet.read users.read like.read`（必要なら `offline.access`）

アプリからのログインフロー:

1. Frontend の「Xでログイン」を押す
2. X 認可画面で許可
3. callback 後に frontend に戻る
4. `/api/auth/x/status` が `logged_in: true` になれば成功

実装済みエンドポイント:

- `GET /api/auth/x/login?return_to=http://localhost:5173`
- `GET /api/auth/x/callback`
- `GET /api/auth/x/status`
- `POST /api/auth/x/logout`

起動:

```bash
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

確認:

- Swagger: `http://localhost:8000/docs`
- API:
  - `GET /api/likes?count=5`
  - `POST /api/jobs/create?count=5`
  - `GET /api/jobs/{job_id}`

## 2) Frontend 起動

```bash
npm install --prefix frontend
npm run dev --prefix frontend
```

アクセス:

- `http://localhost:5173`

## 3) 使い方（MVP）

1. 取得件数（count）を入力（デフォルト: 5）
2. **Generate** を押す
3. progress が `queued/running/done/error` で更新
4. `done` 後に
   - 音声プレイヤーで mp3 再生
   - 抽出素材（記事本文 / 投稿コメント）表示
   - 台本テキスト表示
   - 元URL一覧表示
   - スキップ理由表示

## 失敗時の挙動（PoC方針）

- 本文取得できないURLはスキップ
- スキップ理由はジョブ結果 `skipped` とログへ出力
- 1件でも記事要約が作れれば、全体ジョブは継続して完了を目指します

## 保存先

- SQLite: `backend/app/data.db`
- 音声mp3: `backend/app/static/audio/{episode_id}.mp3`
- 音声配信URL: `/static/audio/{episode_id}.mp3`

## 補足

- `.env` の `X_ACCESS_TOKEN` / `X_BEARER_TOKEN` があればそれも利用可能です。
- OAuthログインで取得したトークンは SQLite (`x_auth`) に保存され、`x_client.py` から参照されます。

## 長文投稿（Long post）がHTML fetchで取れない理由と正攻法

### 1) なぜスクレイピングで失敗するか

- Xの投稿ページはクライアント側JavaScriptで本文を組み立てて描画することが多く、静的HTMLだけでは本文が十分に含まれません。
- そのため `requests` でURLを叩いてDOMを取る方式は、長文投稿で「JS必須」「非対応ブラウザ」表示に当たりやすく、本文抽出が不安定になります。

### 2) X API v2で長文投稿の全文を取る正しい方法

- エンドポイント: `GET /2/tweets/:id`
- 重要な `tweet.fields`: `text,note_tweet`（加えて `created_at,entities,lang` など）
- 理由: 長文投稿の全文は `note_tweet.text` に入るため、`text` だけだと欠ける可能性があります。

例（クエリ文字列）:

```text
GET https://api.x.com/2/tweets/2021927266146570397?tweet.fields=text,note_tweet,created_at,entities,lang
```

### 3) Python短例（URL→投稿ID→API→全文表示）

```python
import os
import re
import requests

TOKEN = os.environ["X_ACCESS_TOKEN"]  # または X_BEARER_TOKEN
URL_RE = re.compile(r"https?://(?:x|twitter)\.com/.+/status/(\d+)")

def extract_tweet_id(url: str) -> str:
    m = URL_RE.search(url)
    if not m:
        raise ValueError("tweet id をURLから抽出できません")
    return m.group(1)

def fetch_full_text(tweet_url: str) -> str:
    tweet_id = extract_tweet_id(tweet_url)
    endpoint = f"https://api.x.com/2/tweets/{tweet_id}"
    params = {"tweet.fields": "text,note_tweet,created_at,entities,lang"}
    headers = {"Authorization": f"Bearer {TOKEN}"}

    r = requests.get(endpoint, params=params, headers=headers, timeout=30)
    if r.status_code == 401:
        raise RuntimeError("401 Unauthorized: トークン不正/期限切れ")
    if r.status_code == 403:
        raise RuntimeError("403 Forbidden: OAuth user context / scope不足")
    if r.status_code == 404:
        raise RuntimeError("404 Not Found: 投稿削除・非公開の可能性")
    r.raise_for_status()

    data = (r.json() or {}).get("data") or {}
    note_text = ((data.get("note_tweet") or {}).get("text") or "").strip()
    text = (data.get("text") or "").strip()
    full_text = note_text or text
    if not full_text:
        raise RuntimeError("本文が空です")

    # fallback: note_tweetが返らず末尾が省略記号の場合
    if not note_text and full_text.endswith("…"):
        full_text += "\n\n[warning] note_tweet が返らず、本文が省略されている可能性があります"
    return full_text
```

### 4) 本プロジェクト内の実装箇所

- `backend/app/x_client.py`
  - `get_tweet_by_id()`
  - `get_tweet_full_text()`
  - `get_tweet_full_text_from_url()`
  - `get_liked_tweets()` でも `tweet.fields` に `note_tweet` を含め、`note_tweet.text` 優先で本文を採用

- エラーハンドリング
  - `401`: 認証トークン問題
  - `403`: 権限/スコープ不足（ユーザーコンテキスト必須）
  - `404`: 投稿不存在（削除/非公開）
  - `note_tweet` 不在時の省略疑い（末尾 `…`）に警告fallback

- `backend/app/article_fetch.py`
  - X投稿URL検出時はまず `tweepy.Client(...).get_tweet(..., tweet_fields=["note_tweet", "text", ...])` を実行
  - `note_tweet.text` 優先で本文を採用
  - tweepyで失敗した場合のみ `cdn.syndication` フォールバック
