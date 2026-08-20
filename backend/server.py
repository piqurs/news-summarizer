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
    build_queries,
    cluster_by_date,
    compute_confidence,
    extract_article,
    looks_like_article_url,
    merge_timeline,
    normalize_url,
    search_candidates,
    summarize_article,
    synthesize_delta,
    translate_summary,
    url_hash,
    web_fetch_candidates,
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
    force_refresh: bool = False


class TranslateRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    target: str = Field(..., pattern="^(en|id)$")


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
        # Stored so /latest-updates can build precise search queries WITHOUT an
        # extra LLM entity-extraction call on every refresh.
        "key_entities": ai.get("key_entities") or [],
        "main_issue": ai.get("main_issue", {"summary": "", "significance": ""}),
        "root_cause": ai.get("root_cause", {"causes": [], "certainty_note": ""}),
        "recommended_actions": ai.get("recommended_actions", {
            "immediate": [], "short_term": [], "long_term": [],
            "disclaimer": "These are AI-generated recommendations.",
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
    print("Line 242 from console")
    limit = int(os.environ.get("RATE_LIMIT_UPDATES", "5"))
    ip = client_ip(request)
    allowed, remaining, retry_after = await store.check_rate(ip, "updates", limit)
    #if not allowed:
    #   return rate_limit_response(limit, retry_after, "updates")

    exclude = None
    entities: list = []
    baseline: dict = {}
    h = None

    if payload.source_url:
        try:
            exclude = normalize_url(payload.source_url)
            h = url_hash(exclude)

            # 1) Cek cache delta 12 jam dulu — kalau ada, skip Tavily+Claude
            # sepenuhnya. Timeline yang dikembalikan tetap di-overlay dengan
            # timeline akumulatif persisten agar tidak pernah menyusut.
            cached_delta = None if payload.force_refresh else await store.get_cached_delta(h)
            if cached_delta:
                stored_tl = await store.get_timeline(h)
                if len(stored_tl) > len(cached_delta.get("timeline") or []):
                    cached_delta["timeline"] = stored_tl
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

    t_start = datetime.now(timezone.utc)

    # Tahap 1 — bangun hingga 8 varian query dari entitas/topik baseline,
    # lalu jalankan SEMUA query secara konkuren (bukan sekuensial).
    queries = build_queries(payload.topic, entities,
                            baseline.get("publication_date"))
    try:
        candidates = await search_candidates(
            queries, exclude_url=exclude,
            baseline_date=baseline.get("publication_date"),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    stored_timeline = await store.get_timeline(h) if h else []

    # Tidak ada kandidat sama sekali — jangan buang Claude call buat sintesis
    # "tidak ada apa-apa", langsung balikin has_update: false. Timeline
    # akumulatif tetap disertakan supaya tidak pernah menyusut.
    if not candidates:
        result = {
            "has_update": False, "overview": "", "developments": [],
            "timeline": stored_timeline, "current_situation": None,
            "market_impact": None,
            "confidence": {"level": "Low", "reason":
                "Tidak ditemukan kandidat berita terkait dalam pencarian."},
            "sources_used": [],
        }
        if h:
            await store.set_cached_delta(h, result)
        return result

    # Tahap 2 — ambil teks halaman penuh untuk kandidat teratas (konkuren,
    # timeout pendek, fallback wajib ke snippet bila fetch gagal).
    candidates = await web_fetch_candidates(candidates)

    # Tahap 3 — kelompokkan kandidat per tanggal terbit sebelum sintesis.
    clusters = cluster_by_date(candidates)

    try:
        delta = await synthesize_delta(baseline, clusters)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Tahap 5 — merge timeline baru ke timeline akumulatif persisten;
    # timeline hanya bertambah, tidak pernah ditulis ulang dari nol.
    merged = merge_timeline(stored_timeline, delta.get("timeline") or [])
    delta["timeline"] = merged

    # Tahap 6 — hitung confidence deterministik SETELAH merge, sehingga skor
    # dilihat konsisten dengan timeline yang benar-benar ditampilkan.
    delta["confidence"] = compute_confidence(candidates, delta)

    # Tahap 7 — konsistensi output. Kalau timeline akumulatif berisi
    # peristiwa pasca-baseline TAPI run kali ini balik kosong dari LLM,
    # respon TIDAK boleh menampilkan badge "no update" bersamaan dengan
    # isi timeline yang ada. Paksakan has_update=true dan isi overview
    # bila kosong, serta timpa confidence.reason agar tidak menyesatkan.
    from services import _parse_date_str as _pd
    baseline_dt2 = _pd(baseline.get("publication_date") or "")

    def _post_baseline(ev):
        d = _pd(str(ev.get("date") or ""))
        return bool(d) and (baseline_dt2 is None or d > baseline_dt2)

    post_baseline_events = [ev for ev in merged if _post_baseline(ev)]
    if post_baseline_events and not delta.get("has_update"):
        delta["has_update"] = True
        if not (delta.get("overview") or "").strip():
            delta["overview"] = (
                f"Tidak ada perkembangan baru terdeteksi pada pencarian ini. "
                f"Timeline berikut merupakan akumulasi {len(post_baseline_events)} "
                f"peristiwa pasca-baseline dari pencarian sebelumnya untuk "
                f"artikel yang sama."
            )
        conf_reason_prev = (delta.get("confidence") or {}).get("reason") or ""
        delta["confidence"]["reason"] = (
            f"{conf_reason_prev} Ditampilkan {len(post_baseline_events)} "
            f"peristiwa akumulatif pasca-baseline (tidak ada perkembangan "
            f"baru dari sumber terbaru run ini)."
        ).strip()

    elapsed = (datetime.now(timezone.utc) - t_start).total_seconds()
    logger.info("[latest-updates] pipeline done in %.1fs — queries=%d "
                "candidates=%d timeline=%d (was %d) confidence=%s",
                elapsed, len(queries), len(candidates), len(merged),
                len(stored_timeline), delta["confidence"].get("level"))

    if h:
        await store.save_timeline(h, merged)
        await store.set_cached_delta(h, delta)

    return delta


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

