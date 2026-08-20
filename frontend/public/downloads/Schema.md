# Schema.md — News Summarizer

> MongoDB collections owned by the app. This is the contract between
> `backend/services.py` and the database. Field names are stable.

MongoDB is document, not relational — there are no foreign keys, no joins.
Cross-collection consistency is enforced by the `hash` field (SHA-256 of the
normalized URL, produced by `services.url_hash`).

---

## Collection: `summary_cache`

**Purpose.** Stores the AI-generated summary for a normalized article URL,
plus any on-demand translations, and the latest `latest-updates` delta.
This is the primary read-through cache. TTL is a function of
`CACHE_TTL_HOURS` (default 12 h), enforced in application code (not a
Mongo TTL index — we need to serve stale-but-known documents from
different TTL rules for different fields).

**Index.**

| Field         | Type   | Constraint |
| ------------- | ------ | ---------- |
| `hash`        | string | UNIQUE     |
| `created_at`  | date   | (secondary — for future analytics) |

**Document shape.**

```jsonc
{
  "hash": "sha256-hex-string",           // primary key
  "url": "https://example.com/article",   // normalized URL (for debug)
  "created_at": "2026-08-20T02:34:16Z",   // ISO string, drives TTL
  "payload": {                            // canonical Indonesian summary
    "article": {
      "title": "string",
      "category": "string",
      "original_url": "string",
      "publication_date": "YYYY-MM-DD | null",
      "generated_at": "ISO datetime",
      "reading_time_minutes": 3,
      "site_name": "string | null"
    },
    "executive_summary": "string",
    "key_points": ["string", "string", "string", "string", "string"],
    "key_entities": ["string"],           // used by /latest-updates
    "main_issue": {
      "summary": "string",
      "significance": "string"
    },
    "root_cause": {
      "causes": ["string"],
      "certainty_note": "string"
    },
    "recommended_actions": {
      "immediate":  ["string"],
      "short_term": ["string"],
      "long_term":  ["string"],
      "disclaimer": "string"
    },
    "sentiment_and_bias": {
      "tone": "Positive | Neutral | Negative | Mixed",  // English literal
      "tone_explanation": "string",
      "bias_indicators": ["string"],
      "disclaimer": "string"
    },
    "five_w_one_h": {
      "who": "string", "what": "string", "when": "string",
      "where": "string", "why": "string", "how": "string"
    },
    "references": [
      { "website": "string", "title": "string",
        "date": "string | null", "url": "https://..." }
    ],
    "confidence_level": {
      "level": "High | Medium | Low",     // English literal
      "reason": "string"
    }
  },

  "translations": {                       // added on demand
    "en": { /* same shape as payload */ }
    // "id" is NEVER stored under translations — payload IS the id copy
  },

  // /latest-updates cached delta (may be absent if never fetched)
  "latest_update_delta": {
    "has_update": true,
    "overview": "string (Bahasa Indonesia)",
    "developments": ["string"],
    "timeline": [
      { "date": "YYYY-MM-DD",
        "event": "string",
        "sources": ["https://..."] }
    ],
    "current_situation": "string | null",
    "market_impact": "string | null",
    "sources_used": ["https://..."],
    "confidence": {
      "level": "High | Medium | Low",
      "reason": "string",
      "score": 0..3 | null,
      "factors": { "...": "..." }
    }
  },
  "delta_generated_at": "ISO datetime"     // drives per-field TTL
}
```

**TTL rules — application-enforced.**

| Field                  | TTL                                                 |
| ---------------------- | --------------------------------------------------- |
| `payload`              | `CACHE_TTL_HOURS` (default 12 h)                    |
| `translations.<lang>`  | shares the parent document TTL                      |
| `latest_update_delta`  | `CACHE_TTL_DELTA_MINUTES` if set, else `CACHE_TTL_HOURS`; **but** `has_update:false` uses `LATEST_UPDATES_EMPTY_TTL_MINUTES` (default 30) |

**Invariants.**
- `hash` is the ONLY safe identifier — never surface `_id` to the client.
- `payload.confidence_level.level` is always one of `High | Medium | Low`
  (English literal). Do not translate.
- `payload.sentiment_and_bias.tone` is always one of
  `Positive | Neutral | Negative | Mixed` (English literal).
- Every entry in `payload.references[]` MUST have a real
  `http(s)://` URL. Sanitised server-side in `summarize_article`.

---

## Collection: `rate_limit`

**Purpose.** Sliding-hour per-IP counter for AI-charged endpoints
(`/summarize`, `/latest-updates`, `/translate`). Not related to auth —
we do not know who a user is, only their client IP (respecting
`X-Forwarded-For` at the ingress).

**Index.**

| Fields                        | Type      |
| ----------------------------- | --------- |
| `(ip, bucket, ts)`            | compound  |

**Document shape.** One document per hit, deleted lazily on peek:

```jsonc
{
  "ip":     "1.2.3.4",              // string
  "bucket": "summarize | updates | translate",
  "ts":     "2026-08-20T02:34:16Z"  // ISO string
}
```

**Behaviour.**
- On each hit, `CacheAndRateLimit.check_rate(ip, bucket, limit)` counts
  documents with `ts >= now - 1 h`. If under `limit`, inserts a new doc
  and returns `(True, remaining, 0)`. Otherwise returns
  `(False, 0, retry_after_seconds)`.
- Old rows are deleted opportunistically; there is no dedicated TTL
  index. This keeps deletion tied to the actual read path — no stale
  counters from clock skew.

---

## Collection: `timeline_store`

**Purpose.** Persistent, append-only accumulation of the Latest-Updates
timeline per article. Deliberately separate from `summary_cache.latest_update_delta`
so the timeline survives cache expiry and never gets regenerated from scratch.

**Index.**

| Field  | Type   | Constraint |
| ------ | ------ | ---------- |
| `hash` | string | UNIQUE     |

**Document shape.**

```jsonc
{
  "hash":       "sha256-hex-string",         // matches summary_cache.hash
  "timeline":   [
    { "date":    "YYYY-MM-DD",                // MANDATORY, parseable
      "event":   "string",
      "sources": ["https://..."] }
  ],
  "updated_at": "ISO datetime"
}
```

**Invariants.**
- **No TTL.** Timelines persist forever until explicitly cleared.
- **Every entry has a parseable date.** `CacheAndRateLimit.get_timeline()`
  strips legacy undated rows on read. Never surface "Unknown Date".
- `merge_timeline()` is the ONLY write path — no direct upserts of
  arbitrary events.
- Timeline **only grows.** If a merge would produce an empty timeline
  where a non-empty one existed, that is a bug — investigate.

---

## Cross-collection lookups

The endpoint contract:

```
/api/summarize        writes summary_cache.payload
                      writes rate_limit         (unless cache hit)

/api/translate        writes summary_cache.translations.<lang>
                      writes rate_limit         (unless cache hit)

/api/latest-updates   reads  summary_cache.payload  (baseline)
                      reads  timeline_store         (accumulated)
                      writes summary_cache.latest_update_delta
                      writes timeline_store         (merged)
                      never writes rate_limit right now
                      (soft-disabled — see server.py lines 245-247)

/api/recent           reads  summary_cache (12 h TTL, sorted desc)
                      never writes anything

/api/rate-status      reads  rate_limit (peek — non-mutating)
```

## Backup / restore

- Emergent-managed MongoDB provides snapshots. The application itself
  has no `mongodump` scripts and no fixture data — a fresh DB is a
  perfectly valid starting state.
- If restoring into an environment with an older schema:
  1. `db.summary_cache.updateMany({}, {$unset: {latest_update_delta: "", delta_generated_at: ""}})`
     to force fresh delta computation.
  2. `db.timeline_store.deleteMany({})` if timelines might contain
     undated rows from before the "no unknown date" rule.
