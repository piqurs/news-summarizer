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

_SYSTEM_PROMPT = """You are an expert news analyst producing structured JSON summaries in Bahasa Indonesia.

You will receive a news article (in ANY language) and MUST return a single JSON object matching this exact schema — no prose, no markdown fences, no commentary. All keys are required.

{
  "title": "string — article title",
  "category": "string — one short category label (e.g., 'Politics', 'Technology', 'Business', 'Health', 'Sports', 'World', 'Science', 'Entertainment')",
  "key_entities": ["array of 2-6 specific named entities central to this article — company names, institution names, stock tickers, person names, or the specific event/decision. These are used to build a precise search query for related news, so use exact proper nouns, not generic terms (e.g. 'Bank Indonesia', 'BBRI', 'Perry Warjiyo' — NOT 'central bank' or 'interest rates')."],
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
  "sentiment_and_bias": {
    "tone": "Positive | Neutral | Negative | Mixed — pick exactly one of these four English literals",
    "tone_explanation": "one sentence explaining what drives that tone (specific language, framing, or facts in the article)",
    "bias_indicators": ["array of 0 to 4 short strings, each grounded in a specific observation from THIS article — e.g. one-sided sourcing, loaded language, missing context that would materially change interpretation. If the article reads as balanced and fact-based with no notable indicators, return an EMPTY array — do NOT manufacture a bias claim to fill the section."],
    "disclaimer": "AI-generated analysis, not a factual claim about the publisher."
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
- Language: Every human-readable string value MUST be written in Bahasa Indonesia (Indonesian), regardless of the source article's language. This includes title, category, executive_summary, key_points, main_issue.*, root_cause.*, recommended_actions.* items and disclaimer, five_w_one_h.*, references[].title/website, sentiment_and_bias.tone_explanation, sentiment_and_bias.bias_indicators[], sentiment_and_bias.disclaimer, confidence_level.reason.
- Keep the following as-is in their original form: URLs, publication_date, dates in references, the confidence_level.level value which MUST remain exactly one of the literal English strings "High", "Medium", or "Low", AND the sentiment_and_bias.tone value which MUST remain exactly one of "Positive", "Neutral", "Negative", or "Mixed".
- Keep proper nouns (people, organisations, places) natural — translate only when a standard Indonesian equivalent exists.
- Never invent facts. If missing, say so in the relevant section (in Indonesian).
- REFERENCES RULE (STRICT): Every references[] entry MUST include a real, retrievable http(s):// URL that is either the source article itself or explicitly cited in the article body with a resolvable link. NEVER emit a reference entry with a missing, empty, "#", "unknown", or otherwise non-navigable url field. If a source is mentioned in the article but no retrievable URL exists for it, OMIT that reference entirely — fold the attribution into body text if needed, but do NOT create a reference card. When in doubt, omit.
- Sentiment & Bias is a subjective analytical read. Only cite bias_indicators that are grounded in specific observable features of THIS article (loaded language, one-sided sourcing, missing counter-context). Do NOT speculate about the publisher's general reputation, political leaning, or editorial history. If the article is balanced and fact-based, return an EMPTY bias_indicators array.
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

    # Ensure required top-level references include the source URL AND drop any
    # entry without a real http(s):// URL — the UI cannot render bare titles as
    # links and this is a repeated bug source.
    refs = data.get("references") or []
    refs = [
        r for r in refs
        if isinstance(r, dict)
        and isinstance(r.get("url"), str)
        and r["url"].strip().lower().startswith(("http://", "https://"))
    ]
    if not any(source_url in (r.get("url") or "") for r in refs):
        refs.insert(0, {
            "website": source_meta.get("sitename") or urlparse(source_url).netloc,
            "title": data.get("title") or source_meta.get("title") or "Source article",
            "date": source_meta.get("date"),
            "url": source_url,
        })
    data["references"] = refs

    # Defensive default for sentiment_and_bias if the model omits or malforms it.
    sab = data.get("sentiment_and_bias") or {}
    tone = sab.get("tone")
    if tone not in ("Positive", "Neutral", "Negative", "Mixed"):
        tone = "Neutral"
    data["sentiment_and_bias"] = {
        "tone": tone,
        "tone_explanation": sab.get("tone_explanation") or "",
        "bias_indicators": [
            s for s in (sab.get("bias_indicators") or []) if isinstance(s, str) and s.strip()
        ],
        "disclaimer": sab.get("disclaimer")
        or "AI-generated analysis, not a factual claim about the publisher.",
    }

    return data


# --- Translation ----------------------------------------------------------

_LANG_NAME = {"en": "English", "id": "Bahasa Indonesia"}

_TRANSLATE_SYSTEM = """You translate structured JSON news summaries between English and Bahasa Indonesia.

Rules:
- You will receive a JSON object and a target language.
- Return ONLY a JSON object with the SAME schema and keys. No prose, no fences.
- Translate every human-readable string value: title, category, executive_summary, each item in key_points, main_issue.summary + significance, root_cause.causes items + certainty_note, recommended_actions.immediate/short_term/long_term items + disclaimer, five_w_one_h.who/what/when/where/why/how, each references[].title and references[].website, sentiment_and_bias.tone_explanation + each item in sentiment_and_bias.bias_indicators + sentiment_and_bias.disclaimer, and confidence_level.reason.
- DO NOT change: any url values, publication_date, generated_at, reading_time_minutes, confidence_level.level (keep as High/Medium/Low), sentiment_and_bias.tone (keep as Positive/Neutral/Negative/Mixed), or any references[].date.
- Keep proper nouns and organisation names natural (translate only where a standard translation exists).
- Do not add or remove keys."""


async def translate_summary(summary: dict[str, Any], target: str
                            ) -> dict[str, Any]:
    """Translate a full summary payload to the target language ('en'|'id')."""
    if target not in _LANG_NAME:
        raise RuntimeError("Unsupported target language.")

    key = os.environ["EMERGENT_LLM_KEY"]
    tag = hashlib.sha256(
        (summary.get("article", {}).get("original_url", "") + target).encode()
    ).hexdigest()[:12]
    chat = LlmChat(
        api_key=key,
        session_id=f"translate-{tag}",
        system_message=_TRANSLATE_SYSTEM,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929").with_params(
        max_tokens=4096,
    )

    prompt = (
        f"TARGET_LANGUAGE: {_LANG_NAME[target]}\n\n"
        f"SUMMARY_JSON:\n{json.dumps(summary, ensure_ascii=False)}"
    )
    response = await chat.send_message(UserMessage(text=prompt))
    raw = response if isinstance(response, str) else str(response)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("translate returned non-JSON: %s", raw[:400])
        raise RuntimeError(f"Translation response could not be parsed: {e}") from e

    # Preserve non-translated fields defensively (never trust the model)
    article = data.get("article") or {}
    original_article = summary.get("article", {})
    for key_preserve in (
        "original_url", "publication_date", "generated_at",
        "reading_time_minutes", "site_name",
    ):
        if key_preserve in original_article:
            article[key_preserve] = original_article[key_preserve]
    data["article"] = article

    # References URLs preserved
    src_refs = summary.get("references") or []
    out_refs = data.get("references") or []
    for i, r in enumerate(out_refs):
        if i < len(src_refs):
            r["url"] = src_refs[i].get("url", r.get("url"))
            r["date"] = src_refs[i].get("date")
    data["references"] = out_refs

    # Confidence level (High/Medium/Low) — enforce
    cl = data.get("confidence_level") or {}
    src_cl = summary.get("confidence_level") or {}
    cl["level"] = src_cl.get("level", cl.get("level", "Medium"))
    data["confidence_level"] = cl

    return data



# --- Tavily live search ---------------------------------------------------

_TRUSTED_DOMAINS = [
    "reuters.com", "bloomberg.com", "apnews.com", "bbc.com", "bbc.co.uk",
    "cnbc.com", "cnn.com", "detik.com", "kompas.com", "tempo.co",
    "bisnis.com", "antaranews.com", "x.com", "twitter.com",
    "threads.net", "instagram.com", "tiktok.com",
]


def fetch_latest_updates(topic: str, exclude_url: Optional[str] = None,
                          entities: Optional[list[str]] = None
                         ) -> list[dict[str, Any]]:
    """Query Tavily for the latest news on the topic. Returns list, newest first.

    If `entities` is provided, the search query is built from those exact
    named entities (not the generic topic string), and any result that
    doesn't mention at least one entity is filtered out.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key or api_key.startswith("REPLACE"):
        raise RuntimeError("Live search is not configured. Set TAVILY_API_KEY.")

    entities = [e.strip() for e in (entities or []) if e and e.strip()]
    query = " ".join(entities) if entities else topic

    client = TavilyClient(api_key=api_key)
    try:
        resp = client.search(
            query=query,
            topic="news",
            time_range="month",
            max_results=10,
            include_domains=_TRUSTED_DOMAINS,
        )
    except Exception as e:  # tavily raises various exceptions
        logger.exception("Tavily error")
        raise RuntimeError(f"Live search failed: {e}") from e

    results = resp.get("results", []) or []
    logger.info(f"[DEBUG] query='{query}' entities={entities} tavily_raw_count={len(results)}")
    updates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    entities_lower = [e.lower() for e in entities]

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
    logger.info(f"[DEBUG] after_entity_filter_count={len(updates)}")
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

    async def get_cached(self, h: str, language: str = "id"
                         ) -> Optional[dict[str, Any]]:
        """Return the cached summary in the requested language, or None if the
        cache is missing/expired or the requested translation is not stored yet.
        The primary (Indonesian) payload lives under `payload`; every additional
        language is stored under `translations.<lang>`."""
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
        if language == "id":
            return doc.get("payload")
        translations = doc.get("translations") or {}
        return translations.get(language)

    async def get_cache_doc(self, h: str) -> Optional[dict[str, Any]]:
        """Return the whole cache document (fresh only), or None."""
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
        return doc

    async def set_translation(self, h: str, language: str,
                              translated: dict[str, Any]) -> None:
        await self.cache.update_one(
            {"hash": h},
            {"$set": {f"translations.{language}": translated}},
        )

    async def list_recent(self, limit: int = 6) -> list[dict[str, Any]]:
        """Return the most recent still-fresh cache entries (metadata only)
        for the global 'Recent News (Last 24h)' feed. Newest first.

        Only entries within the 24 h TTL are returned so expired cache rows
        drop out naturally without a separate history store."""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=self.ttl_hours)).isoformat()
        cursor = (
            self.cache
            .find(
                {"created_at": {"$gte": cutoff}},
                {
                    "_id": 0,
                    "hash": 1,
                    "url": 1,
                    "created_at": 1,
                    "payload.article.title": 1,
                    "payload.article.category": 1,
                    "payload.article.site_name": 1,
                    "payload.article.generated_at": 1,
                    "payload.article.original_url": 1,
                    "payload.article.reading_time_minutes": 1,
                },
            )
            .sort("created_at", -1)
            .limit(max(1, min(limit, 50)))
        )
        rows = await cursor.to_list(length=max(1, min(limit, 50)))
        out: list[dict[str, Any]] = []
        for r in rows:
            art = ((r.get("payload") or {}).get("article")) or {}
            out.append({
                "hash": r.get("hash"),
                "url": art.get("original_url") or r.get("url"),
                "title": art.get("title") or "Untitled",
                "category": art.get("category") or "General",
                "site_name": art.get("site_name")
                    or urlparse(r.get("url", "")).netloc.replace("www.", ""),
                "generated_at": art.get("generated_at") or r.get("created_at"),
                "reading_time_minutes": art.get("reading_time_minutes") or 3,
            })
        return out

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

    async def check_rate(self, ip: str, bucket: str, limit: int
                         ) -> tuple[bool, int, int]:
        """Sliding-hour window per IP.
        Returns (allowed, remaining, retry_after_seconds).
        retry_after_seconds is the wait until the oldest in-window entry
        expires (0 when still allowed)."""
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=1)
        # Purge old
        await self.rate.delete_many({"ts": {"$lt": window_start.isoformat()}})
        docs = await self.rate.find({
            "ip": ip, "bucket": bucket,
            "ts": {"$gte": window_start.isoformat()},
        }).sort("ts", 1).limit(limit).to_list(limit)
        count = len(docs)
        if count >= limit:
            oldest_ts = docs[0]["ts"]
            try:
                oldest_dt = datetime.fromisoformat(oldest_ts)
            except ValueError:
                oldest_dt = now
            retry_after = int(
                (oldest_dt + timedelta(hours=1) - now).total_seconds()
            )
            return False, 0, max(retry_after, 1)
        await self.rate.insert_one({
            "ip": ip, "bucket": bucket, "ts": now.isoformat(),
        })
        return True, limit - count - 1, 0

    async def peek_rate(self, ip: str, bucket: str, limit: int
                        ) -> tuple[int, int]:
        """Return (remaining, retry_after_seconds) without consuming."""
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=1)
        docs = await self.rate.find({
            "ip": ip, "bucket": bucket,
            "ts": {"$gte": window_start.isoformat()},
        }).sort("ts", 1).limit(limit).to_list(limit)
        count = len(docs)
        remaining = max(0, limit - count)
        retry_after = 0
        if remaining == 0 and docs:
            try:
                oldest_dt = datetime.fromisoformat(docs[0]["ts"])
                retry_after = max(
                    1,
                    int((oldest_dt + timedelta(hours=1) - now).total_seconds()),
                )
            except ValueError:
                retry_after = 0
        return remaining, retry_after
