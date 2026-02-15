from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .auth_x import router as x_auth_router
from .db import init_db
from .jobs import job_manager, launch_pipeline
from .x_client import XApiAccessError, XClient


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_PATH)
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
AUDIO_DIR = STATIC_DIR / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="DigLIKE PoC API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(x_auth_router)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/api/likes")
def get_likes(count: int = Query(default=5, ge=1, le=100)) -> dict:
    try:
        tweets = XClient().get_liked_tweets(count=count)
        return {
            "count": len(tweets),
            "requested_count": count,
            "tweets": [
                {
                    "tweet_id": t.tweet_id,
                    "text": t.text,
                    "created_at": t.created_at,
                    "urls": t.urls,
                }
                for t in tweets
            ],
        }
    except XApiAccessError as e:
        code = e.status_code or 500
        raise HTTPException(status_code=code, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/jobs/create")
async def create_job(count: int = Query(default=5, ge=1, le=100)) -> dict:
    state = job_manager.create_job()
    launch_pipeline(state.job_id, count=count, audio_dir=AUDIO_DIR)
    return {"job_id": state.job_id, "status": state.status}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    state = job_manager.as_dict(job_id)
    if not state:
        raise HTTPException(status_code=404, detail="job not found")
    return state
