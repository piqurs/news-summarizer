# Architecture.md — News Summarizer

> How the system is put together and *why*. Every "why" here is a decision
> already made — do not re-open without a documented reason.

---

## 1. Tech stack (locked)

| Layer          | Choice                                              |
| -------------- | --------------------------------------------------- |
| Frontend       | React 19 (Create React App + craco)                 |
| Styling        | Tailwind 3.4 + tailwind-merge + tailwindcss-animate |
| UI primitives  | shadcn/ui (Radix) — imported from `components/ui/`  |
| Icons          | lucide-react                                        |
| Motion         | framer-motion 11                                    |
| Toasts         | sonner                                              |
| Client exports | jsPDF, docx, file-saver                             |
| Data fetching  | axios (via `lib/api.js`)                            |
| Router         | react-router-dom 7 (single route today)             |
| Backend        | FastAPI 0.110 + Uvicorn 0.25                        |
| Article parser | trafilatura 2.1                                     |
| LLM SDK        | emergentintegrations (Claude Sonnet 4.5)            |
| Search         | tavily-python 0.7                                   |
| DB driver      | motor 3.3 (async MongoDB)                           |
| DB             | MongoDB (Emergent-managed)                          |
| Process mgr    | supervisor (backend + frontend)                     |

## 2. Folder structure

```
/app
├── backend/
│   ├── server.py           # HTTP surface only — no business logic
│   ├── services.py         # All business logic in one module
│   ├── requirements.txt    # Managed via pip freeze — never hand-edited
│   ├── .env                # Runtime config (see §6)
│   └── tests/              # pytest — regression must stay green
│       └── test_latest_updates.py
├── frontend/
│   ├── src/
│   │   ├── index.js
│   │   ├── App.js
│   │   ├── pages/
│   │   │   └── Home.jsx             # Single page shell
│   │   ├── components/
│   │   │   ├── Navbar.jsx
│   │   │   ├── Hero.jsx             # URL input + submit
│   │   │   ├── ResultCard.jsx       # 9-section renderer
│   │   │   ├── LatestUpdates.jsx    # Deferred, user-triggered
│   │   │   ├── LanguageToggle.jsx   # EN ↔ ID
│   │   │   ├── ExportShare.jsx      # Copy / PDF / DOCX / Share
│   │   │   ├── RecentNews.jsx       # Public feed of last 12 h
│   │   │   ├── ConfidenceBadge.jsx
│   │   │   ├── SentimentBias.jsx
│   │   │   ├── LoadingState.jsx
│   │   │   ├── ThemeToggle.jsx
│   │   │   ├── Footer.jsx
│   │   │   ├── About.jsx
│   │   │   └── ui/                  # shadcn/ui primitives — do not edit
│   │   ├── constants/testIds/       # data-testid registry
│   │   ├── hooks/                   # e.g. use-toast.js
│   │   ├── lib/
│   │   │   ├── api.js               # axios client + REACT_APP_BACKEND_URL
│   │   │   ├── exporters.js         # PDF + DOCX generators
│   │   │   ├── theme.jsx            # next-themes provider
│   │   │   └── utils.js             # cn() helper
│   │   ├── App.css
│   │   └── index.css                # Tailwind + design tokens
│   ├── package.json                 # yarn only — never npm
│   └── .env                         # REACT_APP_BACKEND_URL only
├── docs/                            # This documentation pack
├── memory/                          # PRD.md, test_credentials.md
└── test_reports/                    # Testing-agent iteration JSONs
```

## 3. Runtime topology

```
Browser ──HTTPS──> Kubernetes ingress
                       │
                       ├── /api/*  ──▶ Uvicorn :8001 (FastAPI)
                       │                     │
                       │                     ├─▶ MongoDB (Motor, async)
                       │                     ├─▶ Tavily API   (HTTPS)
                       │                     └─▶ Emergent LLM (HTTPS)
                       │
                       └── /* ─────▶ React dev server :3000  (yarn start)
```

- Everything the browser needs from the API is prefixed `/api` and reaches
  Uvicorn on port `8001` through the ingress. All other paths route to the
  React dev server on `3000`.
- The frontend **only** knows about `process.env.REACT_APP_BACKEND_URL`.
  No localhost, no hardcoded preview URL anywhere.

## 4. Data flow — POST /api/summarize

```
Client ─▶ POST /api/summarize {url}
           │
           │  server.summarize()
           │  ├── validate URL   (services.looks_like_article_url)
           │  ├── normalize URL  (services.normalize_url)
           │  ├── compute hash   (services.url_hash — SHA-256)
           │  ├── cache lookup   (CacheAndRateLimit.get_cached)
           │  │        ▲ hit? ── return {cached:true, summary}   (free)
           │  ├── check_rate    (per-IP sliding hour)
           │  ├── extract_article (trafilatura + UA fallback)
           │  ├── summarize_article (Claude Sonnet 4.5, JSON schema)
           │  ├── build_summary_payload (canonicalise shape)
           │  └── set_cached    (payload stored under `payload`, 12 h TTL)
           ▼
       {cached:false, summary, rate_limit}
```

## 5. Data flow — POST /api/latest-updates (the pipeline)

The Latest Updates pipeline is the most complex code path. Order matters —
never re-order these stages without reading `Rules.md` §3.

```
Client ─▶ POST /api/latest-updates {topic, source_url, force_refresh}
           │
           │  server.latest_updates()
           │  ├── (optional) cache lookup — get_cached_delta
           │  │        empty-delta rows use LATEST_UPDATES_EMPTY_TTL_MINUTES
           │  │        (default 30 min) so retries are possible
           │  ├── load baseline from summary_cache (title, entities, pub date)
           │  │
           │  │  Stage 1 — build_queries()
           │  │    up to 8 query variants from entities/topic/baseline date
           │  │    tickers (all-caps ≤5 chars) always paired with a longer
           │  │    disambiguator — never emitted standalone (ANTM collision
           │  │    with "America's Next Top Model" was the RCA)
           │  │
           │  │  Stage 2 — search_candidates()
           │  │    ALL 8 queries CONCURRENT via asyncio.gather+to_thread
           │  │    hard 12-month window (LATEST_UPDATES_WINDOW_DAYS=365)
           │  │    drop candidates with missing/unparseable dates
           │  │    drop candidates older than the window
           │  │    dedupe by URL + title Jaccard ≥0.8
           │  │    weekly-bucketed round-robin selection (cap _MAX_CANDIDATES_FOR_LLM)
           │  │
           │  │  Stage 3 — web_fetch_candidates()
           │  │    top-N pages fetched concurrently (8s timeout)
           │  │    MANDATORY fallback to snippet if fetch fails
           │  │    content_kind = full_text | snippet
           │  │
           │  │  Stage 4 — cluster_by_date()
           │  │    group by publish day before synthesis
           │  │
           │  │  Stage 5 — synthesize_delta(baseline, clusters)
           │  │    single Claude call, strict JSON schema
           │  │    ROLLING-MARKET rule in prompt — later dated data points
           │  │    on the same ongoing story count as updates
           │  │    DROP any event whose date does not parse
           │  │    DROP any event dated on/before baseline (anchor rule)
           │  │
           │  │  Stage 6 — merge_timeline(stored, new)
           │  │    accumulate into timeline_store (persistent, no TTL)
           │  │    same_event = same day + Jaccard ≥ 0.5
           │  │    strip any legacy row without a parseable date
           │  │
           │  │  Stage 7 — compute_confidence(candidates, merged_delta)
           │  │    deterministic 3-factor score, computed AFTER merge
           │  │    +1 ≥4 independent domains
           │  │    +1 ≥50% timeline events corroborated by ≥2 domains
           │  │    +1 newest used candidate < 48 h
           │  │
           │  │  Stage 8 — consistency guard
           │  │    if merged has post-baseline events but has_update is
           │  │    false → force has_update=true + populate overview +
           │  │    append note to confidence.reason
           │  │
           │  └── persist: save_timeline + set_cached_delta
           ▼
       {has_update, overview, developments, timeline[],
        current_situation, market_impact, sources_used,
        confidence:{level, reason}}
```

## 6. Environment variables

| Variable                              | Where     | Purpose                                         |
| ------------------------------------- | --------- | ----------------------------------------------- |
| `MONGO_URL`                           | backend   | Mongo connection string (Emergent-provisioned)  |
| `DB_NAME`                             | backend   | Mongo database name                             |
| `CORS_ORIGINS`                        | backend   | Comma-separated allowlist (default `*`)         |
| `EMERGENT_LLM_KEY`                    | backend   | Universal key for Claude                        |
| `TAVILY_API_KEY`                      | backend   | User-supplied Tavily key                        |
| `RATE_LIMIT_SUMMARY`                  | backend   | Per-IP hourly cap on /summarize   (default 5)   |
| `RATE_LIMIT_UPDATES`                  | backend   | Per-IP hourly cap on /latest-updates (default 5)|
| `RATE_LIMIT_TRANSLATE`                | backend   | Per-IP hourly cap on /translate   (default 10)  |
| `CACHE_TTL_HOURS`                     | backend   | Summary + non-empty delta TTL (default 12 h)    |
| `CACHE_TTL_DELTA_MINUTES`             | backend   | Optional shorter delta TTL (has_update:true)    |
| `LATEST_UPDATES_EMPTY_TTL_MINUTES`    | backend   | TTL for has_update:false delta   (default 30)   |
| `LATEST_UPDATES_WINDOW_DAYS`          | backend   | Hard Tavily window from today   (default 365)   |
| `REACT_APP_BACKEND_URL`               | frontend  | Public preview / production origin              |

**Rules**
- Never delete the protected keys (`MONGO_URL`, `DB_NAME`,
  `REACT_APP_BACKEND_URL`).
- No default values in code — missing env must fail fast.
- Do not add comments inside `.env` files.

## 7. Key design decisions and why

### 7.1 Single `services.py` module
Business logic lives in ONE file. It is ~1200 lines and stays intentionally
flat: one function per pipeline stage, all public via a stable import
surface consumed by `server.py`. This keeps the runtime graph obvious and
lets the test suite import stages individually. **Do not** split into
sub-packages without also updating every import in the tests.

### 7.2 Concurrent Tavily fan-out (not sequential)
`asyncio.gather` + `asyncio.to_thread` around the sync Tavily client
gives us 2–6 s for 8 queries. Sequential took 20+ s.

### 7.3 Web-fetch fallback is MANDATORY
If `web_fetch()` fails for a candidate, we still keep the snippet — we
never drop the candidate. Any change that drops candidates on fetch
failure is a regression.

### 7.4 Deterministic confidence
`compute_confidence()` is pure code, not LLM output. Score is
reproducible, sorted for auditability, and can be verified in tests.

### 7.5 Timeline is append-only
`timeline_store` has its own collection with no TTL. `merge_timeline()`
unions new events with the persisted set. If we let the timeline be
regenerated from scratch every 12 h, we would lose history whenever a
future Tavily search misses old events. Append-only was the fix.

### 7.6 Empty-delta cache has its own TTL
Users who see "no updates found" must not be stuck for 12 h. We give
`has_update:false` results a shorter TTL (default 30 min, configurable via
`LATEST_UPDATES_EMPTY_TTL_MINUTES`).

### 7.7 Ticker disambiguation
Short all-caps entities (ANTM, BBRI) are ALWAYS paired with a longer
disambiguator before being sent to Tavily. This one line of code
eliminated a whole class of "empty results because Tavily returned
America's Next Top Model articles" bugs.

### 7.8 Frontend never talks to Mongo
There is no direct DB access from the browser. Every read/write flows
through `/api/*`. This keeps the DB schema swappable without a frontend
change.

### 7.9 Cache-first rate limit
`/api/summarize` checks the cache **before** decrementing the rate limit.
Cache hits never charge the budget. This is what makes the Recent News
feed and repeat clicks free.

### 7.10 Only shadcn primitives, only sonner toasts, only lucide icons
Do not introduce another UI library, toast library, or icon set.

## 8. Deployment

- Preview: `https://<preview-slug>.preview.emergentagent.com`
- Production: `https://<slug>.emergent.host`
- Deployment readiness check must PASS before hitting Deploy in the
  Emergent dashboard. See `deployment_agent` output in the session log.
- Supervisor auto-restarts on file change. Manual restart:
  `sudo supervisorctl restart backend` (env or dependency changes only).
