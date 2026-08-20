# prompt.md — News Summarizer · Master Instruction Document for an AI Agent

> This is the single source of truth for any AI agent tasked with continuing
> development on the News Summarizer application. Do not skim. Do not
> extrapolate. Do not act until every section below is fully internalised.
>
> If the task in front of you conflicts with a rule in this document, stop
> and ask the human before touching code.

---

## 1. Mission

You are contributing to **News Summarizer**, a production-ready full-stack
web application that turns any public news article URL into a structured,
easy-to-read analytical summary in Bahasa Indonesia, with an on-demand
"Latest Updates" pipeline that surfaces what has happened since the article
was published.

The app is deployed and live. Your job is one of:

1. Extending it with a new **in-scope** feature listed in
   `docs/Project.md` §4 or promoted from §9 Backlog.
2. Fixing a bug reported against the preview or production environment.
3. Improving documentation without changing behaviour.

Anything else — refactors, "cleanups", speculative changes — is out of
scope until a human promotes it.

## 2. Read these before you touch anything

Read them in this order. They are self-consistent; do not "resolve"
apparent conflicts on your own.

1. `/app/docs/Project.md`      — scope, goals, MVP, success metrics.
2. `/app/docs/Architecture.md` — stack, folder layout, runtime graph.
3. `/app/docs/Schema.md`       — MongoDB collections and invariants.
4. `/app/docs/Design.md`       — UI/UX flow, design system, components.
5. `/app/docs/Rules.md`        — style guide, guardrails, "do not touch".
6. `/app/memory/PRD.md`        — historical decision log.
7. `/app/backend/server.py`    — HTTP surface.
8. `/app/backend/services.py`  — all business logic.
9. `/app/backend/tests/`       — regression suite (must stay green).
10. Any file in `/app/frontend/src/components/` you are about to modify.

You may skim only files unrelated to the change at hand.

## 3. The one-paragraph mental model

A user pastes a URL. The backend normalises it, hashes it (SHA-256), and
checks the 12-hour cache. Cache miss goes through **article extraction**
(trafilatura + a UA-header fallback) then a **single Claude Sonnet 4.5**
call that returns a strictly structured JSON summary in Bahasa Indonesia.
When the user clicks *Latest Updates*, the backend runs an 8-way concurrent
Tavily search (bounded to a hard 12-month window from today), fetches full
pages concurrently with mandatory snippet fallback, clusters candidates by
date, calls Claude once more to synthesise a delta against the baseline
article, merges the resulting timeline into an append-only
`timeline_store` collection, computes a deterministic 3-factor confidence
score, applies a consistency guard, and returns the result. Every AI-cost
call is gated by both cache lookup and a per-IP sliding-hour rate limit.
Undated events are dropped end-to-end. The response contract must never
be self-contradictory (`has_update:false` with a non-empty timeline is a
bug).

## 4. Non-negotiable invariants

If any change you propose would violate any of these, stop.

- **Bahasa Indonesia is the default output language.** English literals
  are preserved *only* for `confidence.level` (`High | Medium | Low`)
  and `sentiment_and_bias.tone` (`Positive | Neutral | Negative | Mixed`).
- **Cache hits never charge the rate limit budget.**
- **All backend routes are prefixed `/api`.** No exceptions.
- **The frontend only knows about `REACT_APP_BACKEND_URL`.** No hardcoded
  URLs, no `localhost:8001`, no preview URLs in code.
- **URL is normalized before hashing** (`services.normalize_url`).
- **Every LLM/Tavily call is guarded** by cache + rate limit + explicit
  user intent (Latest Updates is behind a button).
- **The Latest-Updates pipeline stage order is fixed** (build_queries →
  search_candidates → web_fetch_candidates → cluster_by_date →
  synthesize_delta → merge_timeline → compute_confidence → consistency
  guard). Do not re-order.
- **No candidate or timeline event without a parseable date ever renders.**
  Filtered in `search_candidates`, `synthesize_delta`, `merge_timeline`,
  and `get_timeline`.
- **Timeline is append-only.** Never regenerated from scratch.
- **Web-fetch fallback to snippet is MANDATORY.** Fetch failure keeps
  the candidate; it never drops it.
- **`has_update` and `timeline` must be internally consistent.** If
  `merged` has post-baseline events, `has_update` is `true`.
- **`components/ui/*` (shadcn primitives) are not edited** — wrapped or
  extended, never modified in place.
- **`data-testid` is required** on every interactive element and every
  user-critical text element (see `docs/Design.md` §9).
- **Toasts: `sonner` only. Icons: `lucide-react` only.** No new UI /
  toast / icon libraries.
- **Third-party integrations** are always fetched via
  `integration_playbook_expert_v2`. Never hand-write SDK code from memory.
  LLMs go through `emergentintegrations` + `EMERGENT_LLM_KEY`.
- **`.env`** — never delete `MONGO_URL`, `DB_NAME`,
  `REACT_APP_BACKEND_URL`. No comments in `.env`. No default fallbacks
  in code — missing config must fail loudly.

## 5. Files you must not touch without stating a reason

See `docs/Rules.md` §3 for the full table. Highlights:

- `services.normalize_url()`
- `services._SYSTEM_PROMPT` (summarize)
- `services._DELTA_SYSTEM_PROMPT` (latest updates)
- `services.search_candidates()`
- `services.synthesize_delta()`
- `services.merge_timeline()`
- `services.compute_confidence()`
- `services._disambiguate()` / `_looks_like_ticker()`
- `server.latest_updates()` stage ordering
- Every file under `frontend/src/components/ui/`

If your change *has* to modify one of these, describe why in the PR
description and update `Rules.md` §3 in the same change.

## 6. How to run and test

- Backend is under **supervisor** on `:8001`, frontend on `:3000`. Both
  hot-reload on file change.
- `sudo supervisorctl restart backend` only after `.env` changes or new
  dependency installs.
- Backend logs: `/var/log/supervisor/backend.err.log` and
  `.out.log`.
- Health check: `GET /api/health` → `{status: "ok"}`.
- Full pipeline probe:
  ```
  curl -sS -X POST "$REACT_APP_BACKEND_URL/api/summarize" \
    -H 'Content-Type: application/json' -d '{"url":"<article url>"}'
  curl -sS -X POST "$REACT_APP_BACKEND_URL/api/latest-updates" \
    -H 'Content-Type: application/json' \
    -d '{"topic":"<topic>","source_url":"<article url>","force_refresh":true}'
  ```
- Regression suite: `cd /app && pytest backend/tests/ -q`.
- Do **not** hand-run the frontend build in a loop. One smoke
  screenshot at the end of a batch of changes is enough — the testing
  agent covers the rest.

## 7. Workflow for you (the AI agent)

1. **Ask** if scope, target environment (preview vs production), or
   inputs are ambiguous. One clarifying round, then decide and move.
2. **Plan** and share the plan with the human before writing code
   when the change touches more than one file or ~40 lines.
3. **Read** the files you are about to touch — every one of them.
4. **Implement** the smallest change that solves the task. Do not
   refactor around your change.
5. **Test**
   - Small change: `curl` + one screenshot.
   - Bigger change: run backend `pytest` and either self-test
     end-to-end or hand off to the testing agent with a written scope.
6. **Document**
   - Update `/app/memory/PRD.md` with a dated entry.
   - Update the relevant `/app/docs/*.md`.
   - If new interactive elements were added, register their
     `data-testid`s in `frontend/src/constants/testIds/`.
7. **Finish**
   - Summarise what changed in 2–3 lines.
   - List 3–4 concrete next-action items.
   - Highlight anything mocked or unverified.

## 8. Anti-patterns you must never fall into

- ❌ Adding "just one more" LLM call somewhere that bypasses the cache.
- ❌ Emitting a timeline entry with `date: null`, `date: "unknown"`, or
  any unparseable date.
- ❌ Returning `has_update: false` alongside a non-empty timeline.
- ❌ Rewriting the entity extraction to drop `_disambiguate()` because
  "ANTM is obviously a ticker" — the point of the function is that
  Tavily disagrees.
- ❌ Adding a new UI framework or icon set.
- ❌ Editing `components/ui/*` shadcn primitives in place.
- ❌ Silencing a failing test to unblock a deploy.
- ❌ Hardcoding a URL, port, or secret.
- ❌ Introducing a new dependency for a task solvable with what is
  already in `requirements.txt` / `package.json`.
- ❌ Refactoring `services.py` into sub-packages because "it is long".
  It is *deliberately* flat — the tests import stages individually.
- ❌ Skipping tests because "the change is tiny".

## 9. Definition of done

A change is done when:

- [ ] Code compiles and imports cleanly on backend and frontend.
- [ ] `backend/tests/` is green.
- [ ] Every new interactive element has a `data-testid`.
- [ ] Every new document field is reflected in `Schema.md`.
- [ ] Every new env var is reflected in `Architecture.md` §6.
- [ ] `/app/memory/PRD.md` has a new dated entry.
- [ ] Supervisor shows `backend` and `frontend` both running.
- [ ] `deployment_agent` health check still passes.
- [ ] The response contract for `/api/latest-updates` is self-consistent
      (see §4 invariants).
- [ ] No unresolved TODOs left in the diff without an owner and a date.

## 10. Final rule

When in doubt, **do less**. Small, well-tested changes ship. Big,
speculative rewrites do not.

If the plan you are about to execute cannot be summarised in three
sentences, the plan is too big. Break it down, ship the smallest
piece first, and revisit.

---

**End of prompt.md.** This document supersedes any conflicting instruction
you may hold from other sources for this project.
