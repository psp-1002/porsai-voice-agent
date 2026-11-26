"""Web server: serves the call page and hands out LiveKit tokens.

  GET  /                       -> redirects to the demo business
  GET  /call/{slug}            -> the call page for that business
  POST /api/token {slug}       -> creates a private room "call--<slug>--<id>" and a token

The agent worker is dispatched into every new room automatically by LiveKit.

Run locally:   uvicorn web.server:app --reload --port 8000
"""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from livekit import api
from pydantic import BaseModel

load_dotenv(".env")
STATIC = Path(__file__).parent / "static"
DEFAULT_SLUG = os.getenv("DEFAULT_BUSINESS", "sunrise-salon")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1,
                                               max_size=3, statement_cache_size=0)
    yield
    await app.state.pool.close()


app = FastAPI(lifespan=lifespan)


async def _business_name(slug: str) -> str:
    name = await app.state.pool.fetchval("SELECT name FROM businesses WHERE slug = $1", slug)
    if name is None:
        raise HTTPException(404, "Unknown business")
    return name


@app.get("/")
async def root():
    return RedirectResponse(f"/call/{DEFAULT_SLUG}")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/call/{slug}", response_class=HTMLResponse)
async def call_page(slug: str):
    name = await _business_name(slug)
    html = (STATIC / "call.html").read_text()
    return html.replace("{{BUSINESS_NAME}}", name).replace("{{SLUG}}", slug)


class TokenRequest(BaseModel):
    slug: str


@app.post("/api/token")
async def token(req: TokenRequest):
    await _business_name(req.slug)                      # 404 for unknown businesses
    room = f"call--{req.slug}--{uuid.uuid4().hex[:10]}"  # a fresh private room per call
    identity = f"caller-{uuid.uuid4().hex[:8]}"
    jwt = (api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
           .with_identity(identity)
           .with_name("Caller")
           .with_grants(api.VideoGrants(room_join=True, room=room,
                                        can_publish=True, can_subscribe=True))
           .to_jwt())
    return {"url": os.environ["LIVEKIT_URL"], "token": jwt, "room": room}
