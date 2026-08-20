# Rules.md — News Summarizer

> Coding conventions, style guide, and guardrails for humans and AI agents.
> Level: **Medium** — style + conventions + a few explicit "don't touch"
> guardrails, no exhaustive line-by-line policing.
>
> Read this **before editing anything** in `/app`.

---

## 0. First principles

1. **Read before you edit.** If you cannot state in one sentence what a
   function does, do not modify it. Read `Project.md`, `Architecture.md`,
   and this file first.
2. **Minimum-viable change.** Fix only what was asked. Do not refactor,
   restyle, or "improve" code around your change.
3. **Trust the framework.** No defensive error handling for cases that
   cannot happen. Only validate at true system boundaries: user input,
   Tavily API, Claude API, MongoDB.
4. **Cache & rate limit are load-bearing.** Every AI call is guarded by
   both. Do not add an AI call that bypasses either.

## 1. Style guide

### 1.1 Python (backend)

- **PEP 8** with 4-space indent, 88-char soft limit (Black-compatible).
- Type hints on every public function and helper. `from __future__ import
  annotations` at the top of new modules.
- Docstrings: short, English, imperative voice. Explain **why** the
  function exists if the "why" is non-obvious. The "what" is usually
  the signature.
- Prefer `datetime.now(timezone.utc)`; **never** `datetime.utcnow()`.
- Prefer `logger.info/warning/exception`; never `print()` in service
  code (there is one `print("Line 242 from console")` left in server.py
  — feel free to remove it if you touch that block, but don't drift
  into `print` elsewhere).
- Prefer async (`await`) throughout the backend. If you need to run
  sync SDK code (e.g. Tavily), wrap with `asyncio.to_thread`.
- Constants at module top in `UPPER_SNAKE_CASE`.
- Private helpers start with a leading underscore (`_helper_name`).
- Import order: stdlib → third-party → local. Group with a blank line.
- No wildcard imports.

### 1.2 JavaScript / JSX (frontend)

- **ESLint config already in package.json** — respect it. No inline
  disables without a written reason.
- 2-space indent. Single quotes for JS, double quotes for JSX props.
  Prettier if you use one — otherwise mimic the surrounding file.
- Components:
  - **Named exports** for reusable components:
    `export const ComponentName = () => {...}`
  - **Default export** for page components in `src/pages/`.
  - One component per file. Keep components under ~50 lines when
    possible; if it grows past 100, extract sub-components.
- Data fetching goes through `lib/api.js`. Never import axios directly
  in a component.
- CSS: **Tailwind first**. Only reach for CSS classes in `index.css`
  when Tailwind cannot express it cleanly (design tokens, keyframes).
- Component state co-located with the component. Do not introduce a
  global store (Redux, Zustand) — the app doesn't need it.
- **Toasts**: `sonner` only. Do not add another toast library.
- **Icons**: `lucide-react` only. Do not add another icon library, and
  never use emoji as an icon.

### 1.3 Naming

| Kind                 | Convention                | Example                    |
| -------------------- | ------------------------- | -------------------------- |
| Python module        | `snake_case.py`           | `services.py`              |
| Python function      | `snake_case`              | `merge_timeline()`         |
| Python class         | `PascalCase`              | `CacheAndRateLimit`        |
| Python constant      | `UPPER_SNAKE_CASE`        | `LATEST_UPDATES_QUERY_COUNT` |
| JS component         | `PascalCase.jsx`          | `LatestUpdates.jsx`        |
| JS function          | `camelCase`               | `fmtDate()`                |
| CSS class            | `kebab-case`              | `surface-card`             |
| `data-testid`        | `kebab-case`, action-first| `summarize-btn`            |
| Mongo collection     | `snake_case`              | `timeline_store`           |
| Env var              | `UPPER_SNAKE_CASE`        | `LATEST_UPDATES_WINDOW_DAYS` |

## 2. `data-testid` rule

Every interactive element **and** every element rendering user-critical
info (badges, prices, counters, timestamps, errors, dialog headers) has
a unique kebab-case `data-testid`. See `Design.md` §9 for naming and
the registry in `frontend/src/constants/testIds/`. This is not optional
— the test suite depends on it.

## 3. Do not touch without an explicit reason

These functions and blocks are load-bearing. Read the reason column
before considering a change. If you must change them, update this file
in the same PR.

| Path                                       | Function / block            | Reason to leave alone                                                          |
| ------------------------------------------ | --------------------------- | ------------------------------------------------------------------------------ |
| `backend/services.py`                      | `normalize_url()`           | Cache correctness depends on deterministic normalization.                      |
| `backend/services.py`                      | `_SYSTEM_PROMPT` (summarize)| The 9-section JSON schema and the "Bahasa Indonesia default" rule live here.   |
| `backend/services.py`                      | `_DELTA_SYSTEM_PROMPT`      | Ships the ROLLING-MARKET rule and the no-null-date rule for Latest Updates.    |
| `backend/services.py`                      | `search_candidates()`       | Concurrency + date-window filter — do not re-serialise, do not weaken filters. |
| `backend/services.py`                      | `synthesize_delta()`        | Drops undated events and enforces baseline-date anchoring.                     |
| `backend/services.py`                      | `merge_timeline()`          | Append-only. Strips legacy undated rows.                                       |
| `backend/services.py`                      | `compute_confidence()`      | Deterministic 3-factor score. Overrides LLM self-claim.                        |
| `backend/services.py`                      | `_disambiguate()`, `_looks_like_ticker()` | Ticker-collision fix (ANTM/BBRI). Removing it re-introduces empty-result bugs. |
| `backend/server.py`                        | `latest_updates()` stages   | Stage ordering (merge before confidence, consistency guard last) is intentional. |
| `frontend/src/components/ui/*`             | any file                    | shadcn/ui primitives — wrap, don't edit.                                       |

## 4. Adding a feature

1. Add or update the entry in `Project.md` §4 (In-scope MVP feature
   set). Feature is not "real" until it appears there.
2. Extend `Schema.md` if a new field / collection is needed.
3. Extend `Architecture.md` §5 if the pipeline changes.
4. Write or update a pytest under `backend/tests/` (regression-first).
5. Land the code. Frontend `data-testid`s are mandatory for interactive
   elements introduced in this change.
6. Run backend tests locally. If touching the pipeline or exposing a
   new endpoint, run the testing agent.
7. Update `/app/memory/PRD.md` with a dated entry.

## 5. Fixing a bug

1. **Reproduce first.** If you cannot reproduce the bug locally or in
   preview, do not "fix" it.
2. Trace the root cause through the failure chain. Surface patches on
   assumed causes are what break the app in production.
3. Add or update a regression test in `backend/tests/` that would have
   caught the bug.
4. Land the fix. Update `Rules.md` §3 if the fix touched a "do not
   touch" area.

## 6. Third-party integrations

- Every integration goes through `integration_playbook_expert_v2`. Do
  not hand-write SDK code from memory.
- LLM calls: use `emergentintegrations` + `EMERGENT_LLM_KEY`. Do not
  install the raw Anthropic / OpenAI / Google SDKs.
- Search calls: `tavily-python`.
- Do not introduce a new integration without updating `Architecture.md`
  §1 and `Project.md` §6.

## 7. Environment variables

- Never delete the protected keys (`MONGO_URL`, `DB_NAME`,
  `REACT_APP_BACKEND_URL`).
- Never hardcode a URL, port, or secret.
- No default values in code — missing config must fail loudly.
- No comments inside `.env` files.
- If you add a new env var, add it to `Architecture.md` §6 in the same
  change.

## 8. Dependencies

- Python: install with pip, then `pip freeze > backend/requirements.txt`.
  Never hand-edit `requirements.txt`.
- Node: **yarn only**, `yarn add <pkg>`. Never `npm install`.
- Prefer no new dependencies. If you must add one, note it in
  `Architecture.md` §1.

## 9. Git & deploy

- Do **not** commit `.env` files.
- Do **not** delete `.git/` or `.emergent/`.
- Preview vs production: fix in preview → user redeploys. You cannot
  push to production directly.
- Use the **Save to Github** feature in Emergent chat for git write
  actions.

## 10. Comments

- Write comments that explain **why**, not **what**. The code shows
  the what.
- Multi-line block comments in Python are welcome above tricky logic
  (see the "Tahap 5–7" block in `latest_updates`) — leave them.
- Do not leave `// TODO` markers without an owner and a date. If it is
  not going to be done this week, it belongs in `Project.md` §9
  Backlog.

## 11. AI-agent guardrails

For AI agents contributing to this repo:

- **Ask before rewriting.** If your plan touches more than one file
  or more than 40 lines, describe the plan first.
- **No speculative refactors.** Don't rename symbols, split modules,
  or reorganise folders "for cleanliness".
- **No "improvements" to prompts** without human sign-off. The prompts
  in `_SYSTEM_PROMPT` and `_DELTA_SYSTEM_PROMPT` are calibrated by
  regression tests.
- **Match the surrounding style.** If a file uses `logger.info`, don't
  add `print()`. If a file uses named exports, don't add a default
  export.
- **Every new interactive element needs a `data-testid`.** Non-negotiable.
- **Every backend change needs a test.** Even if it is a one-line change
  in a helper.
- **Never disable a test to make the pipeline green.** Fix the test or
  fix the code — those are the only two options.

## 12. Definition of done

A change is done when all of the following are true:

- [ ] Code compiles / imports cleanly on backend and frontend.
- [ ] `backend/tests/` regression suite is green.
- [ ] Any new interactive element has a `data-testid`.
- [ ] `/app/docs/*.md` updated where relevant (Project, Architecture,
      Schema, Design, Rules — as applicable).
- [ ] `/app/memory/PRD.md` has a dated entry.
- [ ] Supervisor shows both `backend` and `frontend` running.
- [ ] Deployment readiness check (`deployment_agent`) still passes.
