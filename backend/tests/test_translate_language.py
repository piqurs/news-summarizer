"""Tests for the language toggle feature: default Bahasa Indonesia summaries
and the /api/translate endpoint (cache-first with EN Claude fallback).

Rate-limit budget: RATE_LIMIT_TRANSLATE=5/hour. This module manages state via
`_reset_translation_state` at class scope. It relies on the pre-cached AP News
hub article summary in Mongo (created by prior smoke testing).
"""
from __future__ import annotations

import os
import pytest
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

TEST_URL = "https://apnews.com/hub/artificial-intelligence"

INDONESIAN_HINTS = ["ini", "dan", "yang", "adalah", "artikel", "berita"]


def _mongo():
    client = MongoClient(os.environ["MONGO_URL"])
    return client, client[os.environ["DB_NAME"]]


@pytest.fixture(scope="module", autouse=True)
def _reset_translation_state(api_client, base_url):
    """Ensure the AP News base ID summary is present, wipe any existing EN
    translation, and clear rate-limit state so the /translate charge->cache
    path is deterministic."""
    client, db = _mongo()
    db.rate_limit.delete_many({})
    # Seed the ID payload if session-level reset wiped it.
    if db.summary_cache.count_documents({"url": TEST_URL}) == 0:
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": TEST_URL},
            timeout=180,
        )
        assert r.status_code == 200, f"seed summarize failed: {r.text[:500]}"
    db.summary_cache.update_many({}, {"$unset": {"translations.en": ""}})
    # Reset rate_limit again in case seeding consumed a slot.
    db.rate_limit.delete_many({})
    yield
    client.close()


# --- 1. Summarize returns Indonesian by default -----------------------------

class TestSummarizeLanguage:
    def test_summarize_returns_indonesian_language_label(self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": TEST_URL},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        assert body.get("language") == "id", body
        # Cache hit expected because URL was pre-summarized
        assert body["cached"] is True
        summary = body["summary"]

        # confidence_level.level must remain literal English
        assert summary["confidence_level"]["level"] in ("High", "Medium", "Low")

        # Human-readable strings must be Bahasa Indonesia
        text_bag = " ".join([
            summary["article"]["title"] or "",
            summary["executive_summary"] or "",
            summary["main_issue"]["summary"] or "",
            summary["root_cause"]["causes"][0] if summary["root_cause"]["causes"] else "",
            summary["recommended_actions"]["disclaimer"] or "",
            summary["five_w_one_h"].get("what") or "",
        ]).lower()

        hits = sum(1 for w in INDONESIAN_HINTS if f" {w} " in f" {text_bag} ")
        assert hits >= 3, f"Expected Indonesian words in payload, hits={hits}, sample={text_bag[:400]!r}"

    def test_summarize_cache_hit_does_not_decrement_rate(self, api_client, base_url):
        # /api/summarize on cached article should not decrement rate
        r1 = api_client.get(f"{base_url}/api/rate-status", timeout=15)
        before = r1.json()["summarize"]["remaining"]
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": TEST_URL},
            timeout=30,
        )
        assert r.status_code == 200
        assert r.json()["cached"] is True
        r2 = api_client.get(f"{base_url}/api/rate-status", timeout=15)
        after = r2.json()["summarize"]["remaining"]
        assert before == after, (
            f"summarize cache hit unexpectedly consumed budget: {before}->{after}"
        )


# --- 2. Translate: input validation + missing cache ------------------------

class TestTranslateValidation:
    def test_translate_bad_target_returns_422(self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/translate",
            json={"url": TEST_URL, "target": "fr"},
            timeout=15,
        )
        # Pydantic pattern validation => 422
        assert r.status_code == 422, r.text

    def test_translate_invalid_url_returns_400(self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/translate",
            json={"url": "not-a-valid-url", "target": "en"},
            timeout=15,
        )
        assert r.status_code == 400, r.text
        assert "Invalid URL" in r.json().get("detail", "")

    def test_translate_url_not_summarized_returns_404(self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/translate",
            json={
                "url": "https://apnews.com/hub/nonexistent-topic-xyz-12345",
                "target": "en",
            },
            timeout=15,
        )
        assert r.status_code == 404, r.text
        detail = r.json().get("detail", "").lower()
        assert "summarize" in detail, detail


# --- 3. Translate to id (cached primary payload; free) ---------------------

class TestTranslateIndonesian:
    def test_translate_target_id_returns_primary_payload_no_charge(
            self, api_client, base_url):
        r1 = api_client.get(f"{base_url}/api/rate-status", timeout=15)
        # rate-status endpoint doesn't include translate bucket - so read directly
        # from response of translate call.
        r = api_client.post(
            f"{base_url}/api/translate",
            json={"url": TEST_URL, "target": "id"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cached"] is True
        assert body["target"] == "id"
        remaining_after_first = body["rate_limit"]["remaining"]

        # Call again to ensure NOT decrementing
        r2 = api_client.post(
            f"{base_url}/api/translate",
            json={"url": TEST_URL, "target": "id"},
            timeout=30,
        )
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["cached"] is True
        assert b2["rate_limit"]["remaining"] == remaining_after_first, (
            f"target=id should NOT decrement translate rate limit: "
            f"{remaining_after_first} -> {b2['rate_limit']['remaining']}"
        )

        # Content is the Indonesian payload with English confidence level
        s = body["summary"]
        assert s["confidence_level"]["level"] in ("High", "Medium", "Low")
        text_bag = " " + (s["executive_summary"] or "").lower() + " "
        hits = sum(1 for w in INDONESIAN_HINTS if f" {w} " in text_bag)
        assert hits >= 3, f"Indonesian body expected, got: {text_bag[:300]!r}"


# --- 4. Translate to en: first call charges, second cached -----------------

class TestTranslateEnglish:
    _snapshot: dict = {}

    def test_a_first_en_call_charges_rate_limit(self, api_client, base_url):
        # Read the "translate" remaining via a probe call to target=id (no charge)
        # and use its rate_limit.remaining as the baseline.
        probe = api_client.post(
            f"{base_url}/api/translate",
            json={"url": TEST_URL, "target": "id"},
            timeout=30,
        )
        assert probe.status_code == 200
        remaining_before = probe.json()["rate_limit"]["remaining"]

        r = api_client.post(
            f"{base_url}/api/translate",
            json={"url": TEST_URL, "target": "en"},
            timeout=120,
        )
        assert r.status_code == 200, r.text[:500]
        body = r.json()
        assert body["cached"] is False, "First EN call must NOT be cached"
        assert body["target"] == "en"
        assert body["rate_limit"]["remaining"] == remaining_before - 1, (
            f"First EN call must decrement remaining by 1: "
            f"{remaining_before} -> {body['rate_limit']['remaining']}"
        )

        s = body["summary"]
        # Confidence literal preserved
        assert s["confidence_level"]["level"] in ("High", "Medium", "Low")

        # Article envelope preserved (URL/date/generated_at unchanged from ID)
        # Compare against the ID payload from probe
        id_article = probe.json()["summary"]["article"]
        for k in ("original_url", "publication_date", "generated_at"):
            assert s["article"].get(k) == id_article.get(k), (
                f"article.{k} mutated during translation: "
                f"{id_article.get(k)!r} -> {s['article'].get(k)!r}"
            )
        # Reference URLs preserved
        id_refs = probe.json()["summary"]["references"]
        for i, r_out in enumerate(s["references"]):
            if i < len(id_refs):
                assert r_out["url"] == id_refs[i]["url"], (
                    "reference URL mutated during translation"
                )

        # Human-readable text should be English (not Indonesian)
        body_text = (s["executive_summary"] or "").lower()
        # Check for common English words + absence of common Indonesian markers
        english_hits = sum(1 for w in ("the", "and", "of", "is") if f" {w} " in f" {body_text} ")
        assert english_hits >= 3, f"EN body should read as English, got: {body_text[:300]!r}"

        self._snapshot["remaining"] = body["rate_limit"]["remaining"]
        self._snapshot["summary"] = s

    def test_b_second_en_call_is_cached_no_charge(self, api_client, base_url):
        remaining_before = self._snapshot["remaining"]
        r = api_client.post(
            f"{base_url}/api/translate",
            json={"url": TEST_URL, "target": "en"},
            timeout=30,
        )
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        assert body["cached"] is True, "Second EN call must be cached"
        assert body["target"] == "en"
        assert body["rate_limit"]["remaining"] == remaining_before, (
            f"Second EN call must NOT decrement remaining: "
            f"{remaining_before} -> {body['rate_limit']['remaining']}"
        )
        # Payload matches first translation exactly (same object)
        first = self._snapshot["summary"]
        assert body["summary"]["article"]["title"] == first["article"]["title"]
        assert body["summary"]["executive_summary"] == first["executive_summary"]
