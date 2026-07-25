"""Backend tests for iteration-3 changes:

1. /api/summarize now returns a `sentiment_and_bias` object with tone in
   {Positive, Neutral, Negative, Mixed}, tone_explanation string,
   bias_indicators list (may be empty), and disclaimer string.
2. References sanitiser: every entry has an http(s):// URL.
3. /api/translate target=en translates sentiment_and_bias.tone_explanation,
   each bias_indicator and disclaimer, but PRESERVES `tone` as the original
   English literal.
4. GET /api/recent?limit=6 returns items with the expected shape, newest
   first, only entries within the 24h TTL — expired rows drop out.

The AP News hub article is used as the base fixture (already cached during
prior smoke testing). Rate-limit is cleared per-module for determinism.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")

TEST_URL = "https://apnews.com/hub/artificial-intelligence"

VALID_TONES = {"Positive", "Neutral", "Negative", "Mixed"}


def _mongo():
    client = MongoClient(os.environ["MONGO_URL"])
    return client, client[os.environ["DB_NAME"]]


# ---------------------------------------------------------------------------
# Ensure fresh state for THIS module: the AP News summary must exist, no EN
# translation cached (so we exercise the fresh translate path), and rate
# limits are clean.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _reset(api_client, base_url):
    client, db = _mongo()
    db.rate_limit.delete_many({})

    # Seed the ID payload if missing.
    if db.summary_cache.count_documents({"url": TEST_URL}) == 0:
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": TEST_URL},
            timeout=180,
        )
        assert r.status_code == 200, f"seed summarize failed: {r.text[:500]}"

    # Wipe the EN translation of ONLY this URL so the fresh translate path
    # runs deterministically for this module.
    db.summary_cache.update_one(
        {"url": TEST_URL},
        {"$unset": {"translations.en": ""}},
    )
    db.rate_limit.delete_many({})
    yield
    client.close()


# ---------------------------------------------------------------------------
# 1. /api/summarize returns valid sentiment_and_bias + sanitised references.
# ---------------------------------------------------------------------------
class TestSummarizeSentimentAndReferences:

    def test_summarize_returns_valid_sentiment_and_bias(
            self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": TEST_URL},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        summary = body["summary"]

        assert "sentiment_and_bias" in summary, "missing sentiment_and_bias"
        sab = summary["sentiment_and_bias"]

        # tone MUST be one of the 4 English literals
        assert sab.get("tone") in VALID_TONES, (
            f"tone must be one of {VALID_TONES}, got {sab.get('tone')!r}"
        )

        # tone_explanation is a non-empty string (may be Indonesian)
        assert isinstance(sab.get("tone_explanation"), str), \
            "tone_explanation must be a string"
        assert len(sab["tone_explanation"].strip()) > 0, \
            "tone_explanation must not be empty"

        # bias_indicators is a list (possibly empty)
        assert isinstance(sab.get("bias_indicators"), list), \
            "bias_indicators must be a list"
        for it in sab["bias_indicators"]:
            assert isinstance(it, str) and it.strip(), \
                "each bias_indicator must be a non-empty string"

        # disclaimer present, non-empty string
        assert isinstance(sab.get("disclaimer"), str), \
            "disclaimer must be a string"
        assert len(sab["disclaimer"].strip()) > 0, \
            "disclaimer must not be empty"

    def test_summarize_references_all_have_http_urls(
            self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": TEST_URL},
            timeout=60,
        )
        assert r.status_code == 200
        summary = r.json()["summary"]
        refs = summary.get("references") or []
        assert len(refs) >= 1, "must have at least one reference (the source)"
        for i, ref in enumerate(refs):
            url = ref.get("url")
            assert isinstance(url, str) and url.strip(), \
                f"references[{i}].url must be a non-empty string: {ref!r}"
            assert url.lower().startswith(("http://", "https://")), (
                f"references[{i}].url must start with http:// or https://, "
                f"got {url!r}"
            )
            # Explicit sanity: no placeholder junk
            assert url.strip() not in ("#", "unknown", ""), \
                f"references[{i}].url is a placeholder: {url!r}"


# ---------------------------------------------------------------------------
# 2. /api/translate target=en translates sentiment fields but preserves tone.
# ---------------------------------------------------------------------------
class TestTranslateSentimentEnglish:

    def test_translate_en_preserves_tone_literal_translates_explanation(
            self, api_client, base_url):
        # 1. Get the ID (source) sentiment_and_bias for comparison.
        id_res = api_client.post(
            f"{base_url}/api/translate",
            json={"url": TEST_URL, "target": "id"},
            timeout=30,
        )
        assert id_res.status_code == 200, id_res.text[:400]
        id_sab = id_res.json()["summary"]["sentiment_and_bias"]

        # 2. Translate to EN.
        en_res = api_client.post(
            f"{base_url}/api/translate",
            json={"url": TEST_URL, "target": "en"},
            timeout=120,
        )
        assert en_res.status_code == 200, en_res.text[:500]
        en_body = en_res.json()
        assert en_body["target"] == "en"
        en_sab = en_body["summary"]["sentiment_and_bias"]

        # tone literal must remain one of the four English literals
        # and equal the ID tone (translation must not change it)
        assert en_sab.get("tone") in VALID_TONES, (
            f"EN tone must be one of {VALID_TONES}, got {en_sab.get('tone')!r}"
        )
        assert en_sab["tone"] == id_sab["tone"], (
            f"tone mutated during translation: "
            f"{id_sab['tone']!r} -> {en_sab['tone']!r}"
        )

        # tone_explanation is a string; if the ID version was populated
        # the EN version must also be populated (and typically different text
        # since it's translated — but do not force inequality because a
        # single-word explanation might survive translation intact).
        assert isinstance(en_sab.get("tone_explanation"), str)
        if id_sab.get("tone_explanation", "").strip():
            assert len(en_sab["tone_explanation"].strip()) > 0, \
                "EN tone_explanation must not be empty when ID has one"

        # bias_indicators shape preserved (list of strings; same length)
        assert isinstance(en_sab.get("bias_indicators"), list)
        assert len(en_sab["bias_indicators"]) == len(id_sab["bias_indicators"]), (
            "translation must not add/remove bias_indicators"
        )
        for it in en_sab["bias_indicators"]:
            assert isinstance(it, str) and it.strip()

        # disclaimer non-empty
        assert isinstance(en_sab.get("disclaimer"), str)
        assert len(en_sab["disclaimer"].strip()) > 0

        # And the EN body of the summary itself should look English.
        exec_summary = (en_body["summary"]["executive_summary"] or "").lower()
        english_hits = sum(
            1 for w in ("the", "and", "of", "is") if f" {w} " in f" {exec_summary} "
        )
        assert english_hits >= 3, (
            f"EN executive_summary should look English: {exec_summary[:300]!r}"
        )

    def test_translate_en_references_still_all_http(self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/translate",
            json={"url": TEST_URL, "target": "en"},
            timeout=30,
        )
        assert r.status_code == 200
        refs = r.json()["summary"].get("references") or []
        assert len(refs) >= 1
        for i, ref in enumerate(refs):
            url = ref.get("url")
            assert isinstance(url, str) and url.lower().startswith(
                ("http://", "https://")
            ), f"EN references[{i}].url invalid: {url!r}"


# ---------------------------------------------------------------------------
# 3. GET /api/recent
# ---------------------------------------------------------------------------
class TestRecentEndpoint:

    def test_recent_returns_expected_shape(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/recent?limit=6", timeout=15)
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        assert body.get("limit") == 6
        assert isinstance(body.get("items"), list)
        assert len(body["items"]) >= 1, "expected at least the AP News seed"

        expected_keys = {
            "title", "category", "site_name", "generated_at",
            "url", "hash", "reading_time_minutes",
        }
        for i, it in enumerate(body["items"]):
            missing = expected_keys - set(it.keys())
            assert not missing, f"item {i} missing keys: {missing} -> {it!r}"
            assert isinstance(it["url"], str) and it["url"].lower().startswith(
                ("http://", "https://")
            ), f"item {i} url invalid: {it['url']!r}"
            assert isinstance(it["hash"], str) and len(it["hash"]) == 64, \
                f"item {i} hash must be sha256 hex"

    def test_recent_newest_first(self, api_client, base_url):
        """Inject a second (fresh) cache row with an OLDER created_at and
        verify the endpoint returns it AFTER the newer AP News row."""
        client, db = _mongo()
        try:
            older = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            db.summary_cache.update_one(
                {"hash": "TEST_HASH_ORDER_OLDER"},
                {"$set": {
                    "hash": "TEST_HASH_ORDER_OLDER",
                    "url": "https://example-news.test/older-fresh",
                    "created_at": older,
                    "payload": {
                        "article": {
                            "title": "Older but still fresh",
                            "category": "Test",
                            "site_name": "test",
                            "generated_at": older,
                            "reading_time_minutes": 2,
                            "original_url": "https://example-news.test/older-fresh",
                        },
                    },
                }},
                upsert=True,
            )
            r = api_client.get(f"{base_url}/api/recent?limit=6", timeout=15)
            assert r.status_code == 200
            items = r.json()["items"]
            titles = [it["title"] for it in items]
            # Sanity: our seeded item is present
            assert "Older but still fresh" in titles, titles
            # AP News (recently generated) should appear BEFORE the older one
            ap_idx = next(
                (i for i, it in enumerate(items) if "AP News" in (it.get("site_name") or "")
                 or "Kecerdasan" in (it.get("title") or "")
                 or "artificial-intelligence" in (it.get("url") or "")),
                None,
            )
            older_idx = titles.index("Older but still fresh")
            if ap_idx is not None:
                assert ap_idx < older_idx, (
                    f"newest-first order broken: AP idx={ap_idx}, "
                    f"older idx={older_idx}, titles={titles}"
                )
        finally:
            db.summary_cache.delete_one({"hash": "TEST_HASH_ORDER_OLDER"})
            client.close()

    def test_recent_excludes_expired_rows(self, api_client, base_url):
        """Inject a cache row 25h old and verify it is NOT returned by
        /api/recent (24h TTL). Then delete it."""
        client, db = _mongo()
        try:
            expired_at = (datetime.now(timezone.utc)
                          - timedelta(hours=25)).isoformat()
            marker_title = "TEST_EXPIRED_DO_NOT_RETURN"
            db.summary_cache.update_one(
                {"hash": "TEST_HASH_EXPIRED"},
                {"$set": {
                    "hash": "TEST_HASH_EXPIRED",
                    "url": "https://example-news.test/expired",
                    "created_at": expired_at,
                    "payload": {
                        "article": {
                            "title": marker_title,
                            "category": "Test",
                            "site_name": "test",
                            "generated_at": expired_at,
                            "reading_time_minutes": 2,
                            "original_url": "https://example-news.test/expired",
                        },
                    },
                }},
                upsert=True,
            )

            r = api_client.get(f"{base_url}/api/recent?limit=24", timeout=15)
            assert r.status_code == 200
            titles = [it["title"] for it in r.json()["items"]]
            assert marker_title not in titles, (
                f"expired row leaked into /api/recent: {titles}"
            )
        finally:
            db.summary_cache.delete_one({"hash": "TEST_HASH_EXPIRED"})
            client.close()

    def test_recent_does_not_charge_rate_limit(self, api_client, base_url):
        """/api/recent must not consume the summarize rate budget."""
        before = api_client.get(
            f"{base_url}/api/rate-status", timeout=15
        ).json()["summarize"]["remaining"]
        # Call /recent a few times
        for _ in range(3):
            r = api_client.get(f"{base_url}/api/recent?limit=6", timeout=15)
            assert r.status_code == 200
        after = api_client.get(
            f"{base_url}/api/rate-status", timeout=15
        ).json()["summarize"]["remaining"]
        assert before == after, (
            f"/api/recent unexpectedly consumed summarize budget: "
            f"{before} -> {after}"
        )
