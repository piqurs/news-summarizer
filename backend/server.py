"""News Summarizer FastAPI backend."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from services import (  # noqa: E402
    CacheAndRateLimit,
    extract_article,
    fetch_latest_updates,
    looks_like_article_url,
    normalize_url,
    summarize_article,
    url_hash,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("news_summarizer")

# ---- Mongo -----------------------------------------------------------------
mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]
store = CacheAndRateLimit(db)

# ---- App -------------------------------------------------------------------
app = FastAPI(title="News Summarizer API", version="1.0.0")
api_router = APIRouter(prefix="/api")


# ---- Models ----------------------------------------------------------------
class SummarizeRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)


class LatestUpdatesRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=400)
    source_url: Optional[str] = None


# ---- Helpers ---------------------------------------------------------------
def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def build_summary_payload(url: str, ai: dict[str, Any],
                          meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "article": {
            "title": ai.get("title") or meta.get("title") or "Untitled",
            "category": ai.get("category") or "General",
            "original_url": url,
            "publication_date": ai.get("publication_date") or meta.get("date"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reading_time_minutes": ai.get("reading_time_minutes") or 3,
            "site_name": meta.get("sitename"),
        },
        "executive_summary": ai.get("executive_summary", ""),
        "key_points": ai.get("key_points", []),
        "main_issue": ai.get("main_issue", {"summary": "", "significance": ""}),
        "root_cause": ai.get("root_cause", {"causes": [], "certainty_note": ""}),
        "recommended_actions": ai.get("recommended_actions", {
            "immediate": [], "short_term": [], "long_term": [],
            "disclaimer": "These are AI-generated recommendations.",
        }),
        "five_w_one_h": ai.get("five_w_one_h", {}),
        "references": ai.get("references", []),
        "confidence_level": ai.get("confidence_level",
                                   {"level": "Medium", "reason": ""}),
    }


# ---- Routes ----------------------------------------------------------------
@api_router.get("/")
async def root():
    return {"service": "News Summarizer API", "status": "ok"}


@api_router.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@api_router.post("/summarize")
async def summarize(payload: SummarizeRequest, request: Request):
    raw_url = payload.url.strip()

    if not looks_like_article_url(raw_url):
        raise HTTPException(status_code=400,
                            detail="This does not look like a valid news article URL.")

    try:
        normalized = normalize_url(raw_url)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid URL format.")

    h = url_hash(normalized)

    # Cache lookup FIRST (no rate-limit charge for cached results)
    cached = await store.get_cached(h)
    if cached:
        logger.info("cache_hit hash=%s", h[:10])
        return {"cached": True, "summary": cached}

    # Rate limit
    limit = int(os.environ.get("RATE_LIMIT_SUMMARY", "5"))
    ip = client_ip(request)
    allowed, remaining = await store.check_rate(ip, "summarize", limit)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit reached ({limit} per hour). Please try again later.",
        )

    # Extract
    try:
        article = extract_article(normalized)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logger.exception("article extraction failed")
        raise HTTPException(status_code=502,
                            detail="Failed to fetch the article. Check the URL and try again.")

    # Summarize
    try:
        ai = await summarize_article(article["text"], normalized, article["meta"])
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("summarization failed")
        raise HTTPException(status_code=503,
                            detail=f"The AI service is temporarily unavailable: {e}")

    result = build_summary_payload(normalized, ai, article["meta"])
    await store.set_cached(h, normalized, result)
    logger.info("cache_store hash=%s remaining=%s", h[:10], remaining)
    return {"cached": False, "summary": result}


@api_router.post("/latest-updates")
async def latest_updates(payload: LatestUpdatesRequest, request: Request):
    limit = int(os.environ.get("RATE_LIMIT_UPDATES", "5"))
    ip = client_ip(request)
    allowed, _ = await store.check_rate(ip, "updates", limit)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit reached ({limit} per hour). Please try again later.",
        )

    exclude = None
    if payload.source_url:
        try:
            exclude = normalize_url(payload.source_url)
        except ValueError:
            exclude = None

    try:
        updates = fetch_latest_updates(payload.topic, exclude_url=exclude)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("latest-updates failed")
        raise HTTPException(status_code=502,
                            detail="Could not fetch latest updates. Please try again.")

    return {"updates": updates, "fetched_at": datetime.now(timezone.utc).isoformat()}


# ---- App wiring ------------------------------------------------------------
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await store.ensure_indexes()
    logger.info("News Summarizer API ready")


@app.on_event("shutdown")
async def shutdown():
    client.close()
