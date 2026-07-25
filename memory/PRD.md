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
  `/api/translate`.
- Claude Sonnet 4.5 structured JSON generation with fallback UA article fetch.
- **Bahasa Indonesia is now the default output language** — Claude summarises
  in Indonesian regardless of source article language. `confidence_level.level`
  is enforced as the literal English string ("High"/"Medium"/"Low") because
  the UI badge depends on it.
- **EN ↔ ID language toggle** on the result card near the export/share row.
  Only the currently-displayed language is fetched; toggling back and forth
  never re-triggers the AI once each version has been cached.
- Cache schema per URL hash now stores `payload` (Indonesian) plus
  `translations.<lang>` (e.g. `translations.en`) on the same document, sharing
  the 24 h TTL. Cache hits do not consume any rate-limit budget.
- Tavily-powered Latest Updates section (deferred, chronological, deduped).
- URL normalization + SHA256-hashed 24h Mongo cache.
- Per-IP sliding-hour rate limits (Mongo-backed): 5/h summarize, 5/h updates,
  5/h translate. Cache hits are free.
- Full React SPA: Cabinet Grotesk headings + IBM Plex Sans body, Linear-style
  mono palette with subtle blue accent, staggered framer-motion entrance,
  animated loading state with rotating status messages.
- ExportShare group (Copy, PDF, DOCX, Web Share fallback → clipboard). All
  exports honour the currently-selected language.
- Confidence badge (High/Medium/Low), "AI-generated" label on
  Recommended Actions, dashed borders + monospace meta for engineered feel.
- Bento About section + 2-tile roadmap.
- Footer with GitHub / LinkedIn / Instagram links.

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
