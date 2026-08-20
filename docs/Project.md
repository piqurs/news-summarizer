# Project.md — News Summarizer

> Master project brief. If it is not in this file, it is not in scope. Anything
> in the backlog that has not been explicitly promoted to "In scope" must not
> be built.

---

## 1. Elevator pitch

A single-page web application that turns any public news article URL into a
structured, easy-to-read analytical summary in Bahasa Indonesia, with an
optional on-demand "Latest Updates" pipeline that surfaces what has happened
since the article was published. Cached and rate-limited so a portfolio-scale
Emergent LLM Key budget survives real traffic.

## 2. Goals

- **G1 — Reader value**: 9 structured sections generated from one URL in
  under 45 seconds (cold path). Cache hits under 1 second.
- **G2 — Analyst value**: root-cause, 5W1H, sentiment & bias, dated Latest
  Updates timeline with per-event source attribution.
- **G3 — Portfolio quality**: Linear/Vercel/Stripe-tier polish — light+dark
  theme, staggered motion, responsive to mobile, keyboard-friendly.
- **G4 — Cost-safe by default**: every AI call is guarded by (a) URL-hash
  cache, (b) per-IP sliding-hour rate limit, and (c) explicit user intent
  (Latest Updates is behind a button, translations are on-demand).

## 3. Non-goals (v1)

- No user accounts, no persistent per-user history.
- No paid tier, no Stripe.
- No mobile-native app; responsive web only.
- No AI-generated images (article-only output).
- No live streaming of tokens (planned for v2, not v1).

## 4. In-scope MVP feature set

| ID | Feature                          | State       |
| -- | -------------------------------- | ----------- |
| F1 | Paste URL → structured summary   | shipped     |
| F2 | 9 canonical result sections      | shipped     |
| F3 | Bahasa Indonesia default output  | shipped     |
| F4 | EN ↔ ID toggle (per-summary)     | shipped     |
| F5 | Latest Updates (Tavily + Claude) | shipped     |
| F6 | Recent News feed (last 12 h)     | shipped     |
| F7 | Copy / PDF / DOCX / Share export | shipped     |
| F8 | Per-IP sliding-hour rate limits  | shipped     |
| F9 | Light / dark theme               | shipped     |

### 4.1 The 9 result sections (fixed order — do not re-order)

1. Executive Summary
2. Key Points
3. Main Issue
4. Root Cause
5. Sentiment & Bias Analysis
6. Recommended Actions
7. Latest Updates (deferred, user-triggered)
8. 5W1H
9. References
10. Confidence

## 5. Users

- **Reader** — pastes a link, wants the gist in seconds.
- **Analyst** — needs root cause + 5W1H + references for triage.
- **Portfolio visitor** — evaluates engineering polish.

## 6. Technical requirements

- **Frontend**: React 19, Tailwind, framer-motion, lucide-react, sonner
  toasts, jsPDF + docx + file-saver for client-side exports. All API calls
  through `process.env.REACT_APP_BACKEND_URL`; no localhost, no hardcoded
  preview URLs.
- **Backend**: FastAPI (Python 3.11+), Motor async MongoDB driver,
  trafilatura for article extraction, tavily-python for live search,
  emergentintegrations for Claude Sonnet 4.5. Every route prefixed `/api`.
- **Database**: MongoDB — collections `summary_cache`, `rate_limit`,
  `timeline_store` (see Schema.md).
- **LLM**: Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`) via
  `EMERGENT_LLM_KEY`. No other LLM providers wired.
- **Search**: Tavily API. Restricted to a trusted-domains list + a hard
  12-month window.
- **Rate limits** (per-IP sliding hour, all configurable via `.env`):
  - `RATE_LIMIT_SUMMARY=5`
  - `RATE_LIMIT_UPDATES=5`
  - `RATE_LIMIT_TRANSLATE=10`
- **Cache TTLs**:
  - Summary payload — `CACHE_TTL_HOURS=12` (default 12 h).
  - Latest-Updates delta (has_update: true) — `CACHE_TTL_DELTA_MINUTES=5`
    or falls back to `CACHE_TTL_HOURS`.
  - Latest-Updates delta (has_update: false) —
    `LATEST_UPDATES_EMPTY_TTL_MINUTES=30` — shorter so a user can retry
    quickly without being stuck on a stale empty result.
  - Accumulated timeline — no TTL, only grows.
- **Search window**: `LATEST_UPDATES_WINDOW_DAYS=365` — hard 12-month
  cap from *today*, never based on the baseline article age.

## 7. Success metrics

| Metric                                     | Target                |
| ------------------------------------------ | --------------------- |
| Cold summarize latency (p95)               | ≤ 45 s                |
| Cached summarize latency (p95)             | ≤ 1 s                 |
| Latest Updates pipeline latency (p95)      | ≤ 60 s                |
| Rate-limit false positives                 | 0                     |
| "Unknown date" timeline entries surfaced   | 0                     |
| Empty Latest Updates response with cached  | 0 self-contradictions |
| accumulated timeline still shown           |                       |
| Backend regression tests passing           | 100 %                 |
| Deployment readiness check                 | pass                  |

## 8. Constraints & rules that ship with v1

- Cache hits **never** charge the rate limit budget.
- URL must be normalized before hashing (see `services.normalize_url`).
- Latest Updates timeline **only ever grows** — `merge_timeline` unions
  new events with the persisted `timeline_store`, deduping by same-day
  Jaccard ≥ 0.5.
- Any candidate with a missing / unparseable date is **dropped before the
  LLM ever sees it**. Any LLM timeline event without a parseable date is
  dropped in synthesis. "Unknown Date" must never render.
- The response for `/api/latest-updates` must be internally consistent:
  if the merged timeline contains post-baseline dated events, `has_update`
  MUST be true.

## 9. Backlog (not in scope — do not build without promotion)

### P1
- Streaming SSE for perceived latency on first cold summarize.
- Server-side sitemap + Open Graph refinement for portfolio SEO.
- Playwright end-to-end tests covering the full flow.

### P2 (v2)
- Google or Emergent auth + saved summaries per user.
- Side-by-side article comparison.
- Multi-model selection (Opus, Haiku, Sonnet 4.6, Sonnet 5) in the UI.
- Persistent dashboard with saved history.

## 10. Where things live

- `/app/backend/server.py`  — HTTP surface (all `/api/...` routes)
- `/app/backend/services.py`— all business logic (extract, summarize,
                              latest-updates pipeline, cache + rate limit)
- `/app/backend/tests/`     — pytest regression suite (must stay green)
- `/app/frontend/src/pages/Home.jsx`             — single page shell
- `/app/frontend/src/components/*.jsx`           — feature components
- `/app/frontend/src/lib/api.js`                 — axios client
- `/app/frontend/src/lib/exporters.js`           — client-side PDF/DOCX
- `/app/memory/PRD.md`                           — historical decision log
- `/app/docs/*.md`                               — Project / Architecture /
                                                    Design / Schema / Rules /
                                                    Prompt (this pack)
