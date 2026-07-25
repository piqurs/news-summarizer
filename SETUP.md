# News Summarizer — Local Setup Guide

Full step-by-step tutorial to run this project on your own machine (macOS,
Linux or Windows/WSL). By the end you'll have the backend on
`http://localhost:8001`, the React frontend on `http://localhost:3000`, and
a working end-to-end summarizer.

Estimated time: **10 – 15 minutes**.

---

## 1 · Prerequisites

Install these once:

| Tool | Version | Install |
|------|---------|---------|
| **Python** | 3.11 or newer | https://www.python.org/downloads/ |
| **Node.js** | 20 LTS or newer | https://nodejs.org (or use nvm) |
| **Yarn** | Classic (v1) | `npm install -g yarn` |
| **MongoDB** | 6.x or newer (Community edition is fine) | https://www.mongodb.com/try/download/community |
| **Git** (optional) | any | https://git-scm.com/ |

Quick check they're all installed:

```bash
python --version        # → Python 3.11+
node --version          # → v20+
yarn --version          # → 1.22+
mongod --version        # → db version v6+
```

> **Windows tip** — this guide uses bash-flavoured commands. Either use
> **WSL 2** (Ubuntu recommended) or translate `source .venv/bin/activate` to
> `.venv\Scripts\activate` on plain PowerShell.

---

## 2 · Unzip the project

```bash
unzip news-summarizer-backup.zip -d news-summarizer
cd news-summarizer
```

Folder layout you should see:

```
news-summarizer/
├── backend/            ← FastAPI + Motor (Mongo) service
├── frontend/           ← React SPA
├── memory/             ← PRD.md and running notes
├── test_reports/       ← previous testing-agent iteration reports
└── SETUP.md            ← this file
```

---

## 3 · Get your API keys

You need **two** keys — both free and take under 2 minutes to obtain.

### 3.1 · Emergent Universal LLM Key (for Claude Sonnet 4.5)

1. Go to **https://app.emergent.sh** and sign in (Google works).
2. Click your avatar → **Profile** → **Universal Key**.
3. Copy the key that starts with `sk-emergent-…`.

This single key routes to Claude / OpenAI / Gemini through the
`emergentintegrations` Python package. You can also click **Add Balance** on
that page to top up — a full summary costs a fraction of a cent.

### 3.2 · Tavily API Key (for the Live Updates section)

1. Go to **https://tavily.com** and sign up (Google or email).
2. On the dashboard, copy the key that starts with `tvly-…`.
3. The free tier gives 1000 searches/month, more than enough for local
   testing.

Keep these two strings on hand for the next step.

---

## 4 · Start MongoDB

Pick **one** of the two options.

### Option A — Local Mongo (recommended for local dev)

```bash
# macOS (Homebrew)
brew tap mongodb/brew
brew install mongodb-community@7.0
brew services start mongodb-community@7.0

# Ubuntu / Debian
sudo systemctl start mongod

# Windows
# Install the MSI, then start the "MongoDB" service from Services.msc
```

Verify:

```bash
mongosh --eval "db.runCommand({ping:1})"
# Should print: { ok: 1 }
```

Connection string to use: `mongodb://localhost:27017`

### Option B — MongoDB Atlas (free cloud tier)

1. Create a free cluster at **https://www.mongodb.com/cloud/atlas**.
2. Add a database user + whitelist your IP.
3. Copy the connection string — it looks like
   `mongodb+srv://<user>:<password>@<cluster>.mongodb.net`.

---

## 5 · Backend — FastAPI

```bash
cd backend

# 5.1 · Create an isolated Python environment
python -m venv .venv
source .venv/bin/activate            # macOS / Linux
# .venv\Scripts\activate             # Windows PowerShell

# 5.2 · Install Python deps
pip install --upgrade pip
pip install -r requirements.txt
pip install emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/

# 5.3 · Copy the env template and fill in your keys
cp .env.example .env
# Now open backend/.env in your editor and paste:
#   EMERGENT_LLM_KEY="sk-emergent-…"      (from step 3.1)
#   TAVILY_API_KEY="tvly-…"                (from step 3.2)
# Leave MONGO_URL as localhost if you did step 4-A, or replace it with your
# Atlas connection string from step 4-B.
```

**Start the server:**

```bash
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

You should see `Uvicorn running on http://0.0.0.0:8001`. Leave this terminal
open.

**Sanity check** (in a second terminal):

```bash
curl http://localhost:8001/api/health
# → {"status":"ok","time":"…"}
```

---

## 6 · Frontend — React SPA

Open a **new terminal** (keep the backend running in the first one).

```bash
cd news-summarizer/frontend

# 6.1 · Copy the env template
cp .env.example .env
# The default REACT_APP_BACKEND_URL=http://localhost:8001 is correct for
# local dev — no edit needed unless you host the backend elsewhere.

# 6.2 · Install JS deps (always use yarn, npm can break the lockfile)
yarn install
```

**Start the dev server:**

```bash
yarn start
```

Your browser should auto-open **http://localhost:3000** with the app. If it
doesn't, open that URL manually.

---

## 7 · Try it end-to-end

1. Paste any of these URLs into the input:
   - `https://apnews.com/hub/artificial-intelligence`
   - `https://www.aljazeera.com/economy/`
   - `https://techcrunch.com/` (some articles are bot-blocked — try another
     if you hit a 502)
2. Click **Summarize** — first run takes ~15 – 25 seconds (Claude call +
   scraping). The result renders in **Bahasa Indonesia** by default.
3. Click the **EN** pill on the result card — the summary translates. Toggle
   back to **ID**, it's instant (client cache).
4. Scroll down to **Recent News · Last 24h** — your run should already
   appear as a card.
5. Click **Show Latest Updates** at the bottom of the Recommended Actions
   section — Tavily fetches live news on the same topic.
6. Try **PDF**, **Word**, **Copy**, **Share** in the export row.

---

## 8 · Common issues & fixes

**Backend won't start with `ModuleNotFoundError: emergentintegrations`**
You forgot the extra index. Re-run:
```bash
pip install emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/
```

**Frontend blank / white screen**
Check `frontend/.env` — `REACT_APP_BACKEND_URL` must match where your
backend is actually running (default `http://localhost:8001`, no trailing
slash). Then hard-refresh (Cmd/Ctrl-Shift-R).

**429 "Hourly limit reached"**
The per-IP sliding-hour rate limits triggered (5 summarize / 5 translate /
5 updates per hour). Clear them:
```bash
python -c "from pymongo import MongoClient; import os; from dotenv import load_dotenv; load_dotenv('backend/.env'); MongoClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']].rate_limit.delete_many({})"
```

**"Could not download the article" / 502**
The target news site blocks automated readers (Reuters, BBC, NYT frequently
do). Try a different source — AP News, Al Jazeera, Ars Technica and most
regional sites work.

**"Please summarize it first" on Recent News click**
The cached row has expired (24 h TTL). Just paste the URL again.

**Mongo connection error**
Ensure `mongod` is running (`brew services list` on Mac,
`sudo systemctl status mongod` on Linux) and that `MONGO_URL` in
`backend/.env` matches. For Atlas, whitelist your current IP.

---

## 9 · Project map (quick reference)

**Backend** (`backend/`)
```
server.py         FastAPI routes  /api/{health,summarize,translate,latest-updates,recent,rate-status}
services.py       Scraping (Trafilatura) + Claude prompts + cache + rate limit + Tavily
requirements.txt  Python deps
.env              Runtime secrets (git-ignored)
.env.example      Template with placeholders
```

**Frontend** (`frontend/src/`)
```
pages/Home.jsx                 Top-level state + composition
components/Hero.jsx            URL input + rate-limit budget chip
components/ResultCard.jsx      Sections 1–10 of the analysis
components/SentimentBias.jsx   Section 5 (tone + bias indicators)
components/RecentNews.jsx      "Last 24h" global cache feed
components/LatestUpdates.jsx   On-demand Tavily section
components/LanguageToggle.jsx  EN | ID pill
components/ExportShare.jsx     Copy / PDF / Word / Share
lib/api.js                     Axios client + all endpoint helpers
lib/exporters.js               PDF (jspdf) + Word (docx) generators
```

**Data model** (Mongo collection `summary_cache`)
```jsonc
{
  "hash": "<sha256 of normalized url>",
  "url":  "https://…",
  "created_at": "2026-02-…",
  "payload":      { /* full Indonesian summary */ },
  "translations": { "en": { /* full English summary */ } }
}
```

TTL is 24 h — the 24-h window is enforced in code, not by Mongo indexes, so
expired rows physically remain until overwritten but are ignored by all
reads.

---

## 10 · Deploying to production

Two easy paths:

- **Emergent hosting** — from the original chat interface, click the
  **Deploy** button. It provisions Mongo + backend + frontend behind a
  managed HTTPS domain.
- **Self-host** — deploy backend to Fly.io / Render / Railway with the same
  env vars, frontend to Vercel / Netlify (build command `yarn build`,
  publish `frontend/build`). Set `REACT_APP_BACKEND_URL` to your backend's
  public URL before building the frontend.

---

## 11 · Rotate before sharing

The bundled `backend/.env` (if present in this archive) contains real API
keys. Before pushing this project to a public repo:

1. Copy `.env` to `.env.local` for your own use.
2. Delete or replace secret values in `.env`.
3. Add `backend/.env` and `frontend/.env` to your `.gitignore` (they
   already are in the shipped `.gitignore`).

Have fun!
