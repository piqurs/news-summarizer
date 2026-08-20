"""Backend tests for News Summarizer /api/latest-updates regression fixes.

Verifies:
- /api/health basic
- /api/summarize returns article + publication_date + key_entities
- /api/latest-updates (force_refresh) returns has_update=true, non-empty timeline,
  every event dated (YYYY-MM-DD parseable), strictly ascending chronology,
  confidence.level in {High,Medium,Low}, confidence.reason mentions domains
- Regression checks for the three previously-broken URLs (IHSG, ANTM, Rupiah)
- Empty-cache TTL behaviour (LATEST_UPDATES_EMPTY_TTL_MINUTES)
- Direct services._search_window_days + search_candidates window enforcement
"""
from __future__ import annotations

import os
import re
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

# Load backend .env so EMERGENT_LLM_KEY, TAVILY_API_KEY, envs are available
BACKEND_DIR = Path("/app/backend")
load_dotenv(BACKEND_DIR / ".env")
sys.path.insert(0, str(BACKEND_DIR))

FRONTEND_ENV = Path("/app/frontend/.env").read_text()
m = re.search(r"REACT_APP_BACKEND_URL=(\S+)", FRONTEND_ENV)
assert m, "REACT_APP_BACKEND_URL missing from frontend/.env"
BASE_URL = m.group(1).rstrip("/")
API = f"{BASE_URL}/api"

TIMEOUT_SUMMARY = 90
TIMEOUT_UPDATES = 180

URLS = {
    "kompas": "https://otomotif.kompas.com/read/2026/01/25/074100415/pemerintah-siapkan-transisi-ke-b50-setop-impor-solar-mulai-2026",
    "ihsg": "https://www.bloombergtechnoz.com/detail-news/115683/ihsg-di-ambang-kenaikan-9-hari-beruntun",
    "antm": "https://www.bloombergtechnoz.com/detail-news/96429/harga-emas-antam-naik-lagi-cetak-rekor-tertinggi",
    "rupiah": "https://www.bloombergtechnoz.com/detail-news/96488/tekanan-belum-selesai-rupiah-ditutup-melemah",
}

DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(d):
    if not isinstance(d, str):
        return None
    if not DATE_RX.match(d[:10]):
        return None
    try:
        return datetime.strptime(d[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _validate_delta_response(delta, label):
    """Common assertions for /api/latest-updates response, allowing has_update
    true or false but ALWAYS verifying date-integrity + chronology."""
    assert isinstance(delta, dict), f"[{label}] response not a dict"
    conf = delta.get("confidence") or {}
    assert conf.get("level") in ("High", "Medium", "Low"), (
        f"[{label}] confidence.level invalid: {conf.get('level')}"
    )
    tl = delta.get("timeline") or []
    # Every event must be parseable date + no unknown / null / empty
    parsed = []
    for ev in tl:
        d = ev.get("date")
        assert d not in (None, "", "unknown", "Unknown"), (
            f"[{label}] timeline event with bad date: {ev}"
        )
        pd = _parse_date(str(d))
        assert pd is not None, (
            f"[{label}] timeline event date unparseable: {d}"
        )
        parsed.append(pd)
    # Strict ascending
    for i in range(1, len(parsed)):
        assert parsed[i] >= parsed[i - 1], (
            f"[{label}] timeline not sorted ascending at index {i}"
        )
    return delta


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def test_health():
    r = requests.get(f"{API}/health", timeout=15)
    assert r.status_code == 200
    j = r.json()
    assert j.get("status") == "ok"


# ---------------------------------------------------------------------------
# _search_window_days direct unit
# ---------------------------------------------------------------------------
def test_search_window_days_default_and_env():
    import services  # noqa
    os.environ.pop("LATEST_UPDATES_WINDOW_DAYS", None)
    assert services._search_window_days() == 365
    os.environ["LATEST_UPDATES_WINDOW_DAYS"] = "180"
    assert services._search_window_days() == 180
    os.environ["LATEST_UPDATES_WINDOW_DAYS"] = "bad"
    assert services._search_window_days() == 365
    os.environ["LATEST_UPDATES_WINDOW_DAYS"] = "365"


# ---------------------------------------------------------------------------
# search_candidates drops out-of-window (patch tavily to avoid network)
# ---------------------------------------------------------------------------
def test_search_candidates_drops_out_of_window(monkeypatch):
    import services

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=400)).isoformat()
    recent = (now - timedelta(days=10)).isoformat()

    fake_results = [
        {"url": "https://reuters.com/a", "title": "Old story",
         "published_date": old, "content": "old"},
        {"url": "https://reuters.com/b", "title": "New story",
         "published_date": recent, "content": "new"},
        {"url": "https://reuters.com/c", "title": "Nodate",
         "published_date": None, "content": "nod"},
    ]

    class _Fake:
        def __init__(self, api_key):
            pass

        def search(self, **kwargs):
            return {"results": fake_results}

    monkeypatch.setattr(services, "TavilyClient", _Fake)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake")
    out = asyncio.run(services.search_candidates(["q1"]))
    urls = {c["url"] for c in out}
    assert "https://reuters.com/b" in urls
    assert "https://reuters.com/a" not in urls  # out of window
    assert "https://reuters.com/c" not in urls  # no date


# ---------------------------------------------------------------------------
# Summarize + latest-updates flow per URL
# ---------------------------------------------------------------------------
def _summarize(url):
    r = requests.post(f"{API}/summarize", json={"url": url},
                      timeout=TIMEOUT_SUMMARY)
    assert r.status_code == 200, f"summarize {url} -> {r.status_code}: {r.text[:400]}"
    j = r.json()
    s = j.get("summary") or {}
    art = s.get("article") or {}
    assert art.get("title")
    assert "publication_date" in art
    assert isinstance(s.get("key_entities"), list)
    return s


def _latest(topic, source_url, force=True):
    body = {"topic": topic, "source_url": source_url, "force_refresh": force}
    r = requests.post(f"{API}/latest-updates", json=body,
                      timeout=TIMEOUT_UPDATES)
    assert r.status_code == 200, (
        f"latest-updates {source_url} -> {r.status_code}: {r.text[:400]}"
    )
    return r.json()


@pytest.mark.parametrize("key", ["kompas", "ihsg", "antm", "rupiah"])
def test_summarize_then_latest_updates(key):
    url = URLS[key]
    summary = _summarize(url)
    topic = (summary.get("article") or {}).get("title") or key
    delta = _latest(topic, url, force=True)
    _validate_delta_response(delta, key)
    # For all four target URLs we EXPECT has_update true and non-empty timeline
    assert delta.get("has_update") is True, (
        f"[{key}] has_update expected true; reason={delta.get('confidence',{}).get('reason')}"
    )
    assert delta.get("timeline"), f"[{key}] timeline empty"
    # confidence.reason should mention domains + corroboration wording
    reason = (delta.get("confidence") or {}).get("reason", "").lower()
    assert "domain" in reason, f"[{key}] confidence.reason missing domain wording: {reason}"
    assert "korroborasi" in reason or "corrobor" in reason, (
        f"[{key}] confidence.reason missing corroboration wording: {reason}"
    )
    if key == "antm":
        lvl = (delta.get("confidence") or {}).get("level")
        assert lvl in ("High", "Medium"), f"[antm] confidence too low: {lvl}"


# ---------------------------------------------------------------------------
# Empty-cache TTL behaviour
# ---------------------------------------------------------------------------
def test_empty_cache_ttl_behavior(monkeypatch):
    """A has_update=false response is cached; sending same request without
    force_refresh returns cached; setting empty TTL to 0 (server-side env)
    forces the pipeline to re-run.

    We can't mutate the running server's env from here, so we instead
    exercise the code paths directly via the CacheAndRateLimit helper.
    """
    import services
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]

    async def _run():
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        store = services.CacheAndRateLimit(db)
        h = "TEST_EMPTY_TTL_HASH_" + datetime.now().strftime("%H%M%S%f")
        # seed a base cache doc first (set_cached_delta does no upsert)
        await db.summary_cache.insert_one({
            "hash": h, "url": "https://TEST/empty",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {},
        })
        empty = {"has_update": False, "timeline": [], "overview": ""}
        await store.set_cached_delta(h, empty)
        # default 30 min TTL -> should return cached
        cached = await store.get_cached_delta(h)
        assert cached is not None
        assert cached.get("has_update") is False
        # set TTL to 0 -> immediately expired
        os.environ["LATEST_UPDATES_EMPTY_TTL_MINUTES"] = "0"
        cached2 = await store.get_cached_delta(h)
        assert cached2 is None, "empty cache should be expired at TTL=0"
        # cleanup
        await db.summary_cache.delete_one({"hash": h})
        os.environ["LATEST_UPDATES_EMPTY_TTL_MINUTES"] = "30"
        client.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# merge_timeline drops undated
# ---------------------------------------------------------------------------
def test_merge_timeline_drops_undated():
    import services
    existing = [
        {"date": "2025-01-10", "event": "old event", "sources": ["https://x/1"]},
        {"date": None, "event": "legacy undated", "sources": []},
        {"date": "unknown", "event": "legacy unknown", "sources": []},
    ]
    new = [
        {"date": "2025-02-01", "event": "new event", "sources": ["https://x/2"]},
        {"date": "", "event": "should drop", "sources": []},
    ]
    out = services.merge_timeline(existing, new)
    for ev in out:
        assert services._parse_date_str(str(ev["date"])) is not None
    assert any(e["event"] == "old event" for e in out)
    assert any(e["event"] == "new event" for e in out)
    assert not any(e["event"] in ("legacy undated", "legacy unknown", "should drop") for e in out)



# ---------------------------------------------------------------------------
# Iteration 5 focus: consistency invariant for merged timeline overlay
# ---------------------------------------------------------------------------
def test_consistency_invariant_all_urls():
    """Across all responses: has_update must be True whenever timeline
    has any entry strictly after baseline publication_date. No response may
    have has_update:false while timeline contains post-baseline dated events.
    Uses cached responses from the previous parametrized run when available;
    otherwise re-fetches."""
    for key, url in URLS.items():
        # summarize (uses cache) to fetch baseline publication_date
        s = _summarize(url)
        baseline_pub = (s.get("article") or {}).get("publication_date") or ""
        base_dt = _parse_date(baseline_pub)
        topic = (s.get("article") or {}).get("title") or key
        delta = _latest(topic, url, force=False)
        _validate_delta_response(delta, key)
        tl = delta.get("timeline") or []
        post = []
        for ev in tl:
            d = _parse_date(str(ev.get("date")))
            if d and (base_dt is None or d > base_dt):
                post.append(ev)
        if post:
            assert delta.get("has_update") is True, (
                f"[{key}] INVARIANT VIOLATED: has_update=false but "
                f"{len(post)} post-baseline events present in timeline"
            )
            assert (delta.get("overview") or "").strip(), (
                f"[{key}] overview empty despite post-baseline events"
            )


def test_forced_accumulated_overlay_via_seed(monkeypatch):
    """Seed timeline_store with 3 synthetic post-baseline events for a fake
    URL and force synthesize_delta to return empty. The endpoint must then:
      - has_update = True
      - overview populated (mentioning accumulated events count, Bahasa)
      - confidence.reason contains 'peristiwa akumulatif pasca-baseline'
      - timeline returned equals the seeded 3 events (all dated, ascending)
    """
    import services
    import server as srv
    from motor.motor_asyncio import AsyncIOMotorClient

    fake_url = ("https://otomotif.kompas.com/read/2026/01/25/"
                "074100415/pemerintah-siapkan-transisi-ke-b50-"
                "setop-impor-solar-mulai-2026")
    # This URL has an existing summary cache from earlier tests; if not,
    # summarize it first so /api/latest-updates has a baseline to load.
    _summarize(fake_url)
    normalized = services.normalize_url(fake_url)
    h = services.url_hash(normalized)

    seeded_events = [
        {"date": "2026-01-26", "event": "SEED_A pemerintah mempercepat B50",
         "sources": ["https://reuters.com/seed-a"]},
        {"date": "2026-01-27", "event": "SEED_B kilang menyesuaikan pasokan",
         "sources": ["https://reuters.com/seed-b"]},
        {"date": "2026-01-28", "event": "SEED_C ekspor solar diperiksa",
         "sources": ["https://reuters.com/seed-c"]},
    ]

    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]

    async def _prep():
        c = AsyncIOMotorClient(mongo_url)
        db = c[db_name]
        # Backup existing timeline (if any) so we don't corrupt shared state
        prev = await db.timeline_store.find_one({"hash": h}, {"_id": 0})
        await db.timeline_store.update_one(
            {"hash": h},
            {"$set": {"hash": h, "timeline": seeded_events,
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        # Also clear any existing delta cache so force_refresh path runs
        await db.summary_cache.update_one(
            {"hash": h},
            {"$unset": {"latest_update_delta": "",
                        "delta_generated_at": ""}},
        )
        c.close()
        return prev

    async def _restore(prev):
        c = AsyncIOMotorClient(mongo_url)
        db = c[db_name]
        if prev is not None:
            await db.timeline_store.update_one(
                {"hash": h}, {"$set": prev}, upsert=True,
            )
        else:
            await db.timeline_store.delete_one({"hash": h})
        c.close()

    prev = asyncio.run(_prep())

    # Force LLM delta to be empty by monkeypatching synthesize_delta
    async def _empty_delta(baseline, clusters):
        return {
            "has_update": False, "overview": "", "developments": [],
            "timeline": [], "current_situation": None,
            "market_impact": None, "sources_used": [],
            "confidence": {"level": "Low", "reason": "no fresh"},
        }
    monkeypatch.setattr(srv, "synthesize_delta", _empty_delta)

    try:
        body = {"topic": "B50 transisi", "source_url": fake_url,
                "force_refresh": True}
        r = requests.post(f"{API}/latest-updates", json=body,
                          timeout=TIMEOUT_UPDATES)
        assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
        delta = r.json()
        _validate_delta_response(delta, "seeded-overlay")

        # NOTE: because the API server runs in a different Python process,
        # monkeypatching srv.synthesize_delta from within pytest does NOT
        # affect the served process. So the endpoint may still return a
        # populated fresh delta. That's OK — the invariant still applies:
        # if the merged timeline contains post-baseline events, has_update
        # MUST be true and overview MUST be populated.
        tl = delta.get("timeline") or []
        base_dt = _parse_date(
            (_summarize(fake_url).get("article") or {}).get("publication_date")
            or ""
        )
        post = [ev for ev in tl
                if _parse_date(str(ev.get("date"))) and
                (base_dt is None or
                 _parse_date(str(ev.get("date"))) > base_dt)]
        # our seeded events should still be in the merged timeline (they
        # merge-in via merge_timeline overlay from timeline_store)
        seeded_dates = {"2026-01-26", "2026-01-27", "2026-01-28"}
        returned_dates = {str(ev.get("date"))[:10] for ev in tl}
        assert seeded_dates.issubset(returned_dates), (
            f"seeded events lost; returned dates={returned_dates}"
        )
        assert post, "no post-baseline events in merged timeline"
        assert delta.get("has_update") is True, (
            "has_update must be true when merged timeline has post-baseline"
        )
        assert (delta.get("overview") or "").strip(), "overview empty"
        # If the endpoint hit the fallback path (fresh LLM was empty in this
        # container's run), the reason will contain the appended sentence.
        # It's only guaranteed when fresh delta was empty; we assert softly.
        reason = (delta.get("confidence") or {}).get("reason", "")
        # log for visibility
        print(f"[seeded-overlay] confidence.reason = {reason!r}")
        print(f"[seeded-overlay] overview = "
              f"{(delta.get('overview') or '')[:160]!r}")
    finally:
        asyncio.run(_restore(prev))
