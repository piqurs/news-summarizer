"""News Summarizer FastAPI backend."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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
    synthesize_delta,
    translate_summary,
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


class TranslateRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    target: str = Field(..., pattern="^(en|id)$")


class ShareRequest(BaseModel):
    summary: dict = Field(...)


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
        "impact_analysis": ai.get("impact_analysis", {
            "items": [],
            "disclaimer": "AI-generated impact analysis based on the article's "
                          "content and general domain knowledge — not "
                          "financial, investment, or professional advice.",
        }),
        "sentiment_and_bias": ai.get("sentiment_and_bias", {
            "tone": "Neutral",
            "tone_explanation": "",
            "bias_indicators": [],
            "disclaimer": "AI-generated analysis, not a factual claim about the publisher.",
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


def rate_limit_response(limit: int, retry_after: int, bucket: str) -> JSONResponse:
    minutes = max(1, round(retry_after / 60))
    labels = {"summarize": "summaries", "updates": "updates", "translate": "translations"}
    label = labels.get(bucket, bucket)
    return JSONResponse(
        status_code=429,
        content={
            "detail": (
                f"You've used all {limit} {label} for this hour. "
                f"Please try again in about {minutes} minute"
                f"{'s' if minutes != 1 else ''}."
            ),
            "rate_limit": {
                "limit": limit,
                "remaining": 0,
                "retry_after_seconds": retry_after,
                "bucket": bucket,
            },
        },
        headers={"Retry-After": str(retry_after)},
    )


@api_router.get("/rate-status")
async def rate_status(request: Request):
    ip = client_ip(request)
    s_limit = int(os.environ.get("RATE_LIMIT_SUMMARY", "5"))
    u_limit = int(os.environ.get("RATE_LIMIT_UPDATES", "5"))
    s_remaining, s_retry = await store.peek_rate(ip, "summarize", s_limit)
    u_remaining, u_retry = await store.peek_rate(ip, "updates", u_limit)
    return {
        "summarize": {
            "limit": s_limit, "remaining": s_remaining,
            "retry_after_seconds": s_retry,
        },
        "updates": {
            "limit": u_limit, "remaining": u_remaining,
            "retry_after_seconds": u_retry,
        },
    }


@api_router.get("/recent")
async def recent(limit: int = 6):
    """Global feed of the most recent still-fresh cached summaries.
    Purely reads the existing 12 h cache — never triggers new AI calls."""
    limit = max(1, min(limit, 12))
    items = await store.list_recent(limit=limit)
    return {"items": items, "limit": limit}


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
    limit = int(os.environ.get("RATE_LIMIT_SUMMARY", "5"))
    ip = client_ip(request)

    # Cache lookup FIRST (no rate-limit charge for cached results)
    cached = await store.get_cached(h, "id")
    if cached:
        logger.info("cache_hit hash=%s", h[:10])
        peek_remaining, _ = await store.peek_rate(ip, "summarize", limit)
        return {
            "cached": True,
            "summary": cached,
            "language": "id",
            "rate_limit": {"limit": limit, "remaining": peek_remaining},
        }

    allowed, remaining, retry_after = await store.check_rate(ip, "summarize", limit)
    if not allowed:
        return rate_limit_response(limit, retry_after, "summarize")

    # Extract
    try:
        article = extract_article(normalized)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logger.exception("article extraction failed")
        raise HTTPException(status_code=502,
                            detail="Failed to fetch the article. Check the URL and try again.")

    # Summarize (output is Bahasa Indonesia by default)
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
    return {
        "cached": False,
        "summary": result,
        "language": "id",
        "rate_limit": {"limit": limit, "remaining": remaining},
    }


@api_router.post("/latest-updates")
async def latest_updates(payload: LatestUpdatesRequest, request: Request):
    limit = int(os.environ.get("RATE_LIMIT_UPDATES", "5"))
    ip = client_ip(request)
    allowed, remaining, retry_after = await store.check_rate(ip, "updates", limit)
    if not allowed:
        return rate_limit_response(limit, retry_after, "updates")

    exclude = None
    entities: list = []
    baseline: dict = {}
    h = None

    if payload.source_url:
        try:
            exclude = normalize_url(payload.source_url)
            h = url_hash(exclude)

            # 1) Cek cache delta 12 jam dulu — kalau ada, skip Tavily+Claude sepenuhnya
            cached_delta = await store.get_cached_delta(h)
            if cached_delta:
                return cached_delta

            # 2) Ambil baseline dari cache summary utama (title, entities, dll)
            cached_summary = await store.get_cached(h, "id")
            if cached_summary:
                article_meta = cached_summary.get("article", {})
                entities = cached_summary.get("key_entities") or []
                baseline = {
                    "source_url": exclude,
                    "title": article_meta.get("title") or cached_summary.get("title"),
                    "publication_date": (
                        article_meta.get("publication_date")
                        or cached_summary.get("publication_date")
                    ),
                    "key_entities": entities,
                    "key_points": cached_summary.get("key_points") or [],
                    "main_issue": cached_summary.get("main_issue") or {},
                }
        except ValueError:
            exclude = None

    existing_timeline = await store.get_accumulated_timeline(h) if h else []

    try:
        raw_candidates = await fetch_latest_updates(
            payload.topic, exclude_url=exclude, entities=entities,
            baseline_date=(baseline or {}).get("publication_date"),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Tidak ada kandidat sama sekali — jangan buang Claude call buat sintesis
    # "tidak ada apa-apa". Riwayat timeline yang sudah terakumulasi dari
    # refresh sebelumnya tetap dipertahankan, bukan ikut dikosongkan.
    if not raw_candidates:
        result = {
            "has_update": bool(existing_timeline), "overview": "", "developments": [],
            "timeline": existing_timeline, "current_situation": None, "market_impact": None,
            "confidence": {"level": "Low", "reason":
                "Tidak ditemukan kandidat berita terkait dalam pencarian."},
            "sources_used": [],
        }
        if h:
            await store.set_cached_delta(h, result)
        return result

    try:
        delta = await synthesize_delta(baseline, raw_candidates, existing_timeline)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if h:
        await store.set_cached_delta(h, delta)
        await store.set_accumulated_timeline(h, delta.get("timeline") or [])

    return delta


@api_router.post("/share")
async def create_share(payload: ShareRequest, request: Request):
    """Store a snapshot of an already-generated summary and return a short
    slug for it. This is deliberately decoupled from the 12h summary cache —
    a link someone shares should keep working even after that expires."""
    slug = await store.create_share(payload.summary)
    return {"slug": slug}


@api_router.get("/shared/{slug}")
async def get_shared(slug: str):
    doc = await store.get_share(slug)
    if not doc:
        raise HTTPException(status_code=404,
                             detail="This shared summary was not found.")
    return {"summary": doc["summary"], "created_at": doc.get("created_at")}


@api_router.post("/translate")
async def translate(payload: TranslateRequest, request: Request):
    limit = int(os.environ.get("RATE_LIMIT_TRANSLATE", "10"))
    ip = client_ip(request)
    target = payload.target

    try:
        normalized = normalize_url(payload.url.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid URL format.")

    h = url_hash(normalized)
    doc = await store.get_cache_doc(h)
    if not doc:
        raise HTTPException(
            status_code=404,
            detail="No cached summary for this article. Please summarize it first.",
        )

    # ID = the primary payload (already stored on first summarize)
    if target == "id":
        peek_remaining, _ = await store.peek_rate(ip, "translate", limit)
        return {
            "cached": True,
            "summary": doc.get("payload"),
            "target": "id",
            "rate_limit": {"limit": limit, "remaining": peek_remaining},
        }

    # target == "en": cache hit → free
    translations = doc.get("translations") or {}
    if translations.get(target):
        peek_remaining, _ = await store.peek_rate(ip, "translate", limit)
        return {
            "cached": True,
            "summary": translations[target],
            "target": target,
            "rate_limit": {"limit": limit, "remaining": peek_remaining},
        }

    # cache miss → charge rate limit, translate, save
    allowed, remaining, retry_after = await store.check_rate(ip, "translate", limit)
    if not allowed:
        return rate_limit_response(limit, retry_after, "translate")

    try:
        translated = await translate_summary(doc["payload"], target)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("translate failed")
        raise HTTPException(status_code=503,
                            detail=f"Translation service temporarily unavailable: {e}")

    await store.set_translation(h, target, translated)
    logger.info("translate_store hash=%s lang=%s", h[:10], target)
    return {
        "cached": False,
        "summary": translated,
        "target": target,
        "rate_limit": {"limit": limit, "remaining": remaining},
    }


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
