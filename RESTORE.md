# News Summarizer — Restore Instructions

This zip contains the full project source (backend + frontend + memory docs).
`node_modules`, `.git`, `.emergent`, build artefacts and Python caches were
excluded to keep the archive small.

## Contents
- `backend/` — FastAPI app (`server.py`, `services.py`, tests, `.env`)
- `frontend/` — React + Vite/CRA app (`src/`, `public/`, `.env`, `package.json`)
- `memory/` — PRD.md, test_credentials.md
- `test_reports/` — testing agent iteration reports

## Sensitive keys (please rotate before sharing)
`backend/.env` contains real API keys:
- `EMERGENT_LLM_KEY` — Emergent universal LLM key
- `TAVILY_API_KEY` — Tavily live-search API key
- `MONGO_URL` / `DB_NAME`
Rotate anything you don't want the next environment to see.

## Restore on a new machine

```bash
# 1. Unzip
unzip news-summarizer-backup.zip -d news-summarizer && cd news-summarizer

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Ensure MongoDB is running locally, or point MONGO_URL to your cluster
uvicorn server:app --host 0.0.0.0 --port 8001 --reload

# 3. Frontend (new terminal)
cd ../frontend
yarn install
# Set REACT_APP_BACKEND_URL in .env to your backend URL
yarn start
```

## Key architecture notes
- Backend: FastAPI + Motor (MongoDB async), Trafilatura scraping,
  Claude Sonnet 4.5 via `emergentintegrations`, Tavily for live updates.
- Frontend: React SPA, Shadcn UI, Framer Motion, Cabinet Grotesk / IBM Plex.
- Cache schema per URL hash: `{payload: <ID summary>, translations: {en: ...}}`
  with 24 h TTL.
- Rate limits: sliding-hour per-IP buckets in Mongo (`summarize`, `updates`,
  `translate` — 5/hour each by default).

## Endpoint map
- `POST /api/summarize` — scrape + summarise in Bahasa Indonesia
- `POST /api/translate` — `{url, target}` — cache-first ID↔EN
- `POST /api/latest-updates` — Tavily live search
- `GET /api/recent?limit=6` — global "Last 24h" feed
- `GET /api/rate-status` — remaining budget for current IP
- `GET /api/health` — ping

## Result-card section order
1 Executive Summary · 2 Key Points · 3 Main Issue · 4 Root Cause ·
5 Sentiment & Bias Analysis · 6 Recommended Actions · 7 Latest Updates ·
8 5W1H · 9 References · 10 Confidence
