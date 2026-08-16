"""Business logic for News Summarizer: URL normalization, article extraction,
Claude summarization, Tavily live search, MongoDB caching and rate limiting."""
from __future__ import annotations

import asyncio
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

_DELTA_SYSTEM_PROMPT = """You are analyzing what has genuinely CHANGED since a news article was published — a delta, not a re-summary.

You will receive:
1. BASELINE — the original article's title, key entities, key points, main issue, and publication date. Treat this as everything the reader already knows.
2. CANDIDATE ARTICLES, GROUPED BY PUBLISH DATE — an ordered list of date clusters (oldest to newest; unknown dates last). Each candidate has: source domain, title, url, published date, content_kind, and content. content_kind is either "full_text" (the full extracted article body — you may draw detailed facts, numbers, and quotes from it) or "snippet" (a short search excerpt — use ONLY what the snippet explicitly states).

Your job: identify ONLY what these candidates reveal that is NEW relative to the baseline — new facts, new numbers, new statements, new official/company responses, new market reactions, new regulatory action, new consequences. Then compose a rich, precise update in the style of a well-briefed analyst: concrete dates, figures, percentages, names, and institutions — never vague phrases like "ada perkembangan baru". Ignore any candidate that merely restates baseline facts in different words.

Return a single JSON object matching this schema — no prose, no markdown fences:

{
  "has_update": true or false,
  "overview": "string, 2-4 sentences in Bahasa Indonesia summarizing how the story has moved since the baseline — may open with one short sentence of context connecting baseline to the newest state — empty string if has_update is false",
  "developments": ["array of AT MOST 6 strings, each ONE genuinely new development stated with its concrete specifics (date, number, actor) — empty array if none"],
  "timeline": [{"date": "YYYY-MM-DD or null if unknown", "event": "string — ONE concise sentence (max ~25 words) describing the event with specifics", "sources": ["exact candidate URLs that report THIS event"]}],
  "current_situation": "string or null — the latest concrete state of affairs, only if the candidates actually state it; otherwise null, do not infer",
  "market_impact": "string or null — only fill if a candidate explicitly discusses market/investor/stakeholder impact; otherwise null",
  "confidence": {"level": "High | Medium | Low", "reason": "string"},
  "sources_used": ["array of the exact URLs, from the candidate list, that you actually drew information from — never include a URL you did not use"]
}

Rules:
- Language: every human-readable string must be in Bahasa Indonesia. Keep confidence.level as exactly "High", "Medium", or "Low".
- DATE ANCHORING (critical): a candidate only counts as an update if its published date is genuinely after the baseline's publication date. If a candidate's date is missing or unparseable, you may still use it but note the reduced certainty in confidence.reason. If a candidate's date is on or before the baseline date, discard it entirely.
- TIMELINE DISCIPLINE: build the timeline strictly in chronological order using the date clusters you were given. AT MOST 6 entries, one per distinct real-world event. Every timeline entry MUST list in "sources" the exact candidate URL(s) that report that event — never an empty array, never a URL not in the candidate list.
- BE CONCISE: precision over length. Do not pad. Total output should stay compact — dense facts, no filler phrases.
- Never repeat or re-explain baseline facts.
- Never speculate or infer beyond what a candidate's content explicitly states. For "snippet" candidates, if the snippet is too thin to state a concrete development, leave it out rather than guess.
- SOURCE PRIORITY: prefer local Indonesian outlets with direct coverage (Kompas, Bisnis.com, Detik, Tempo, Antara) as primary evidence for Indonesia-specific events. Treat international wire coverage (Reuters, Bloomberg, CNBC, AP, BBC) as corroboration or global-impact context, not an automatic override of more specific local reporting.
- Merge duplicate reports of the same event (different outlets, different wording) into ONE timeline entry and ONE development — but list every corroborating URL in that entry's "sources" and in "sources_used".
- If no candidate contains a genuine post-baseline development, set has_update to false, overview to an empty string, developments to an empty array, and explain in confidence.reason that no significant change was found.
- Every fact in "overview", "developments", "current_situation", and "market_impact" must be traceable to at least one URL in "sources_used"."""


async def synthesize_delta(baseline: dict[str, Any],
                            clusters: list[dict[str, Any]]
                           ) -> dict[str, Any]:
    """Call Claude to turn date-clustered candidates (full text where fetched,
    snippet otherwise) into a genuine delta versus the baseline article.
    Returns the parsed JSON dict. Timeline events carry per-event sources."""
    key = os.environ["EMERGENT_LLM_KEY"]
    n_candidates = sum(len(c.get("articles") or []) for c in clusters)
    tag = hashlib.sha256(
        (baseline.get("source_url", "") + str(n_candidates)).encode()
    ).hexdigest()[:12]

    chat = LlmChat(
        api_key=key,
        session_id=f"delta-{tag}",
        system_message=_DELTA_SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929").with_params(
        max_tokens=3072,
    )

    prompt = (
        f"BASELINE:\n{json.dumps(baseline, ensure_ascii=False)}\n\n"
        f"CANDIDATE ARTICLES GROUPED BY PUBLISH DATE (oldest cluster first):\n"
        f"{json.dumps(clusters, ensure_ascii=False)}"
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
        logger.error("delta synthesis returned non-JSON: %s", raw[:400])
        raise RuntimeError(f"Delta synthesis could not be parsed: {e}") from e

    # Defensive defaults — never trust the model to include every key.
    data.setdefault("has_update", bool(data.get("developments")))
    data.setdefault("overview", "")
    data.setdefault("developments", [])
    data.setdefault("timeline", [])
    data.setdefault("current_situation", None)
    data.setdefault("market_impact", None)
    data.setdefault("sources_used", [])
    conf = data.get("confidence") or {}
    if conf.get("level") not in ("High", "Medium", "Low"):
        conf["level"] = "Low"
    conf.setdefault("reason", "")
    data["confidence"] = conf

    # Validate every URL the model claims against the actual candidate pool —
    # a hallucinated URL must never reach the UI or the confidence scorer.
    valid_urls = {
        (a.get("url") or "").strip()
        for c in clusters for a in (c.get("articles") or [])
    }
    valid_urls.discard("")
    data["sources_used"] = [
        u for u in data["sources_used"]
        if isinstance(u, str) and u.strip() in valid_urls
    ]
    timeline: list[dict[str, Any]] = []
    for ev in data["timeline"]:
        if not isinstance(ev, dict) or not (ev.get("event") or "").strip():
            continue
        srcs = [
            u for u in (ev.get("sources") or [])
            if isinstance(u, str) and u.strip() in valid_urls
        ]
        timeline.append({
            "date": ev.get("date"),
            "event": ev["event"].strip(),
            "sources": srcs,
        })
    data["timeline"] = timeline

    return data

# --- Latest Updates pipeline ------------------------------------------------
# build_queries -> search_candidates (concurrent) -> web_fetch_candidates
# -> cluster_by_date -> synthesize_delta -> compute_confidence -> merge_timeline

# Lower to 3 if the Tavily free quota gets exhausted — keep the mechanism,
# just change the number.
LATEST_UPDATES_QUERY_COUNT = 8
LATEST_UPDATES_FETCH_COUNT = 8   # candidates whose full page we fetch
_SEARCH_RESULTS_PER_QUERY = 6
_MAX_CANDIDATES_FOR_LLM = 10
_FETCH_TIMEOUT_SECONDS = 8       # short — several fetches run concurrently
_FULLTEXT_CHAR_CAP = 2000

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": ("text/html,application/xhtml+xml,application/xml;"
               "q=0.9,image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "id-ID,id;q=0.9,en-US,en;q=0.8",
}


def _parse_date_str(dstr: str) -> Optional[datetime]:
    """Parse an RFC-2822 or ISO date string; None if unparseable."""
    if not dstr:
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(dstr)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(dstr.replace("Z", "+00:00"))
    except Exception:
        return None


def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return ""


def _word_set(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w-]+", (text or "").lower()) if len(w) > 3}


def build_queries(topic: str, entities: Optional[list[str]] = None,
                  baseline_date: Optional[str] = None) -> list[str]:
    """Build up to LATEST_UPDATES_QUERY_COUNT search query variants from the
    baseline article's topic/entities — combined-entities, '{entity} terbaru',
    '{entity} since {baseline date}', etc. No LLM call: entities come from the
    original /summarize result."""
    entities = [e.strip() for e in (entities or []) if e and e.strip()]
    topic = (topic or "").strip()

    variants: list[str] = []
    combined = " ".join(entities[:4]) if entities else topic
    if combined:
        variants.append(combined)
    if entities:
        e0 = entities[0]
        if len(entities) >= 2:
            variants.append(f"{e0} {entities[1]}")
        variants.append(f"{e0} terbaru")
        variants.append(f"{e0} latest news")
        variants.append(f"{e0} hari ini")
        if baseline_date:
            variants.append(f"{e0} since {str(baseline_date)[:10]}")
        if len(entities) >= 2:
            variants.append(f"{entities[1]} terbaru")
    if topic:
        variants.append(topic)
        variants.append(f"{topic} perkembangan terbaru")

    # Dedupe case-insensitively, preserve order, cap.
    seen: set[str] = set()
    queries: list[str] = []
    for q in variants:
        key = q.lower()
        if key and key not in seen:
            seen.add(key)
            queries.append(q)
    return queries[:LATEST_UPDATES_QUERY_COUNT]


def _title_similar(a: str, b: str, threshold: float = 0.8) -> bool:
    wa, wb = _word_set(a), _word_set(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= threshold

def _compute_search_days(baseline_date: Optional[str]) -> int:
    """How far back Tavily should search. Floor of 4 days for a fresh
    baseline. If the baseline article is older than that, extend the window
    to cover the full gap since it was published — otherwise a genuine
    update from, say, 6 months ago against a 6-month-old baseline falls
    outside a fixed 30-day window and gets silently missed (this was the
    root cause of "no updates found" on older articles). Capped at 400 days
    so a very old baseline doesn't pull in irrelevantly old "updates"."""
    if not baseline_date:
        return 30
    parsed = _parse_date_str(baseline_date)
    if not parsed:
        return 30
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    gap_days = (datetime.now(timezone.utc) - parsed).days
    return max(4, min(gap_days + 1, 400))

async def search_candidates(queries: list[str],
                            exclude_url: Optional[str] = None,
                            baseline_date: Optional[str] = None,
                           ) -> list[dict[str, Any]]:
    """Run ALL queries against Tavily CONCURRENTLY (sequential execution was
    measured to add 20+ seconds), pool the results, dedupe by URL and by
    near-identical titles, and return candidates sorted newest first."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key or api_key.startswith("REPLACE"):
        raise RuntimeError("Live search is not configured. Set TAVILY_API_KEY.")
    if not queries:
        return []

    tavily_client = TavilyClient(api_key=api_key)
    search_days = _compute_search_days(baseline_date)

    started = datetime.now(timezone.utc)
    results = await asyncio.gather(
        *[asyncio.to_thread(lambda q=q: tavily_client.search(
            query=q,
            topic="news",
            day=search_days,
            max_results=_SEARCH_RESULTS_PER_QUERY,
            include_domains=_TRUSTED_DOMAINS,
        )) for q in queries],
        return_exceptions=True,
    )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    pooled: list[dict[str, Any]] = []
    failed = 0
    for resp in results:
        if isinstance(resp, Exception):
            failed += 1
            logger.warning("Tavily query failed: %s", resp)
            continue
        pooled.extend(resp.get("results", []) or [])
    logger.info("[latest-updates] %d queries in %.1fs (%d failed), pooled=%d",
                len(queries), elapsed, failed, len(pooled))
    if failed == len(queries):
        raise RuntimeError("Live search failed: every search query errored.")

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for r in pooled:
        url = (r.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        try:
            if exclude_url and normalize_url(url) == exclude_url:
                continue
        except ValueError:
            pass
        title = r.get("title") or ""
        # Near-identical title from another query result -> same story, skip.
        if any(_title_similar(title, c["title"]) for c in candidates):
            continue
        seen_urls.add(url)
        pub = r.get("published_date") or r.get("publication_date")
        candidates.append({
            "date": pub,
            "source": _domain_of(url),
            "title": title,
            "summary": (r.get("content") or "")[:400],
            "url": url,
        })

    # Sort newest first — parseable dates first, undated last.
    def _sort_key(u: dict[str, Any]) -> tuple[int, float]:
        parsed = _parse_date_str(u.get("date") or "")
        if parsed is None:
            return (1, 0.0)
        return (0, -parsed.timestamp())

    candidates.sort(key=_sort_key)
    return candidates[:_MAX_CANDIDATES_FOR_LLM]


def _fetch_page_text(url: str) -> Optional[str]:
    """Fetch one page and extract its article text with trafilatura (the same
    extraction library used for the main article). Returns None on ANY failure
    so the caller can fall back to the search snippet."""
    try:
        import requests
        resp = requests.get(url, headers=_BROWSER_HEADERS,
                            timeout=_FETCH_TIMEOUT_SECONDS,
                            allow_redirects=True)
        if resp.status_code != 200 or not resp.text:
            return None
        text = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if text and len(text.strip()) >= 200:
            return text.strip()
        return None
    except Exception as e:
        logger.info("web_fetch failed for %s: %s", url, e)
        return None


async def web_fetch_candidates(candidates: list[dict[str, Any]]
                               ) -> list[dict[str, Any]]:
    """Fetch the full page for the top LATEST_UPDATES_FETCH_COUNT candidates
    CONCURRENTLY. Mandatory fallback: any candidate whose fetch fails keeps
    its search snippet — one blocked site never fails the whole check."""
    to_fetch = candidates[:LATEST_UPDATES_FETCH_COUNT]

    started = datetime.now(timezone.utc)
    texts = await asyncio.gather(
        *[asyncio.to_thread(_fetch_page_text, c["url"]) for c in to_fetch],
        return_exceptions=True,
    )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    fetched_ok = 0
    for c, text in zip(to_fetch, texts):
        if isinstance(text, str) and text:
            c["content"] = text[:_FULLTEXT_CHAR_CAP]
            c["content_kind"] = "full_text"
            fetched_ok += 1
        else:
            c["content"] = c.get("summary") or ""
            c["content_kind"] = "snippet"
    for c in candidates[LATEST_UPDATES_FETCH_COUNT:]:
        c["content"] = c.get("summary") or ""
        c["content_kind"] = "snippet"
    logger.info("[latest-updates] web_fetch %d/%d full pages in %.1fs",
                fetched_ok, len(to_fetch), elapsed)
    return candidates


def cluster_by_date(candidates: list[dict[str, Any]]
                    ) -> list[dict[str, Any]]:
    """Group candidates by publish day BEFORE synthesis so the LLM receives an
    ordered, date-grouped structure instead of a flat unordered list — with
    multi-query pooling the pool is larger, and ungrouped input makes a messy,
    out-of-order timeline far more likely."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        parsed = _parse_date_str(c.get("date") or "")
        key = parsed.date().isoformat() if parsed else "unknown"
        groups.setdefault(key, []).append({
            "source": c.get("source"),
            "title": c.get("title"),
            "url": c.get("url"),
            "published": c.get("date"),
            "content_kind": c.get("content_kind", "snippet"),
            "content": c.get("content", c.get("summary") or ""),
        })

    dated = sorted(k for k in groups if k != "unknown")
    ordered = dated + (["unknown"] if "unknown" in groups else [])
    return [{"date": k, "articles": groups[k]} for k in ordered]


def _overlap_domains(event: dict[str, Any],
                     candidates: list[dict[str, Any]]) -> set[str]:
    """Fallback corroboration matching: which candidate domains' text overlaps
    this timeline event's wording enough to plausibly report it."""
    ev_words = _word_set(event.get("event") or "")
    if not ev_words:
        return set()
    domains: set[str] = set()
    for c in candidates:
        cand_words = _word_set((c.get("title") or "") + " "
                               + (c.get("content") or c.get("summary") or ""))
        if not cand_words:
            continue
        if len(ev_words & cand_words) / len(ev_words) >= 0.3:
            domains.add(c.get("source") or _domain_of(c.get("url") or ""))
    domains.discard("")
    return domains


def compute_confidence(candidates: list[dict[str, Any]],
                       delta: dict[str, Any],
                       now: Optional[datetime] = None) -> dict[str, Any]:
    """Deterministic 3-factor confidence — computed in code, never trusted
    from the LLM's self-assessment. One point each:
      1. distinct/independent source domains used >= 4
      2. share of timeline events corroborated by >= 2 domains >= 50%
      3. most recent candidate used is within the last 48 hours
    score >= 2 -> High, 1 -> Medium, 0 -> Low."""
    now = now or datetime.now(timezone.utc)
    by_url = {c["url"]: c for c in candidates}
    sources_used = [u for u in (delta.get("sources_used") or []) if u in by_url]

    # Factor 1 — independent domains
    used_domains = {_domain_of(u) for u in sources_used}
    used_domains.discard("")
    f1 = len(used_domains) >= 4

    # Factor 2 — corroboration share over the NEWLY synthesized timeline
    timeline = [e for e in (delta.get("timeline") or []) if isinstance(e, dict)]
    corroborated = 0
    for ev in timeline:
        doms = {_domain_of(u) for u in (ev.get("sources") or []) if u in by_url}
        doms.discard("")
        if len(doms) < 2:
            doms |= _overlap_domains(ev, candidates)
        if len(doms) >= 2:
            corroborated += 1
    share = (corroborated / len(timeline)) if timeline else 0.0
    f2 = bool(timeline) and share >= 0.5

    # Factor 3 — recency of the newest candidate actually used
    newest: Optional[datetime] = None
    for u in sources_used:
        d = _parse_date_str(by_url[u].get("date") or "")
        if d is None:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        if newest is None or d > newest:
            newest = d
    f3 = newest is not None and (now - newest) <= timedelta(hours=48)

    score = int(f1) + int(f2) + int(f3)
    level = "High" if score >= 2 else ("Medium" if score == 1 else "Low")

    recency_txt = (
        f"sumber terbaru berumur {int((now - newest).total_seconds() // 3600)} jam"
        if newest is not None else "tanggal sumber terbaru tidak diketahui"
    )
    reason = (
        f"Skor deterministik {score}/3 — "
        f"{len(used_domains)} domain sumber independen"
        f" ({'>=4' if f1 else '<4'}); "
        f"{corroborated}/{len(timeline) or 0} peristiwa timeline terkorroborasi "
        f"oleh >=2 domain ({int(share * 100)}%); {recency_txt}."
    )
    return {
        "level": level,
        "reason": reason,
        "score": score,
        "factors": {
            "independent_domains": len(used_domains),
            "independent_domains_ok": f1,
            "corroborated_share": round(share, 2),
            "corroboration_ok": f2,
            "newest_within_48h": f3,
        },
    }


def same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Similarity-based event matching (Jaccard >= 0.5 on same-day events) —
    NOT exact string matching: different sources routinely report the same
    event with different wording, and exact-match fills the timeline with
    duplicates."""
    da = (a.get("date") or "")
    db = (b.get("date") or "")
    if str(da)[:10] != str(db)[:10]:
        return False
    wa = set((a.get("event") or "").lower().split())
    wb = set((b.get("event") or "").lower().split())
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.5  # Jaccard similarity


def _timeline_sort_key(ev: dict[str, Any]) -> tuple[int, str]:
    d = str(ev.get("date") or "")[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return (0, d)
    return (1, "")


def merge_timeline(existing: list[dict[str, Any]],
                   new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge the newly synthesized timeline INTO the accumulated one — the
    timeline only ever grows, it is never regenerated from scratch. Duplicate
    detection uses same_event (word-overlap), unioning sources and keeping the
    more detailed wording."""
    merged = [dict(e) for e in (existing or []) if isinstance(e, dict)]
    for ev in (new or []):
        if not isinstance(ev, dict) or not (ev.get("event") or "").strip():
            continue
        match = next((m for m in merged if same_event(m, ev)), None)
        if match is not None:
            srcs = list(dict.fromkeys(
                (match.get("sources") or []) + (ev.get("sources") or [])
            ))
            if srcs:
                match["sources"] = srcs
            if len(ev.get("event") or "") > len(match.get("event") or ""):
                match["event"] = ev["event"]
            if not match.get("date") and ev.get("date"):
                match["date"] = ev["date"]
        else:
            merged.append(dict(ev))
    merged.sort(key=_timeline_sort_key)
    return merged


# --- MongoDB cache + rate limit -------------------------------------------

class CacheAndRateLimit:
    """Small helper that owns both the cached-summary store and the per-IP
    rate limit counters. Both live in MongoDB."""

    def __init__(self, db):
        self.db = db
        self.cache = db.summary_cache
        self.rate = db.rate_limit
        # Accumulated Latest-Updates timelines — deliberately SEPARATE from the
        # short-TTL delta cache so the timeline survives cache expiry and only
        # ever grows via merge_timeline, never gets regenerated from scratch.
        self.timeline = db.timeline_store
        self.ttl_hours = int(os.environ.get("CACHE_TTL_HOURS", "12"))

    async def ensure_indexes(self) -> None:
        await self.cache.create_index("hash", unique=True)
        await self.cache.create_index("created_at")
        await self.rate.create_index([("ip", 1), ("bucket", 1), ("ts", 1)])
        await self.timeline.create_index("hash", unique=True)

    async def get_timeline(self, h: str) -> list[dict[str, Any]]:
        """Load the accumulated timeline for an article. No TTL — persists
        far beyond the 12h delta cache."""
        doc = await self.timeline.find_one({"hash": h}, {"_id": 0})
        return (doc or {}).get("timeline") or []

    async def save_timeline(self, h: str, timeline: list[dict[str, Any]]
                            ) -> None:
        await self.timeline.update_one(
            {"hash": h},
            {"$set": {
                "hash": h,
                "timeline": timeline,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )

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
    
    async def get_cached_delta(self, h: str) -> Optional[dict[str, Any]]:
        """Return the cached Latest-Update delta if it's still within its own
        12h freshness window (shorter than the 12h main summary cache, since
        'what's new' goes stale faster than the summary itself)."""
        doc = await self.cache.find_one({"hash": h}, {"_id": 0})
        if not doc:
            return None
        delta = doc.get("latest_update_delta")
        generated_at = doc.get("delta_generated_at")
        if not delta or not generated_at:
            return None
        try:
            generated_dt = datetime.fromisoformat(generated_at)
        except ValueError:
            return None
        if datetime.now(timezone.utc) - generated_dt > timedelta(hours=12):
            return None
        return delta

    async def set_cached_delta(self, h: str, delta: dict[str, Any]) -> None:
        await self.cache.update_one(
            {"hash": h},
            {"$set": {
                "latest_update_delta": delta,
                "delta_generated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )

    async def list_recent(self, limit: int = 6) -> list[dict[str, Any]]:
        """Return the most recent still-fresh cache entries (metadata only)
        for the global 'Recent News (Last 12h)' feed. Newest first.

        Only entries within the 12 h TTL are returned so expired cache rows
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
