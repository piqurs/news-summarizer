# News Summarizer — PRD

## Problem Statement
Production-ready AI-powered News Summarizer as a modern SPA for a software
engineering portfolio. One responsive page (Nav, Hero, Result, About, Footer)
that transforms news article URLs into structured, easy-to-understand insights.
Clean, premium SaaS experience inspired by Linear/Vercel/Stripe/Notion.

## Architecture

### Stack
- **Frontend**: React 19, Tailwind, framer-motion, lucide-react, sonner,
  jsPDF + docx + file-saver for exports.
- **Backend**: FastAPI + Motor (MongoDB), trafilatura for article extraction,
  emergentintegrations for Claude Sonnet 4.5, tavily-python for live search.
- **Database**: MongoDB collections `summary_cache` and `rate_limit`.

### Integrations
- **Claude Sonnet 4.5** (`claude-sonnet-4-5-20250929`) via `EMERGENT_LLM_KEY` —
  structured JSON summary in one call.
- **Tavily Search API** — on-demand "Latest Updates" via `TAVILY_API_KEY`,
  restricted to trusted domains + social platforms, newest first.

### Key Design Decisions
- Cache hits **do not** consume the rate-limit budget.
- Per-IP sliding-hour rate limit (5/h) on both summarize & latest-updates.
- URL normalized before hashing (tracking params stripped, lowercase host).
- Latest Updates deferred behind explicit button to avoid AI cost on load.
- Client-side PDF/DOCX generation — no server storage of exports.

## User Personas
- **The reader**: pastes a link, wants a structured summary in seconds.
- **The analyst**: needs root cause + 5W1H + references for triage.
- **The portfolio visitor**: evaluates engineering quality — expects a polished
  Linear-style SaaS feel with light/dark toggle.

## Core Requirements (Static)
1. One SPA — Nav / Hero / Result / About / Footer.
2. URL → structured summary (9 sections).
3. On-demand Latest Updates via live search.
4. Export: Copy, PDF, DOCX, native Share.
5. Rate-limited & cached to control AI cost.
6. Both light & dark theme.
7. Responsive down to mobile.

## Implemented (2026-02)
- FastAPI backend with `/api/health`, `/api/summarize`, `/api/latest-updates`,
  `/api/translate`, `/api/recent`, `/api/rate-status`.
- Claude Sonnet 4.5 structured JSON generation with fallback UA article fetch.
- **Bahasa Indonesia is the default output language** — Claude summarises
  in Indonesian regardless of source article language. `confidence_level.level`
  and `sentiment_and_bias.tone` are enforced as literal English strings so
  the UI badges continue to map correctly.
- **Sentiment & Bias Analysis (section #5)** — tone
  (Positive/Neutral/Negative/Mixed) + one-sentence explanation + observable
  bias indicators grounded in the article text. Empty indicators when the
  article is balanced (no manufactured claims). Labelled as AI-generated,
  not a factual claim about the publisher.
- **References sanitiser** — server-side drops any entry without a real
  `http(s)://` URL before returning to the client. Never render a bare
  reference card with no link.
- **EN ↔ ID language toggle** on the result card. Only the currently-displayed
  language is fetched; toggling back and forth after the first translation
  makes zero network calls.
- **Recent News (Last 12h)** — public feed of the last 6 cached summaries
  sitting above the About section. Click a card to load the cached analysis
  into the main result card without any AI call or rate-limit charge.
- Cache schema per URL hash stores `payload` (Indonesian) plus
  `translations.<lang>` under a single document that shares the 12 h TTL;
  cache hits are free. Expired rows drop out of `/api/recent` naturally.
- Tavily-powered Latest Updates section (deferred, chronological, deduped).
- Per-IP sliding-hour rate limits (Mongo-backed): 5/h summarize, 5/h updates,
  5/h translate.
- Full React SPA: Cabinet Grotesk headings + IBM Plex Sans body, Linear-style
  mono palette with subtle blue accent, staggered framer-motion entrance,
  animated loading state with rotating status messages.
- ExportShare (Copy, PDF, DOCX, Web Share fallback → clipboard) — always
  exports the currently-selected language.
- Confidence badge, AI-generated labels on Recommended Actions and Sentiment
  & Bias, dashed borders + monospace meta for engineered feel.
- Bento About section with 13 feature cards (roadmap removed 2026-02).
- Footer with GitHub / LinkedIn / Instagram links.

## Result-card section order (11 sections, as rendered Jun 2026)
1 Executive Summary · 2 Key Points · 3 Main Issue · 4 Root Cause · 5 5W1H ·
6 Sentiment & Bias · 7 Confidence · 8 Recommended Actions ·
9 Impact Analysis (hidden entirely when items empty) · 10 Latest Updates ·
11 References

## Implemented (2026-06) — Impact Analysis section
- New `impact_analysis` in summarize schema: `items[{aspect, direction, certainty, description}]` + mandatory disclaimer.
- `direction` (Positive|Negative|Mixed|Unclear) and `certainty` (Stated|Inferred) are English literals — untouched by EN/ID translation (enforced in prompt + code, like tone/confidence.level).
- Defensive defaults in `services.summarize_article`: invalid direction→Unclear, invalid certainty→Inferred (cautious), items without description dropped.
- Category-adaptive prompt rules (finance aspects for finance news, domain-relevant for others), no-advice guardrails, "Stated" only when article explicitly states it.
- Empty items ⇒ entire section hidden in UI (`ImpactAnalysis.jsx` returns null + parent conditional), PDF, Word, and plain-text copy/share (guarded in `exporters.js`).
- Verified: financial (kompas rupiah) + sports (BBC La Liga) articles via curl, EN translation enum preservation, UI screenshot.

## Backlog (Deferred)
### P1
- **Streaming responses** for perceived latency win on first render.
- **Server-side sitemap + Open Graph** meta refinement for portfolio SEO.
- **E2E tests** with Playwright covering the full flow.

### P2 (Version 2)
- Auth (Google or Emergent) + saved summaries per user.
- Sentiment / bias analysis pass.
- Article comparison (side-by-side).
- Multilingual output.
- Persistent history + a dashboard.

## Env vars (backend/.env)
- `MONGO_URL`, `DB_NAME`, `CORS_ORIGINS`
- `EMERGENT_LLM_KEY`
- `TAVILY_API_KEY`
- `RATE_LIMIT_SUMMARY` (default 5)
- `RATE_LIMIT_UPDATES` (default 5)
- `RATE_LIMIT_TRANSLATE` (default 5)
- `CACHE_TTL_HOURS` (default 24)

## Latest Updates pipeline v3 (Feb 2026 — user-driven fixes)
Two hard product rules enforced end-to-end plus a consistency fix:
1. **12-month window** — `_search_window_days()` returns int(`LATEST_UPDATES_WINDOW_DAYS`) with a default of 365. Applied both as `day=` param to Tavily AND as a `parsed_pub >= now-365d` filter in `search_candidates()`. Never depends on baseline article age.
2. **No "Unknown date"** — any candidate with missing/unparseable date is dropped in `search_candidates()`; any LLM timeline event without a parseable date is dropped in `synthesize_delta`; `merge_timeline()` also strips legacy undated rows (including from stored `timeline_store`); `get_timeline()` sanitises on read.
3. **Ticker disambiguation** — `_looks_like_ticker`/`_disambiguate` pair short all-caps entities (e.g. ANTM, BBRI) with a longer companion entity or topic word before sending as a query. Prevents ANTM colliding with "America's Next Top Model" in Tavily results.
4. **Rolling-market prompt rule** — synthesize_delta system prompt now explicitly instructs the model to treat later dated data points on continuous stories (indices, prices, rates, policy timelines) as GENUINE updates, and to silently ignore off-topic candidates from broad entity searches.
5. **Empty-cache TTL** — `has_update: false` results get a separate shorter TTL from `LATEST_UPDATES_EMPTY_TTL_MINUTES` (default 30) so users can retry without being stuck.
6. **Consistency fix** — `latest_updates` now merges timeline BEFORE calling `compute_confidence`, and if the merged accumulated timeline contains any post-baseline event but the fresh LLM run was empty, forces `has_update=true` + populates a Bahasa Indonesia overview + appends `peristiwa akumulatif pasca-baseline` context to `confidence.reason`. Prevents the previous self-contradictory `has_update:false` + non-empty timeline response.

Backend testing agent iteration_5.json: 11/11 tests pass, all four user-reported URLs green (kompas B50, IHSG, Rupiah, Emas Antam). Frontend NOT modified per user directive.
Env (backend/.env): `LATEST_UPDATES_WINDOW_DAYS=365`, `LATEST_UPDATES_EMPTY_TTL_MINUTES=30` (new — both configurable at runtime).

## Latest Updates pipeline v2 (Aug 2026 session — per "Latest Update Pipeline Design.txt")
Implemented in backend/services.py + server.py; frontend contract unchanged.
1. build_queries(): up to LATEST_UPDATES_QUERY_COUNT=8 variants from stored key_entities/topic/baseline date (lower constant to 3 if Tavily quota exhausted).
2. search_candidates(): ALL queries concurrent via asyncio.gather + asyncio.to_thread (2-6s for 8; sequential was 20+s). Dedupe URL + title Jaccard 0.8, newest-first, cap 10.
3. web_fetch_candidates(): top 8 pages fetched concurrently (requests 8s timeout + trafilatura); MANDATORY snippet fallback; content_kind full_text|snippet.
4. cluster_by_date(): candidates grouped by publish day before synthesis.
5. synthesize_delta(baseline, clusters): timeline events carry per-event sources; all URLs validated against candidate pool.
6. compute_confidence(): deterministic in code — +1 if >=4 distinct used domains, +1 if >=50% timeline events corroborated by >=2 domains, +1 if newest used candidate <48h; score>=2 High / 1 Medium / 0 Low. Overrides LLM self-claim.
7. merge_timeline(): accumulated timeline persisted in NEW `timeline_store` collection (no TTL, separate from 12h delta cache); same_event = same-day + Jaccard>=0.5 (never exact match); timeline only grows. key_entities now stored in summary payload (no extra LLM entity call).

Verified by backend testing agent 9/9: concurrency in logs, timeline 8→11 never shrank, deterministic confidence, 429 on 6th call. Fresh run ~32s (Claude generation ~24s dominates; search+fetch ~8s).
Env: .env files gitignored — Tavily key from user; emergentintegrations via --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/. Preview: https://3d2fce82-df06-4551-a317-10b3d3af21c4.preview.emergentagent.com. Stale pytest file backend/tests/test_news_summarizer_api.py expects obsolete shape.
