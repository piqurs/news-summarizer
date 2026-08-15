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
  "impact_analysis": {
    "items": [
      {"aspect": "string — short label for who/what is affected, e.g. 'Harga di pasar', 'Bank yang diuntungkan', 'Bank yang dirugikan', 'Emiten terdampak', 'Sektor IHSG terkait'", "direction": "Positive | Negative | Mixed | Unclear — pick exactly one of these four English literals", "certainty": "Stated | Inferred — pick exactly one of these two English literals. Use 'Stated' ONLY if the article itself explicitly says this consequence/impact. Use 'Inferred' if you reasoned it from general domain knowledge that is NOT stated in the article — this will be the majority of items, and that is expected, not a flaw.", "description": "string — 1-2 sentences on how and why, grounded in the article's facts plus well-established domain reasoning"}
    ],
    "disclaimer": "AI-generated impact analysis based on the article's content and general domain knowledge — not financial, investment, or professional advice."
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
- Language: Every human-readable string value MUST be written in Bahasa Indonesia (Indonesian), regardless of the source article's language. This includes title, category, executive_summary, key_points, main_issue.*, root_cause.*, recommended_actions.* items and disclaimer, impact_analysis.items[].aspect/description and disclaimer, five_w_one_h.*, references[].title/website, sentiment_and_bias.tone_explanation, sentiment_and_bias.bias_indicators[], sentiment_and_bias.disclaimer, confidence_level.reason.
- Keep the following as-is in their original form: URLs, publication_date, dates in references, the confidence_level.level value which MUST remain exactly one of the literal English strings "High", "Medium", or "Low", the sentiment_and_bias.tone value which MUST remain exactly one of "Positive", "Neutral", "Negative", or "Mixed", AND impact_analysis.items[].direction which MUST remain exactly one of "Positive", "Negative", "Mixed", or "Unclear", AND impact_analysis.items[].certainty which MUST remain exactly one of "Stated" or "Inferred".
- Keep proper nouns (people, organisations, places) natural — translate only when a standard Indonesian equivalent exists.
- Never invent facts. If missing, say so in the relevant section (in Indonesian).
- REFERENCES RULE (STRICT): Every references[] entry MUST include a real, retrievable http(s):// URL that is either the source article itself or explicitly cited in the article body with a resolvable link. NEVER emit a reference entry with a missing, empty, "#", "unknown", or otherwise non-navigable url field. If a source is mentioned in the article but no retrievable URL exists for it, OMIT that reference entirely — fold the attribution into body text if needed, but do NOT create a reference card. When in doubt, omit.
- IMPACT ANALYSIS: identify concrete stakeholders/aspects the article's news would plausibly affect, and explain the direction grounded in the article's actual facts plus well-established general domain reasoning (e.g., "suku bunga naik → biaya kredit naik → bank dengan porsi kredit besar bisa tertekan marginnya, bank dengan pendapatan dari dana murah relatif diuntungkan"). Set certainty honestly per item: "Stated" only when the article itself says this consequence will happen; "Inferred" for everything reasoned from domain knowledge the article does not state — most items will legitimately be "Inferred", and that is fine as long as it's labeled correctly; do not mark something "Stated" just to sound more authoritative. For business/economic/financial articles, prioritize aspects like affected market prices, which side benefits vs. loses, affected listed companies/sectors, and related stock-index sectors — matching the shape of the article's actual subject, not a fixed checklist. For non-financial articles (sports, politics, health, entertainment, etc.), adapt the aspects to whatever stakeholders are actually relevant to that domain — e.g. a sports team winning a championship might affect jersey/merchandise sales, tourism to the team's city, sponsorship deal values; do not force market/financial framing onto non-financial news. Do NOT name a specific individual company or ticker unless the article itself discusses that company/sector, or it is extremely well-established general knowledge (e.g., naming major national banks in the context of a systemic policy change) — never invent an obscure specific stock claim not grounded in the article. This is analytical context, not a recommendation to buy, sell, or hold anything — phrase every item descriptively ("sektor X berpotensi tertekan karena...") rather than prescriptively ("sebaiknya membeli/menjual X"). If the article genuinely has no clear differential impact on identifiable stakeholders (e.g. a general human-interest story), return an EMPTY items array rather than manufacturing weak or generic impacts.
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

    # Defensive default for impact_analysis. Also drop any item missing an
    # aspect/description (a malformed item is worse than no item) and
    # normalize direction to one of the four allowed literals.
    ia = data.get("impact_analysis") or {}
    items = []
    for it in (ia.get("items") or []):
        if not isinstance(it, dict):
            continue
        aspect = (it.get("aspect") or "").strip()
        description = (it.get("description") or "").strip()
        if not aspect or not description:
            continue
        direction = it.get("direction")
        if direction not in ("Positive", "Negative", "Mixed", "Unclear"):
            direction = "Unclear"
        certainty = it.get("certainty")
        if certainty not in ("Stated", "Inferred"):
            certainty = "Inferred"  # default to the more honest/cautious label
        items.append({
            "aspect": aspect, "direction": direction,
            "certainty": certainty, "description": description,
        })
    data["impact_analysis"] = {
        "items": items,
        "disclaimer": ia.get("disclaimer")
        or "AI-generated impact analysis based on the article's content and "
           "general domain knowledge — not financial, investment, or "
           "professional advice.",
    }

    return data


# --- Translation ----------------------------------------------------------

_LANG_NAME = {"en": "English", "id": "Bahasa Indonesia"}

_TRANSLATE_SYSTEM = """You translate structured JSON news summaries between English and Bahasa Indonesia.

Rules:
- You will receive a JSON object and a target language.
- Return ONLY a JSON object with the SAME schema and keys. No prose, no fences.
- Translate every human-readable string value: title, category, executive_summary, each item in key_points, main_issue.summary + significance, root_cause.causes items + certainty_note, recommended_actions.immediate/short_term/long_term items + disclaimer, impact_analysis.items[].aspect/description + disclaimer, five_w_one_h.who/what/when/where/why/how, each references[].title and references[].website, sentiment_and_bias.tone_explanation + each item in sentiment_and_bias.bias_indicators + sentiment_and_bias.disclaimer, and confidence_level.reason.
- DO NOT change: any url values, publication_date, generated_at, reading_time_minutes, confidence_level.level (keep as High/Medium/Low), sentiment_and_bias.tone (keep as Positive/Neutral/Negative/Mixed), impact_analysis.items[].direction (keep as Positive/Negative/Mixed/Unclear), impact_analysis.items[].certainty (keep as Stated/Inferred), or any references[].date.
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
    "bisnis.com", "antaranews.com",
    "cnbcindonesia.com", "kontan.co.id", "katadata.co.id", "investor.id",
    "idxchannel.com", "liputan6.com", "thejakartapost.com", "viva.co.id",
    "x.com", "twitter.com", "threads.net", "instagram.com", "tiktok.com",
]

_DELTA_SYSTEM_PROMPT = """You are analyzing what has genuinely CHANGED since a news article was published — a delta, not a re-summary.

You will receive:
1. BASELINE — the original article's title, key entities, key points, main issue, and publication date. Treat this as everything the reader already knows.
2. CANDIDATE ARTICLES — grouped by publish date, a list of more recent articles (title + snippet or full text + source + published date each), already filtered to mention the same named entities as the baseline. Some entries have a full article extract instead of a short snippet — use whatever depth is available, but do not treat a longer entry as more trustworthy just because it's longer.

Your job: identify ONLY what these candidates reveal that is NEW relative to the baseline — new facts, new numbers, new statements, new official/company responses, new market reactions, new regulatory action, new consequences. Ignore any candidate that merely restates baseline facts in different words.

Return a single JSON object matching this schema — no prose, no markdown fences:

{
  "has_update": true or false,
  "overview": "string, 2-4 sentences in Bahasa Indonesia summarizing what has changed — empty string if has_update is false",
  "developments": ["array of short strings, each ONE genuinely new development — empty array if none"],
  "timeline": [{"date": "string or null", "event": "string", "source_urls": ["array of exact candidate URLs that support this specific event — used to check corroboration and to merge this event across future refreshes, so include every candidate URL that mentions it, not just one"]}],
  "current_situation": "string or null — only fill if the candidates actually state a current status; otherwise null, do not infer",
  "market_impact": "string or null — only fill if a candidate explicitly discusses market/investor/stakeholder impact; otherwise null",
  "confidence": {"level": "High | Medium | Low", "reason": "string"},
  "sources_used": ["array of the exact URLs, from the candidate list, that you actually drew information from — never include a URL you did not use"]
}

Rules:
- Language: every human-readable string must be in Bahasa Indonesia. Keep confidence.level as exactly "High", "Medium", or "Low".
- DATE ANCHORING (critical): a candidate only counts as an update if its published date is genuinely after the baseline's publication date. If a candidate's date is missing or unparseable, you may still use it but note the reduced certainty in confidence.reason. If a candidate's date is on or before the baseline date, discard it entirely.
- Never repeat or re-explain baseline facts.
- Never speculate or infer beyond what a candidate's snippet explicitly states. You only have short snippets, not full articles — if a snippet is too thin to state a concrete development, leave it out rather than guess.
- SOURCE PRIORITY: prefer local Indonesian outlets with direct coverage (Kompas, Bisnis.com, Detik, Tempo, Antara) as primary evidence for Indonesia-specific events. Treat international wire coverage (Reuters, Bloomberg, CNBC, AP, BBC) as corroboration or global-impact context, not an automatic override of more specific local reporting.
- Merge duplicate events reported by multiple candidates into one entry in "developments" — but list every source you drew from in "sources_used". If multiple candidates you drew from are from the SAME website/domain, only include ONE of them in "sources_used" (the most complete one) — do not list the same outlet twice just because it published more than one article.
- NARRATIVE STYLE: "overview" must read as ONE cohesive paragraph (2-5 sentences), never a string of disconnected facts. When two facts from different candidates are causally or temporally related, connect them explicitly (e.g. "harga minyak terkoreksi turun, sejalan dengan itu indeks berbalik menguat"). When a key figure in the paragraph comes from only a single candidate, name that outlet inline in the sentence itself (e.g. "data resmi X yang dikutip Y menunjukkan...") so the reader can judge reliability without opening the source link.
- CONFIDENCE RUBRIC (apply exactly, do not default to Low just because there is only one source):
  - High: sources_used contains candidates from at least 2 DIFFERENT websites/domains corroborating the same core fact. Two articles from the same outlet (e.g. two different Detik.com URLs) count as ONE source, not two — "independent" means a different publisher, never just a different URL.
  - Medium: only 1 unique domain in sources_used, but that domain cites primary/official data (a stock exchange, a government body, an official company filing or report) rather than opinion or an analyst projection.
  - Low: only 1 unique domain with no primary data behind it (opinion, analyst projection, outlook/forecast piece), OR the candidate's published date is missing/unparseable so its post-baseline status cannot be confirmed.
  - confidence.reason must explicitly state: how many unique domains were used, and whether the evidence is primary/official or a projection/opinion.
- If no candidate contains a genuine post-baseline development, set has_update to false, overview to an empty string, developments to an empty array, and explain in confidence.reason that no significant change was found.
- Every fact in "overview", "developments", "current_situation", and "market_impact" must be traceable to at least one URL in "sources_used"."""


def _cluster_by_date(candidates: list[dict[str, Any]]) -> str:
    """Group candidates by publish date (day granularity) and format them
    for the prompt as dated clusters instead of a flat list — helps the
    model build a coherent timeline instead of picking arbitrary items,
    especially now that multi-query pooling can return more candidates
    than the old single-query version did."""
    from collections import OrderedDict
    buckets: "OrderedDict[str, list]" = OrderedDict()
    for c in candidates:
        d = (c.get("date") or "")[:10] or "Tanggal tidak diketahui"
        buckets.setdefault(d, []).append(c)

    parts = []
    for date, items in buckets.items():
        parts.append(f"=== {date} ===")
        for it in items:
            parts.append(json.dumps(it, ensure_ascii=False))
    return "\n".join(parts)


def _compute_confidence(sources_used: list[dict[str, Any]],
                         timeline: list[dict[str, Any]],
                         candidates: list[dict[str, Any]]) -> dict[str, str]:
    """Deterministic 3-factor confidence score, computed in code rather than
    trusted from the model's own self-assessment (same reasoning as the
    domain-count check it replaces): distinct domains, corroboration ratio
    across timeline events, and recency of the newest candidate used."""
    from email.utils import parsedate_to_datetime

    def _parsed(dstr: Optional[str]):
        if not dstr:
            return None
        try:
            dt = parsedate_to_datetime(dstr)
        except Exception:
            try:
                dt = datetime.fromisoformat(dstr.replace("Z", "+00:00"))
            except Exception:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    domains = {s.get("website", "") for s in sources_used if s.get("website")}
    distinct_domains = len(domains)

    corroborated = 0
    for ev in timeline:
        ev_domains = {
            urlparse(u).netloc.replace("www.", "")
            for u in (ev.get("source_urls") or [])
        }
        if len(ev_domains) >= 2:
            corroborated += 1
    corroboration_ratio = corroborated / len(timeline) if timeline else 0.0

    most_recent = None
    for c in candidates:
        dt = _parsed(c.get("date"))
        if dt and (most_recent is None or dt > most_recent):
            most_recent = dt
    recent_48h = bool(
        most_recent and (datetime.now(timezone.utc) - most_recent) <= timedelta(hours=48)
    )

    score = int(distinct_domains >= 4) + int(corroboration_ratio >= 0.5) + int(recent_48h)
    level = "High" if score >= 2 else "Medium" if score == 1 else "Low"

    reason = (
        f"{distinct_domains} domain sumber independen; "
        + (f"{corroborated}/{len(timeline)} event timeline terkonfirmasi ≥2 sumber"
           if timeline else "tidak ada event timeline")
        + ("; berita dalam 48 jam terakhir." if recent_48h
           else "; berita di luar 48 jam terakhir.")
    )
    return {"level": level, "reason": reason}


def _merge_timeline(existing: list[dict[str, Any]],
                     new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accumulate timeline history across refreshes instead of overwriting
    it. An event found in a PREVIOUS refresh that doesn't resurface in this
    refresh's (bounded) search window must still be remembered — dropping
    it just because this week's Tavily window didn't happen to re-surface
    it would silently erase real history.

    Two events count as the same event if they share a date AND at least
    half their words overlap (Jaccard similarity) — an exact-string match
    would miss the common case where two sources phrase the same event
    differently (e.g. "B50 masih tahap persiapan" vs "...menurut Kompas,
    B50 masih tahap persiapan")."""
    def _words(text: str) -> set[str]:
        return set(re.findall(r"\w+", (text or "").lower()))

    def _same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
        if (a.get("date") or "")[:10] != (b.get("date") or "")[:10]:
            return False
        wa, wb = _words(a.get("event")), _words(b.get("event"))
        if not wa or not wb:
            return False
        return len(wa & wb) / len(wa | wb) >= 0.5

    merged: list[dict[str, Any]] = [dict(e) for e in existing]
    for ev in new:
        match = next((m for m in merged if _same_event(m, ev)), None)
        if match is None:
            merged.append(dict(ev))
            continue
        match["source_urls"] = sorted(
            set(match.get("source_urls") or []) | set(ev.get("source_urls") or [])
        )
        if len(ev.get("event") or "") > len(match.get("event") or ""):
            match["event"] = ev["event"]  # keep the more detailed wording

    merged.sort(key=lambda e: (e.get("date") or ""), reverse=True)
    return merged


async def synthesize_delta(baseline: dict[str, Any],
                            candidates: list[dict[str, Any]],
                            existing_timeline: Optional[list[dict[str, Any]]] = None
                           ) -> dict[str, Any]:
    """Call Claude to turn raw Tavily candidates into a genuine delta versus
    the baseline article. Returns the parsed JSON dict."""
    key = os.environ["EMERGENT_LLM_KEY"]
    tag = hashlib.sha256(
        (baseline.get("source_url", "") + str(len(candidates))).encode()
    ).hexdigest()[:12]

    chat = LlmChat(
        api_key=key,
        session_id=f"delta-{tag}",
        system_message=_DELTA_SYSTEM_PROMPT,
    ).with_model("anthropic", "claude-sonnet-4-5-20250929").with_params(
        max_tokens=2048,
    )

    prompt = (
        f"BASELINE:\n{json.dumps(baseline, ensure_ascii=False)}\n\n"
        f"CANDIDATE ARTICLES (grouped by publish date):\n{_cluster_by_date(candidates)}"
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

    # Enrich sources_used from bare URLs into structured citation objects
    # (title/website/date/url) matching the shape of the article's own
    # "references" field, so the frontend can merge them into one list.
    # Built from `candidates` (which we already fetched and trust) rather
    # than asking the model to echo metadata — avoids relying on the LLM
    # to reproduce titles/dates accurately, and silently drops any URL
    # the model cited that isn't actually in the candidate list.
    candidates_by_url = {c.get("url"): c for c in candidates if c.get("url")}
    data["sources_used"] = [
        {
            "url": url,
            "title": candidates_by_url[url].get("title", ""),
            "website": candidates_by_url[url].get("source", ""),
            "date": candidates_by_url[url].get("date"),
        }
        for url in data.get("sources_used", [])
        if url in candidates_by_url
    ]

    # De-dupe sources_used by website: 3 different Detik.com URLs are one
    # outlet, not three sources. Keep the first (most-cited-first) entry
    # per unique domain rather than showing repeat rows for the same outlet.
    seen_websites: set[str] = set()
    deduped_sources = []
    for src in data["sources_used"]:
        w = src.get("website") or src.get("url", "")
        if w in seen_websites:
            continue
        seen_websites.add(w)
        deduped_sources.append(src)
    data["sources_used"] = deduped_sources

    # Ensure every timeline item has a source_urls list — the model may
    # omit it even though the schema asks for it.
    for ev in data.get("timeline", []):
        if isinstance(ev, dict):
            ev.setdefault("source_urls", [])

    # Deterministic 3-factor confidence, computed from THIS refresh's own
    # candidates/sources/timeline (not the accumulated history) — fully
    # replaces the model's self-rating, same reasoning as the domain-count
    # check this supersedes: don't trust the model's own tally of
    # "independent sources", recompute it in code.
    data["confidence"] = _compute_confidence(
        data["sources_used"], data.get("timeline", []), candidates,
    )
    conf = data["confidence"]

    if conf["level"] == "Low":
        # This refresh's own findings are too thin to trust — don't fold
        # them into the permanent accumulated timeline, and don't present
        # them as "what's new" either. The frontend already renders "No
        # newer public updates..." whenever has_update is false. But if
        # there's ALREADY accumulated history from an earlier, stronger
        # refresh, that history is preserved and still shown — a weak
        # refresh today shouldn't erase validated history from before.
        data["has_update"] = bool(existing_timeline)
        data["overview"] = ""
        data["developments"] = []
        data["current_situation"] = None
        data["market_impact"] = None
        data["sources_used"] = []
        data["timeline"] = existing_timeline or []
    else:
        data["timeline"] = _merge_timeline(existing_timeline or [], data["timeline"])

    return data

def _compute_search_days(baseline_date: Optional[str]) -> int:
    """How far back Tavily should search. Floor of 4 days for a fresh
    baseline (matches the original fixed window). If the baseline article
    is older than that, extend the window to cover the full gap since it
    was published — otherwise a genuine update published, say, 6 days ago
    against a 7-day-old baseline would fall outside a fixed 4-day window
    and get silently missed. Capped at 30 days so a very old baseline
    doesn't pull in irrelevantly old "updates"."""
    if not baseline_date:
        return 4
    from email.utils import parsedate_to_datetime
    try:
        try:
            pub = parsedate_to_datetime(baseline_date)
        except (TypeError, ValueError):
            pub = datetime.fromisoformat(baseline_date.replace("Z", "+00:00"))
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        gap_days = (datetime.now(timezone.utc) - pub).days
        return max(4, min(gap_days + 1, 30))
    except Exception:
        return 4


def _build_queries(entities: list[str], baseline_date: Optional[str] = None) -> list[str]:
    """Build up to 3 search query variants from the baseline's entities —
    NOT the 8 in the original pipeline sketch. Each query is a separate
    Tavily search call (1 credit each on the free/basic tier), so this is
    a deliberate cost/recall tradeoff: 3 gives real angle diversity
    without multiplying the Tavily bill 8x per check."""
    entities = [e for e in entities if e]
    if not entities:
        return []
    lead = entities[0]
    queries = [" ".join(entities[:3]), f"{lead} terbaru"]
    queries.append(f"{lead} setelah {baseline_date[:10]}" if baseline_date
                    else f"{lead} hari ini")
    seen: set[str] = set()
    out = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out[:3]


def _fetch_full_text(url: str, max_chars: int = 2000) -> Optional[str]:
    """Best-effort full-page fetch for a Latest Updates candidate, to enrich
    beyond Tavily's ~400-char snippet. Returns None on ANY failure — never
    raises, so a blocked/slow site just falls back to the Tavily snippet
    instead of failing the whole Latest Updates check. Timeout kept short
    (8s) because several of these run concurrently per check."""
    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=False)
        if not downloaded:
            import requests
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            }
            resp = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
            if resp.status_code == 200 and resp.text:
                downloaded = resp.text
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded, include_comments=False,
                                    include_tables=False, favor_precision=True)
        if not text or len(text.strip()) < 100:
            return None
        return text.strip()[:max_chars]
    except Exception as e:
        logger.info(f"[latest-updates] full-fetch failed for {url}: {e}")
        return None


async def fetch_latest_updates(topic: str, exclude_url: Optional[str] = None,
                          entities: Optional[list[str]] = None,
                          baseline_date: Optional[str] = None
                         ) -> list[dict[str, Any]]:
    """Query Tavily for the latest news on the topic. Returns list, newest first.

    If `entities` is provided, up to 3 query variants are built from them
    (see _build_queries) instead of one generic query. Results across all
    queries are pooled and de-duped by URL. The top candidates then get a
    best-effort full-page fetch (see _fetch_full_text) instead of relying
    solely on Tavily's short snippet.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key or api_key.startswith("REPLACE"):
        raise RuntimeError("Live search is not configured. Set TAVILY_API_KEY.")

    entities = [e.strip() for e in (entities or []) if e and e.strip()]
    search_days = _compute_search_days(baseline_date)
    queries = _build_queries(entities, baseline_date) or [topic]

    client = TavilyClient(api_key=api_key)
    seen_urls: set[str] = set()
    raw_results: list[dict[str, Any]] = []
    for q in queries:
        try:
            resp = client.search(
                query=q,
                topic="news",
                days=search_days,
                max_results=10,
                include_domains=_TRUSTED_DOMAINS,
            )
        except Exception as e:  # tavily raises various exceptions
            logger.exception(f"Tavily error for query '{q}'")
            continue  # one bad query in the batch shouldn't kill the whole check
        for r in (resp.get("results", []) or []):
            url = (r.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            if exclude_url and normalize_url(url) == exclude_url:
                continue
            seen_urls.add(url)
            raw_results.append(r)

    logger.info(f"[DEBUG] queries={queries} entities={entities} "
                f"pooled_raw_count={len(raw_results)}")

    updates: list[dict[str, Any]] = []
    for r in raw_results:
        url = r["url"]
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

    # Full-page fetch for the top N candidates only — bounds latency/cost;
    # fetching all 20-30 pooled results concurrently would make one check
    # take far too long and hammer sites we're not even going to use.
    FULL_FETCH_CAP = 8
    to_fetch = updates[:FULL_FETCH_CAP]
    if to_fetch:
        fetched_texts = await asyncio.gather(
            *[asyncio.to_thread(_fetch_full_text, c["url"]) for c in to_fetch]
        )
        for c, full_text in zip(to_fetch, fetched_texts):
            if full_text:
                c["summary"] = full_text
                c["full_text"] = True
            else:
                c["full_text"] = False

    logger.info(f"[DEBUG] after_dedup_count={len(updates)} "
                f"full_fetched={sum(1 for c in to_fetch if c.get('full_text'))}")
    return updates


# --- MongoDB cache + rate limit -------------------------------------------

class CacheAndRateLimit:
    """Small helper that owns both the cached-summary store and the per-IP
    rate limit counters. Both live in MongoDB."""

    def __init__(self, db):
        self.db = db
        self.cache = db.summary_cache
        self.rate = db.rate_limit
        self.shares = db.shared_articles
        self.ttl_hours = int(os.environ.get("CACHE_TTL_HOURS", "12"))

    async def ensure_indexes(self) -> None:
        await self.cache.create_index("hash", unique=True)
        await self.cache.create_index("created_at")
        await self.rate.create_index([("ip", 1), ("bucket", 1), ("ts", 1)])
        await self.shares.create_index("slug", unique=True)

    async def create_share(self, summary: dict[str, Any]) -> str:
        """Store a point-in-time snapshot of a summary under a short slug.
        No TTL — a link someone shared should still work weeks later, even
        after the 12h working cache this summary came from has expired."""
        import secrets
        for _ in range(5):
            slug = secrets.token_urlsafe(6).replace("_", "").replace("-", "")[:8]
            if not slug:
                continue
            try:
                await self.shares.insert_one({
                    "slug": slug,
                    "summary": summary,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                return slug
            except Exception:
                continue  # slug collision (very rare) — try another
        raise RuntimeError("Could not generate a unique share link. Try again.")

    async def get_share(self, slug: str) -> Optional[dict[str, Any]]:
        doc = await self.shares.find_one({"slug": slug}, {"_id": 0})
        return doc

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

    async def get_accumulated_timeline(self, h: str) -> list[dict[str, Any]]:
        """The permanently-accumulated timeline for this article, built up
        across every refresh via _merge_timeline. Deliberately NOT subject
        to the 12h delta freshness window — summary_cache has no Mongo TTL
        index (confirmed: only 'hash' and 'created_at' indexes exist), so
        this field persists regardless of how stale the rest of the cache
        entry is considered by the app."""
        doc = await self.cache.find_one({"hash": h}, {"_id": 0, "accumulated_timeline": 1})
        return (doc or {}).get("accumulated_timeline") or []

    async def set_accumulated_timeline(self, h: str, timeline: list[dict[str, Any]]) -> None:
        await self.cache.update_one(
            {"hash": h},
            {"$set": {"accumulated_timeline": timeline}},
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
