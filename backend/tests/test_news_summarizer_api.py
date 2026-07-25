"""End-to-end backend tests for the News Summarizer API.

Structure keeps rate-limit sensitive checks in a single file so pytest-xdist
(--dist loadscope) pins them all to the same worker => sequential execution
with predictable rate-limit budget consumption.

Reset behaviour: conftest.py wipes rate_limit + summary_cache once per session.
"""
from __future__ import annotations

import time
import pytest


# --- 1. Health -------------------------------------------------------------


class TestHealth:
    def test_health(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/health", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("status") == "ok"
        assert "time" in data


# --- 2. URL validation (no rate-limit consumed - 400 short-circuits) --------


class TestUrlValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "not-a-url",
            "ftp://example.com/article",
            "http://localhost:3000/article",
            "https://example.com/login",
            "https://cnn.com/story.pdf",
            "https://reuters.com/image.jpg",
        ],
    )
    def test_invalid_urls_return_400(self, api_client, base_url, url):
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": url},
            timeout=15,
        )
        assert r.status_code == 400, f"URL {url!r} did not 400: {r.status_code} {r.text}"
        detail = r.json().get("detail", "")
        assert "valid news article URL" in detail, detail


# --- 3. Summarize + Cache + Normalization -----------------------------------

ARTICLE_URL = (
    "https://techcrunch.com/2026/07/24/spacex-launches-new-v3-starlink-"
    "satellites-but-suffers-another-booster-failure/"
)


class TestSummarizeCache:
    """One expensive Claude call, then repeatedly hits the cache."""

    _shared: dict = {}  # holds cross-test state (summary from first call)

    def test_a_first_call_returns_fresh_summary(self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": ARTICLE_URL},
            timeout=180,
        )
        # If the domain blocks extraction we consider that a legitimate 422
        # for THIS test we require a full 200 (Techcrunch usually works).
        assert r.status_code == 200, f"summarize failed: {r.status_code} {r.text[:500]}"
        body = r.json()
        assert body["cached"] is False, "First call must not be cached"
        summary = body["summary"]

        # Article envelope
        article = summary["article"]
        for k in (
            "title", "category", "original_url", "publication_date",
            "generated_at", "reading_time_minutes", "site_name",
        ):
            assert k in article, f"missing article.{k}"

        # Core sections
        assert isinstance(summary["executive_summary"], str)
        assert 50 < len(summary["executive_summary"]) < 3000

        assert isinstance(summary["key_points"], list)
        assert len(summary["key_points"]) == 5, \
            f"expected 5 key_points, got {len(summary['key_points'])}"

        for section in ("main_issue", "root_cause", "recommended_actions",
                        "five_w_one_h", "confidence_level"):
            assert section in summary, f"missing section: {section}"

        # main_issue shape
        assert "summary" in summary["main_issue"]
        assert "significance" in summary["main_issue"]

        # root_cause shape
        assert "causes" in summary["root_cause"]
        assert isinstance(summary["root_cause"]["causes"], list)

        # recommended_actions shape
        ra = summary["recommended_actions"]
        for bucket in ("immediate", "short_term", "long_term"):
            assert bucket in ra
            assert isinstance(ra[bucket], list)
        assert "disclaimer" in ra

        # 5W1H
        w = summary["five_w_one_h"]
        for k in ("who", "what", "when", "where", "why", "how"):
            assert k in w, f"missing 5W1H.{k}"

        # references
        assert isinstance(summary["references"], list)
        assert len(summary["references"]) >= 1

        # confidence
        assert summary["confidence_level"]["level"] in ("High", "Medium", "Low")

        self._shared["summary"] = summary

    def test_b_same_url_hits_cache(self, api_client, base_url):
        # Second call must be cached=True
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": ARTICLE_URL},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cached"] is True, "Second identical call must be cached"

    def test_c_url_normalization_hits_same_cache(self, api_client, base_url):
        """UTM + trailing slash + upper-case host must resolve to same cache row."""
        variant = (
            "https://TechCrunch.com/2026/07/24/spacex-launches-new-v3-starlink-"
            "satellites-but-suffers-another-booster-failure/"
            "?utm_source=xyz&utm_medium=email&fbclid=abc"
        )
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": variant},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cached"] is True, (
            "URL normalization should have hit the existing cache entry "
            f"but got cached={body['cached']}"
        )


# --- 4. Summarize rate limit ------------------------------------------------


class TestSummarizeRateLimit:
    """Consume the remaining 4 summarize slots with fake URLs that pass
    `looks_like_article_url` but fail extraction (422). Rate-limit is charged
    BEFORE extraction, so 422s still burn the budget. After 5 non-cached
    hits within the hour, the 6th must return 429."""

    FAKE_URLS = [
        f"https://httpbin.org/status/404?slot={i}"
        for i in range(1, 6)  # 5 URLs => used to force 4 more consumed + 1 for 429
    ]

    def test_a_consume_remaining_budget(self, api_client, base_url):
        # After TestSummarizeCache: 1 slot consumed (fresh) — 4 remain.
        results = []
        for u in self.FAKE_URLS[:4]:
            r = api_client.post(
                f"{base_url}/api/summarize",
                json={"url": u},
                timeout=60,
            )
            results.append(r.status_code)
        # Each should be 4xx/5xx from extraction failure but NOT 429
        for sc in results:
            assert sc != 429, f"unexpected early 429 while consuming budget: {results}"
        # Extraction fails => 422 (or 502)
        for sc in results:
            assert sc in (400, 422, 502), (
                f"unexpected status while consuming budget: {results}"
            )

    def test_b_sixth_call_returns_429(self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/summarize",
            json={"url": self.FAKE_URLS[4]},  # 6th call total
            timeout=30,
        )
        assert r.status_code == 429, (
            f"expected 429 rate-limit, got {r.status_code}: {r.text[:300]}"
        )
        detail = r.json().get("detail", "")
        assert "Rate limit" in detail, detail


# --- 5. Latest updates ------------------------------------------------------


class TestLatestUpdates:

    def test_a_returns_updates_list(self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/latest-updates",
            json={"topic": "SpaceX Starship test flight"},
            timeout=60,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
        body = r.json()
        assert "updates" in body
        updates = body["updates"]
        assert isinstance(updates, list)
        # Tavily should return >=1 item for such a well-covered topic
        assert len(updates) >= 1, "Tavily returned no updates for SpaceX Starship"

        # Each item shape
        for u in updates:
            for k in ("date", "source", "title", "summary", "url"):
                assert k in u, f"latest-update missing field: {k}"

        # De-dup by URL
        urls = [u["url"] for u in updates]
        assert len(urls) == len(set(urls)), "duplicate URLs in updates"

        # Sorted newest first: items with parseable dates should come before None
        # Simple check: if there's any parseable date, ensure ordering roughly holds.
        from email.utils import parsedate_to_datetime
        from datetime import datetime

        def _dt(s):
            if not s:
                return None
            try:
                return parsedate_to_datetime(s)
            except Exception:
                pass
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                return None

        parsed = [(_dt(u["date"]), i) for i, u in enumerate(updates)]
        dated = [p for p in parsed if p[0] is not None]
        # Dated entries must appear before non-dated entries
        first_undated_idx = next(
            (i for (d, i) in parsed if d is None), len(updates)
        )
        for d, i in dated:
            assert i < first_undated_idx or first_undated_idx == len(updates), (
                "undated update appeared before a dated one"
            )
        # Dated entries themselves must be newest first
        dts = [d for (d, _) in dated]
        for a, b in zip(dts, dts[1:]):
            assert a >= b, f"updates not sorted newest first: {a} < {b}"

    def test_b_rate_limit(self, api_client, base_url):
        # We already made 1 successful updates call — hit 4 more, then 6th 429.
        # Use a cheap topic that Tavily can still resolve.
        for i in range(4):
            r = api_client.post(
                f"{base_url}/api/latest-updates",
                json={"topic": f"news headlines batch {i}"},
                timeout=60,
            )
            assert r.status_code != 429, (
                f"unexpected early 429 on latest-updates iter {i}: {r.status_code}"
            )
            # brief spacing to avoid Tavily edge throttling
            time.sleep(0.5)

        # 6th call — must be 429
        r = api_client.post(
            f"{base_url}/api/latest-updates",
            json={"topic": "final overflow topic"},
            timeout=30,
        )
        assert r.status_code == 429, (
            f"expected 429, got {r.status_code}: {r.text[:200]}"
        )
        assert "Rate limit" in r.json().get("detail", "")
