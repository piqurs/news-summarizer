# Design.md — News Summarizer

> UI / UX flow, design system, and *design-level* decisions. Anything that
> touches user-visible behaviour or visual output must respect this file.
> Anything that is not here must not be added without a decision line here.

---

## 1. Product principles

1. **One page, one job.** Paste URL, get analysis. No dashboards, no
   modals, no wizards.
2. **The article result is the hero.** Everything else (Recent News,
   About, Footer) sits below and never competes.
3. **AI cost is a design constraint.** Latest Updates is behind a button.
   Translations are on demand. Cache hits look identical to fresh results
   so users never feel penalised for revisiting.
4. **Serious but not stiff.** Linear / Vercel / Stripe / Notion — precise
   type, generous negative space, one dominant hue, subtle motion.
5. **Legible before pretty.** Long-form analysis is the payload — the
   design must make walls of text scannable, never decorative.

## 2. Information architecture

```
Home page (single route)
├─ Navbar
│    ├─ Brand (News Summarizer wordmark + monogram)
│    ├─ Home · About links (in-page anchors)
│    └─ Theme toggle
├─ Hero
│    ├─ Two-line H1 headline
│    ├─ One-paragraph subhead
│    ├─ URL input + Summarize CTA
│    └─ Meta strip:
│         "Learn more →  ·  10 structured sections  ·  PDF · DOCX · Share
│          ·  Cached for 12h  ·  N / N summaries left this hour"
├─ Result card (appears above Recent News after a successful summarize)
│    ├─ Article header — title, category chip, meta line
│    ├─ Language toggle (ID ↔ EN)
│    ├─ Export bar (Copy · PDF · DOCX · Share)
│    ├─ 1  Executive Summary
│    ├─ 2  Key Points
│    ├─ 3  Main Issue
│    ├─ 4  Root Cause
│    ├─ 5  Sentiment & Bias Analysis  (AI-generated label)
│    ├─ 6  Recommended Actions        (AI-generated label)
│    ├─ 7  Latest Updates             (deferred — see §5)
│    ├─ 8  5W1H
│    ├─ 9  References
│    └─ 10 Confidence badge
├─ Recent News · last 12 h
│    Grid of 6 cards, each cached summary; click to load full result.
├─ About
│    Bento of feature cards.
└─ Footer
     GitHub · LinkedIn · Instagram
```

## 3. Design system

### 3.1 Typography

| Role          | Font                    | Size (desktop)              | Weight |
| ------------- | ----------------------- | --------------------------- | ------ |
| Display / H1  | Cabinet Grotesk         | text-4xl → text-6xl         | 700    |
| H2 section    | Cabinet Grotesk         | text-lg (mobile: text-base) | 600    |
| Body          | IBM Plex Sans           | text-base (mobile: text-sm) | 400    |
| Mono / meta   | IBM Plex Mono (aliased) | text-xs → text-sm           | 400    |
| Eyebrow label | IBM Plex Mono uppercase | text-[11px] tracking-[0.14em]| 500   |

Utilities in `index.css`: `.font-mono-alt`, `.label-eyebrow`, `.surface-card`,
`.btn-secondary`.

### 3.2 Colour tokens (CSS variables)

Palette is intentionally muted mono with one blue accent. Do not
introduce a second hue.

- Background / surface: near-white in light, near-black in dark.
- Text: high-contrast neutral gray scale.
- Accent (a): subtle blue used sparingly for CTAs and confidence "High".
- Semantic: sonner toasts pick up variant colours; do not override.

### 3.3 Spacing & rhythm

- Baseline: Tailwind default (4 px scale).
- 2–3× more vertical spacing between sections than "feels comfortable" —
  the result card breathes.
- Section header pattern: `label-eyebrow` (small caps meta) above the
  `H2` on its own line.

### 3.4 Motion

- framer-motion for entrance animations only. No hover-scale springs, no
  parallax.
- Staggered reveal for section cards on first render (delay by index).
- Reduced-motion respected via `prefers-reduced-motion`.

### 3.5 Iconography

- **lucide-react only.** No inline SVG icon dumps, no emoji as icons.
- Consistent 16 px inside labels / meta, 20 px inside buttons.

### 3.6 Components (owned)

Every component in `frontend/src/components/*.jsx` is owned by this
codebase and free to edit. Everything under `components/ui/*` is
shadcn/ui — **do not modify**. Use as-is, wrap if extension is needed.

Owned components and their single responsibility:

| Component            | Responsibility                                      |
| -------------------- | --------------------------------------------------- |
| `Navbar.jsx`         | Brand + in-page anchors + theme toggle              |
| `Hero.jsx`           | URL input, submit, meta strip                       |
| `LoadingState.jsx`   | Animated status while the summarize call is in-flight |
| `ResultCard.jsx`     | Renders the 9 sections (source of truth for order)  |
| `LanguageToggle.jsx` | EN ↔ ID switch driving `/api/translate`             |
| `ExportShare.jsx`    | Copy · PDF · DOCX · native Share                    |
| `LatestUpdates.jsx`  | Deferred button + timeline renderer                 |
| `SentimentBias.jsx`  | Section 5 — tone chip + bias indicator list         |
| `ConfidenceBadge.jsx`| Section 10 — level pill + reason text               |
| `RecentNews.jsx`     | Feed of cached summaries (last 12 h)                |
| `About.jsx`          | Bento of feature cards                              |
| `Footer.jsx`         | Social links                                        |
| `ThemeToggle.jsx`    | Uses `next-themes`                                  |

## 4. Result-card section pattern

Every section in the result card follows the same shape:

```
┌ Eyebrow label ─── e.g. "01 · EXECUTIVE SUMMARY" ────────────────────┐
│ H2 title in Cabinet Grotesk                                         │
│ ─────────────────────────────────────────────────────────────────── │
│ Content:                                                            │
│   - paragraph, list, or grid — depending on section                 │
│   - never more than one type of content variety per section         │
│ (optional) AI-generated caveat, small mono meta                     │
└──────────────────────────────────────────────────────────────────────┘
```

## 5. Latest Updates — UX rules

Section 7 is deferred behind a button. When clicked:

1. Show a small progress indicator ("Menganalisis update terbaru…") —
   the pipeline can take 15–45 s.
2. On success, render:
   - Overview paragraph.
   - Chronological timeline — cards with a **date badge** ("22 Jul 2026")
     and the event sentence.
   - Sources chips per timeline entry.
   - Optional "Situasi Terkini" and "Dampak Pasar" paragraphs.
   - Confidence badge for the update (independent of the main
     Confidence section).
3. Never render "Unknown Date". Backend already drops undated events;
   frontend must not fabricate them either.
4. Never render `has_update:false` with a non-empty timeline. Backend
   already enforces this; frontend must not diverge.
5. Retry / refresh: the primary click sends `force_refresh=false` and
   uses the cache. A user visible retry (if added) should send
   `force_refresh=true`.

## 6. Language, tone, copy

- **Default output language: Bahasa Indonesia.** Every user-facing
  string generated by the LLM MUST be Indonesian.
- Two enums stay in English literal form even in Indonesian output:
  - `confidence.level`: `High | Medium | Low`
  - `sentiment_and_bias.tone`: `Positive | Neutral | Negative | Mixed`
  These map directly to UI badges — do not translate them.
- UI chrome copy (buttons, labels, tooltips) may be English or
  Indonesian; be consistent per component.
- No exclamation marks in generated content. No emoji.

## 7. Accessibility

- Semantic landmarks (`<header>`, `<main>`, `<footer>`).
- Buttons are `<button>` — never a `<div onClick>`.
- Focus rings visible on every interactive element (default Tailwind
  focus-visible ring, not removed).
- All colour combinations pass WCAG AA in both themes.
- Keyboard: Tab order follows visual reading order. Enter submits Hero
  form.
- `data-testid` is required on every interactive element (see §9).

## 8. Responsive breakpoints

| Breakpoint | Tailwind | Behaviour                                     |
| ---------- | -------- | --------------------------------------------- |
| ≥1280 px   | xl       | Result card ~880 px, comfortable margins      |
| ≥768 px    | md       | Two-column bento in About                     |
| Base       | mobile   | Single column, larger tap targets, smaller H1 |

## 9. `data-testid` contract

Every interactive element and every element rendering user-critical text
carries a stable, kebab-case `data-testid`. Naming:

- Kebab-case, action-oriented: `summarize-btn`, `latest-updates-btn`.
- One test id per element, never duplicated on the page.
- Registry lives in `frontend/src/constants/testIds/` — reuse existing
  keys before inventing new ones.

## 10. Loading & error states

- Loading state uses `LoadingState.jsx` — a rotating status message on
  a subtle skeleton. Never a bare spinner.
- Errors surface via `sonner` toast with a plain-language message.
- 429 rate-limit errors show the retry_after minutes explicitly.
- 502 / 503 (LLM or Tavily down) show "AI service is temporarily
  unavailable" — never a stack trace.

## 11. Technical design decisions

### 11.1 Client-side exports
PDF and DOCX are generated in the browser (`jsPDF`, `docx`). No server
storage of exports, no signed URLs, no email-me-the-file flow.

### 11.2 Theme via next-themes
Single provider in `lib/theme.jsx`. All colour tokens flip via CSS
variables. No component reads the current theme; they read tokens.

### 11.3 One canonical `api.js` client
`frontend/src/lib/api.js` is the only place that constructs axios
instances or reads `REACT_APP_BACKEND_URL`. Components import functions,
not axios directly.

### 11.4 Motion is decoration, not chrome
No motion is ever load-bearing. Removing framer-motion must not break
functionality.

### 11.5 No AI-generated imagery
The product surface is textual. Do not add generated images to the
result card without a scope change.
