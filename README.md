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
