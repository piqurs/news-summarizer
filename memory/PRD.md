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

## Result-card section order (10 sections)
1 Executive Summary · 2 Key Points · 3 Main Issue · 4 Root Cause ·
5 Sentiment & Bias Analysis · 6 Recommended Actions · 7 Latest Updates ·
8 5W1H · 9 References · 10 Confidence

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
