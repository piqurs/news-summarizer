"""Business logic for News Summarizer: URL normalization, article extraction,
Claude summarization, Tavily live search, MongoDB caching and rate limiting."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import trafilatura
from tavily import TavilyClient
from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)

# --- URL normalization ----------------------------------------------------

_TRACKING_PARAM_PREFIXES = ("utm_", "ga_", "fbclid", "gclid", "mc_", "mkt_",
                            "yclid", "msclkid", "_hsenc", "_hsmi", "hsctatracking",
                            "vero_", "trk", "ref_")
_TRACKING_PARAMS = {"fbclid", "gclid", "yclid", "msclkid", "igshid", "ref",
                    "share", "share_id", "shared", "spm", "s_kwcid", "cmpid"}


def normalize_url(url: str) -> str:
    """Strip tracking params, trailing slashes, lowercase host. Deterministic."""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid URL")

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    cleaned_query = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not any(k.lower().startswith(p) for p in _TRACKING_PARAM_PREFIXES)
        and k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(cleaned_query))

    path = parsed.path.rstrip("/") or "/"

    return urlunparse(("https", host, path, "", query, ""))


def url_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_ARTICLE_URL_RX = re.compile(r"^https?://[^\s]+", re.I)


def looks_like_article_url(url: str) -> bool:
    """Cheap heuristic: reject obviously non-article URLs before any AI call."""
    if not _ARTICLE_URL_RX.match(url):
        return False
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not host or "." not in host:
        return False
    blocked_hosts = ("localhost", "127.0.0.1", "example.com", "example.org")
    if any(h in host for h in blocked_hosts):
        return False
    # Reject typical non-article endpoints
    path = parsed.path.lower()
    bad_paths = ("/login", "/signup", "/pricing", "/checkout", "/cart",
                 "/api/", "/wp-admin", "/wp-login", "/search")
    if any(path.startswith(p) or p in path for p in bad_paths):
        return False
    # Extensions that are never articles
    if any(path.endswith(ext) for ext in
           (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mp3", ".zip")):
        return False
    return True


# --- Article extraction ---------------------------------------------------

def extract_article(url: str) -> dict[str, Any]:
    """Download and parse article. Returns text + metadata, or raises RuntimeError."""
    downloaded = trafilatura.fetch_url(url, no_ssl=False)

    if not downloaded:
        # Fallback with a realistic browser User-Agent — many news sites block
        # trafilatura's default UA.
        try:
            import requests
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": ("text/html,application/xhtml+xml,application/xml;"
                           "q=0.9,image/avif,image/webp,*/*;q=0.8"),
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = requests.get(url, headers=headers, timeout=20,
                                allow_redirects=True)
            if resp.status_code == 200 and resp.text:
                downloaded = resp.text
        except Exception as e:
            logger.info("fallback fetch failed: %s", e)

    if not downloaded:
        raise RuntimeError("Could not download the article. The site may be "
                           "unreachable or blocking automated access.")

    text = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if not text or len(text.strip()) < 300:
        raise RuntimeError("Could not parse enough article content from this "
                           "URL. It may be a paywalled, JavaScript-rendered, "
                           "or unsupported page.")

    meta = trafilatura.extract_metadata(downloaded)
    meta_dict: dict[str, Any] = {}
    if meta:
        m = meta.as_dict()
        meta_dict = {
            "title": m.get("title"),
            "author": m.get("author"),
            "date": m.get("date"),
            "sitename": m.get("sitename") or m.get("hostname"),
            "categories": m.get("categories") or [],
            "description": m.get("description"),
        }

    return {"text": text.strip(), "meta": meta_dict}


# --- Claude summarization -------------------------------------------------

_SYSTEM_PROMPT = """You are an expert news analyst producing structured JSON summaries.

You will receive a news article and MUST return a single JSON object matching this exact schema — no prose, no markdown fences, no commentary. All keys are required.

{
  "title": "string — article title",
  "category": "string — one short category label (e.g., 'Politics', 'Technology', 'Business', 'Health', 'Sports', 'World', 'Science', 'Entertainment')",
  "publication_date": "string or null — ISO date if the article states one, else null",
  "reading_time_minutes": "integer — realistic reading time",
  "executive_summary": "string — 100 to 150 words. What happened, why it matters, overall context.",
  "key_points": ["exactly 5 concise bullet strings"],
  "main_issue": {
    "summary": "string — the primary issue discussed",
    "significance": "string — why it's significant"
  },
  "root_cause": {
    "causes": ["array of 1-4 strings, one per distinct cause"],
    "certainty_note": "string — if information is insufficient to determine a root cause, explicitly say so here; otherwise a short caveat about assumptions"
  },
  "recommended_actions": {
    "immediate": ["1-3 practical steps"],
    "short_term": ["1-3 practical steps"],
    "long_term": ["1-3 practical steps"],
    "disclaimer": "These are AI-generated recommendations based only on the article's content."
  },
  "five_w_one_h": {
    "who": "string",
    "what": "string",
    "when": "string",
    "where": "string",
    "why": "string",
    "how": "string"
  },
  "references": [
    {"website": "string", "title": "string", "date": "string or null", "url": "string"}
  ],
  "confidence_level": {
    "level": "High | Medium | Low",
    "reason": "string — brief explanation"
  }
}

Rules:
- Never invent facts. If missing, say so in the relevant section.
- References must include the source article itself plus any other sources it explicitly cites.
- Keep every string plain text — no markdown, no HTML.
- Return ONLY the JSON object, nothing else."""


async def summarize_article(text: str, source_url: str,
                            source_meta: dict[str, Any]) -> dict[str, Any]:
    """Call Claude Sonnet 4.5 and return parsed JSON dict."""
    key = os.environ["EMERGENT_LLM_KEY"]

    session_id = f"summary-{hashlib.sha256(source_url.encode()).hexdigest()[:12]}"
    chat = LlmChat(
        api_key=key,
        session_id=session_id,
        system_message=_SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929").with_params(
        max_tokens=4096,
    )

    prompt = (
        f"SOURCE URL: {source_url}\n"
        f"SITE: {source_meta.get('sitename') or 'unknown'}\n"
        f"PUBLICATION_DATE_HINT: {source_meta.get('date') or 'unknown'}\n"
        f"TITLE_HINT: {source_meta.get('title') or 'unknown'}\n\n"
        f"ARTICLE TEXT:\n{text[:18000]}"
    )

    response = await chat.send_message(UserMessage(text=prompt))
    raw = response if isinstance(response, str) else str(response)

    # Strip potential ```json fences
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Claude returned non-JSON: %s", raw[:500])
        raise RuntimeError(f"AI response could not be parsed: {e}") from e

    # Ensure required top-level references include the source URL
    refs = data.get("references") or []
    if not any(source_url in (r.get("url") or "") for r in refs):
        refs.insert(0, {
            "website": source_meta.get("sitename") or urlparse(source_url).netloc,
            "title": data.get("title") or source_meta.get("title") or "Source article",
            "date": source_meta.get("date"),
            "url": source_url,
        })
        data["references"] = refs

    return data


# --- Tavily live search ---------------------------------------------------

_TRUSTED_DOMAINS = [
    "reuters.com", "bloomberg.com", "apnews.com", "bbc.com", "bbc.co.uk",
    "cnbc.com", "cnn.com", "detik.com", "kompas.com", "tempo.co",
    "bisnis.com", "antaranews.com", "x.com", "twitter.com",
    "threads.net", "instagram.com", "tiktok.com",
]


def fetch_latest_updates(topic: str, exclude_url: Optional[str] = None
                         ) -> list[dict[str, Any]]:
    """Query Tavily for the latest news on the topic. Returns list, newest first."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key or api_key.startswith("REPLACE"):
        raise RuntimeError("Live search is not configured. Set TAVILY_API_KEY.")

    client = TavilyClient(api_key=api_key)
    try:
        resp = client.search(
            query=topic,
            topic="news",
            time_range="month",
            max_results=10,
            include_domains=_TRUSTED_DOMAINS,
        )
    except Exception as e:  # tavily raises various exceptions
        logger.exception("Tavily error")
        raise RuntimeError(f"Live search failed: {e}") from e

    results = resp.get("results", []) or []
    updates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for r in results:
        url = (r.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        if exclude_url and normalize_url(url) == exclude_url:
            continue
        seen_urls.add(url)

        pub = r.get("published_date") or r.get("publication_date")
        updates.append({
            "date": pub,
            "source": urlparse(url).netloc.replace("www.", ""),
            "title": r.get("title") or "",
            "summary": (r.get("content") or "")[:400],
            "url": url,
        })

    # Sort newest first — items with parseable dates first, then items
    # with any string date (lexically), then items without a date.
    from email.utils import parsedate_to_datetime
    from datetime import datetime as _dt

    def _parsed(dstr: str) -> Optional[_dt]:
        if not dstr:
            return None
        try:
            return parsedate_to_datetime(dstr)
        except Exception:
            pass
        try:
            return _dt.fromisoformat(dstr.replace("Z", "+00:00"))
        except Exception:
            return None

    def _sort_key(u: dict[str, Any]) -> tuple[int, float]:
        parsed = _parsed(u.get("date") or "")
        if parsed is None:
            return (1, 0.0)
        return (0, -parsed.timestamp())  # newest first via negative ts

    updates.sort(key=_sort_key)
    return updates


# --- MongoDB cache + rate limit -------------------------------------------

class CacheAndRateLimit:
    """Small helper that owns both the cached-summary store and the per-IP
    rate limit counters. Both live in MongoDB."""

    def __init__(self, db):
        self.db = db
        self.cache = db.summary_cache
        self.rate = db.rate_limit
        self.ttl_hours = int(os.environ.get("CACHE_TTL_HOURS", "24"))

    async def ensure_indexes(self) -> None:
        await self.cache.create_index("hash", unique=True)
        await self.cache.create_index("created_at")
        await self.rate.create_index([("ip", 1), ("bucket", 1), ("ts", 1)])

    async def get_cached(self, h: str) -> Optional[dict[str, Any]]:
        doc = await self.cache.find_one({"hash": h}, {"_id": 0})
        if not doc:
            return None
        created = doc.get("created_at")
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        if not created:
            return None
        if datetime.now(timezone.utc) - created > timedelta(hours=self.ttl_hours):
            return None
        return doc.get("payload")

    async def set_cached(self, h: str, url: str, payload: dict[str, Any]) -> None:
        await self.cache.update_one(
            {"hash": h},
            {"$set": {
                "hash": h,
                "url": url,
                "payload": payload,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

    async def check_rate(self, ip: str, bucket: str, limit: int) -> tuple[bool, int]:
        """Sliding-hour window per IP. Returns (allowed, remaining)."""
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=1)
        # Purge old
        await self.rate.delete_many({"ts": {"$lt": window_start.isoformat()}})
        # Bounded scan — stop counting once we've seen `limit` docs.
        docs = await self.rate.find({
            "ip": ip, "bucket": bucket,
            "ts": {"$gte": window_start.isoformat()},
        }).limit(limit).to_list(limit)
        count = len(docs)
        if count >= limit:
            return False, 0
        await self.rate.insert_one({
            "ip": ip, "bucket": bucket, "ts": now.isoformat(),
        })
        return True, limit - count - 1
