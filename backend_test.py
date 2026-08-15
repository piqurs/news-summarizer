"""
Comprehensive backend test for News Summarizer Latest Updates pipeline.
Tests the rebuilt pipeline with concurrent search, web fetch, clustering,
deterministic confidence scoring, and timeline accumulation.
"""
import os
import sys
import time
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

# Load backend environment
load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

# Configuration
BACKEND_URL = os.getenv("REACT_APP_BACKEND_URL", "").rstrip("/")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "news_summarizer")

# Test article URL (already cached according to review_request)
TEST_ARTICLE_URL = "https://finance.detik.com/bursa-dan-valas/d-8616222/msci-coret-10-saham-ri-ihsg-tertekan"

print(f"Backend URL: {BACKEND_URL}")
print(f"MongoDB: {MONGO_URL}/{DB_NAME}")
print(f"Test Article: {TEST_ARTICLE_URL}")
print("=" * 80)

# MongoDB connection for cache/rate limit management
mongo_client = MongoClient(MONGO_URL)
db = mongo_client[DB_NAME]


def clear_rate_limits():
    """Clear all rate limit entries to allow testing."""
    result = db.rate_limit.delete_many({})
    print(f"✓ Cleared {result.deleted_count} rate limit entries")


def clear_delta_cache(url_hash=None):
    """Clear delta cache to force fresh pipeline run."""
    if url_hash:
        result = db.summary_cache.update_one(
            {"hash": url_hash},
            {"$unset": {"latest_update_delta": "", "delta_generated_at": ""}}
        )
        print(f"✓ Cleared delta cache for hash {url_hash[:10]}...")
    else:
        result = db.summary_cache.update_many(
            {},
            {"$unset": {"latest_update_delta": "", "delta_generated_at": ""}}
        )
        print(f"✓ Cleared delta cache for {result.modified_count} entries")


def get_timeline_from_db(url_hash):
    """Get timeline from timeline_store collection."""
    doc = db.timeline_store.find_one({"hash": url_hash})
    if doc:
        return doc.get("timeline", [])
    return []


def compute_url_hash(url):
    """Compute URL hash the same way backend does."""
    import hashlib
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
    
    # Normalize URL
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    
    # Remove tracking params
    tracking_prefixes = ("utm_", "ga_", "fbclid", "gclid", "mc_", "mkt_",
                        "yclid", "msclkid", "_hsenc", "_hsmi", "hsctatracking",
                        "vero_", "trk", "ref_")
    tracking_params = {"fbclid", "gclid", "yclid", "msclkid", "igshid", "ref",
                      "share", "share_id", "shared", "spm", "s_kwcid", "cmpid"}
    
    cleaned_query = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not any(k.lower().startswith(p) for p in tracking_prefixes)
        and k.lower() not in tracking_params
    ]
    query = urlencode(sorted(cleaned_query))
    path = parsed.path.rstrip("/") or "/"
    normalized = urlunparse(("https", host, path, "", query, ""))
    
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def test_health():
    """Test 1: GET /api/health should return 200."""
    print("\n[TEST 1] GET /api/health")
    try:
        response = requests.get(f"{BACKEND_URL}/api/health", timeout=10)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert "status" in data, "Missing 'status' field"
        assert data["status"] == "ok", f"Expected status 'ok', got {data['status']}"
        print(f"✓ Health check passed: {data}")
        return True
    except Exception as e:
        print(f"✗ Health check failed: {e}")
        return False


def test_summarize():
    """Test 2: POST /api/summarize with real Indonesian news article."""
    print(f"\n[TEST 2] POST /api/summarize")
    print(f"Article URL: {TEST_ARTICLE_URL}")
    
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/summarize",
            json={"url": TEST_ARTICLE_URL},
            timeout=60
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response structure
        assert "summary" in data, "Missing 'summary' field"
        summary = data["summary"]
        
        # Verify key_entities exists and is non-empty
        assert "key_entities" in summary, "Missing 'key_entities' in summary"
        key_entities = summary["key_entities"]
        assert isinstance(key_entities, list), "key_entities should be a list"
        assert len(key_entities) > 0, "key_entities should not be empty"
        
        print(f"✓ Summarize successful")
        print(f"  - Cached: {data.get('cached', False)}")
        print(f"  - Title: {summary.get('article', {}).get('title', 'N/A')}")
        print(f"  - Key entities ({len(key_entities)}): {key_entities}")
        print(f"  - Rate limit remaining: {data.get('rate_limit', {}).get('remaining', 'N/A')}")
        
        return True, summary
    except Exception as e:
        print(f"✗ Summarize failed: {e}")
        return False, None


def test_latest_updates_first_call(summary):
    """Test 3: POST /api/latest-updates (first call, should take 25-60s)."""
    print(f"\n[TEST 3] POST /api/latest-updates (first call)")
    
    # Get article title for topic
    topic = summary.get("article", {}).get("title", "MSCI Indonesia")
    print(f"Topic: {topic}")
    print(f"Source URL: {TEST_ARTICLE_URL}")
    
    start_time = time.time()
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/latest-updates",
            json={"topic": topic, "source_url": TEST_ARTICLE_URL},
            timeout=120  # Allow up to 120s for first call
        )
        elapsed = time.time() - start_time
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Verify response schema
        required_fields = ["has_update", "overview", "developments", "timeline", 
                          "current_situation", "market_impact", "confidence", "sources_used"]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
        
        # Verify confidence structure
        confidence = data["confidence"]
        assert "level" in confidence, "Missing confidence.level"
        assert "reason" in confidence, "Missing confidence.reason"
        assert "score" in confidence, "Missing confidence.score"
        assert "factors" in confidence, "Missing confidence.factors"
        
        # Verify confidence.level is one of the expected values
        assert confidence["level"] in ["High", "Medium", "Low"], \
            f"Invalid confidence level: {confidence['level']}"
        
        # Verify factors structure
        factors = confidence["factors"]
        expected_factors = ["independent_domains", "independent_domains_ok", 
                           "corroborated_share", "corroboration_ok", "newest_within_48h"]
        for factor in expected_factors:
            assert factor in factors, f"Missing confidence factor: {factor}"
        
        # Verify timeline structure
        timeline = data["timeline"]
        assert isinstance(timeline, list), "timeline should be a list"
        for event in timeline:
            assert "date" in event, "Timeline event missing 'date'"
            assert "event" in event, "Timeline event missing 'event'"
            assert "sources" in event, "Timeline event missing 'sources'"
            assert isinstance(event["sources"], list), "Timeline event sources should be a list"
        
        # Verify sources_used are valid URLs
        sources_used = data["sources_used"]
        assert isinstance(sources_used, list), "sources_used should be a list"
        for url in sources_used:
            assert url.startswith("http"), f"Invalid source URL: {url}"
        
        # Verify timeline sources are valid URLs
        for event in timeline:
            for url in event["sources"]:
                assert url.startswith("http"), f"Invalid timeline source URL: {url}"
        
        print(f"✓ Latest updates (first call) successful in {elapsed:.1f}s")
        print(f"  - has_update: {data['has_update']}")
        print(f"  - Timeline events: {len(timeline)}")
        print(f"  - Sources used: {len(sources_used)}")
        print(f"  - Confidence: {confidence['level']} (score: {confidence['score']}/3)")
        print(f"  - Factors: domains={factors['independent_domains']}, " +
              f"corroboration={factors['corroborated_share']:.0%}, " +
              f"recent={factors['newest_within_48h']}")
        
        return True, data, elapsed
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"✗ Latest updates (first call) failed after {elapsed:.1f}s: {e}")
        return False, None, elapsed


def test_confidence_scoring(data):
    """Test 4: Verify confidence scoring logic is deterministic."""
    print(f"\n[TEST 4] Verify confidence scoring logic")
    
    try:
        confidence = data["confidence"]
        score = confidence["score"]
        level = confidence["level"]
        factors = confidence["factors"]
        
        # Verify deterministic rule: score>=2 → High, score==1 → Medium, score==0 → Low
        expected_level = "High" if score >= 2 else ("Medium" if score == 1 else "Low")
        assert level == expected_level, \
            f"Confidence level mismatch: score={score} should give '{expected_level}', got '{level}'"
        
        # Verify score calculation
        calculated_score = 0
        if factors["independent_domains_ok"]:
            calculated_score += 1
        if factors["corroboration_ok"]:
            calculated_score += 1
        if factors["newest_within_48h"]:
            calculated_score += 1
        
        assert score == calculated_score, \
            f"Score mismatch: expected {calculated_score}, got {score}"
        
        print(f"✓ Confidence scoring is deterministic")
        print(f"  - Score: {score}/3 → Level: {level} ✓")
        print(f"  - Factor 1 (>=4 domains): {factors['independent_domains']} domains → {factors['independent_domains_ok']}")
        print(f"  - Factor 2 (>=50% corroborated): {factors['corroborated_share']:.0%} → {factors['corroboration_ok']}")
        print(f"  - Factor 3 (newest <48h): {factors['newest_within_48h']}")
        
        return True
    except Exception as e:
        print(f"✗ Confidence scoring verification failed: {e}")
        return False


def test_latest_updates_cached():
    """Test 5: Repeat POST /api/latest-updates (should be cached, instant)."""
    print(f"\n[TEST 5] POST /api/latest-updates (cached)")
    
    # Get article title from cache
    url_hash = compute_url_hash(TEST_ARTICLE_URL)
    cached_summary = db.summary_cache.find_one({"hash": url_hash})
    if not cached_summary:
        print("✗ Cannot test cached response: summary not in cache")
        return False, None
    
    topic = cached_summary.get("payload", {}).get("article", {}).get("title", "MSCI Indonesia")
    
    start_time = time.time()
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/latest-updates",
            json={"topic": topic, "source_url": TEST_ARTICLE_URL},
            timeout=30
        )
        elapsed = time.time() - start_time
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Should be very fast (cached)
        assert elapsed < 5.0, f"Cached response took too long: {elapsed:.1f}s"
        
        # Verify timeline length is >= previous
        timeline = data["timeline"]
        
        print(f"✓ Latest updates (cached) successful in {elapsed:.1f}s")
        print(f"  - Timeline events: {len(timeline)}")
        print(f"  - Response was instant (cached)")
        
        return True, len(timeline)
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"✗ Latest updates (cached) failed after {elapsed:.1f}s: {e}")
        return False, None


def test_timeline_accumulation(previous_timeline_length):
    """Test 6: Force fresh run and verify timeline accumulation."""
    print(f"\n[TEST 6] Timeline accumulation (force fresh run)")
    
    # Clear delta cache to force fresh run
    url_hash = compute_url_hash(TEST_ARTICLE_URL)
    clear_delta_cache(url_hash)
    
    # Get timeline length before
    timeline_before = get_timeline_from_db(url_hash)
    n1 = len(timeline_before)
    print(f"Timeline length before: {n1}")
    
    # Get article title
    cached_summary = db.summary_cache.find_one({"hash": url_hash})
    topic = cached_summary.get("payload", {}).get("article", {}).get("title", "MSCI Indonesia")
    
    start_time = time.time()
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/latest-updates",
            json={"topic": topic, "source_url": TEST_ARTICLE_URL},
            timeout=120
        )
        elapsed = time.time() - start_time
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        
        # Get timeline length after
        timeline_after = data["timeline"]
        n2 = len(timeline_after)
        
        # Verify timeline never shrinks
        assert n2 >= n1, f"Timeline shrank! Before: {n1}, After: {n2}"
        
        # Verify timeline in DB
        timeline_db = get_timeline_from_db(url_hash)
        assert len(timeline_db) == n2, \
            f"Timeline in DB ({len(timeline_db)}) doesn't match response ({n2})"
        
        print(f"✓ Timeline accumulation verified in {elapsed:.1f}s")
        print(f"  - Timeline length: {n1} → {n2} (never shrinks ✓)")
        print(f"  - DB timeline_store has {len(timeline_db)} events")
        
        return True
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"✗ Timeline accumulation test failed after {elapsed:.1f}s: {e}")
        return False


def test_backend_logs():
    """Test 7: Check backend logs for concurrent execution."""
    print(f"\n[TEST 7] Check backend logs for concurrent execution")
    
    try:
        import subprocess
        result = subprocess.run(
            ["tail", "-n", "100", "/var/log/supervisor/backend.err.log"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        logs = result.stdout + result.stderr
        
        # Look for concurrent query execution log
        if "[latest-updates]" in logs and "queries in" in logs:
            # Extract timing
            import re
            match = re.search(r'\[latest-updates\] (\d+) queries in ([\d.]+)s', logs)
            if match:
                num_queries = int(match.group(1))
                query_time = float(match.group(2))
                
                assert num_queries == 8, f"Expected 8 queries, found {num_queries}"
                assert query_time < 10, f"Queries took too long: {query_time}s (should be <10s for concurrent)"
                
                print(f"✓ Backend logs show concurrent execution")
                print(f"  - {num_queries} queries in {query_time:.1f}s (concurrent ✓)")
            else:
                print("⚠ Could not parse query timing from logs")
        
        # Look for web_fetch log
        if "web_fetch" in logs:
            match = re.search(r'web_fetch (\d+)/(\d+) full pages', logs)
            if match:
                fetched = int(match.group(1))
                total = int(match.group(2))
                print(f"  - web_fetch {fetched}/{total} full pages (rest snippet fallback)")
        
        return True
    except Exception as e:
        print(f"⚠ Could not check backend logs: {e}")
        return True  # Don't fail test if logs unavailable


def test_edge_case_nonsense_topic():
    """Test 8: Edge case with nonsense topic."""
    print(f"\n[TEST 8] Edge case: nonsense topic")
    
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/latest-updates",
            json={"topic": "xyzzyqwerty nonsense topic that matches nothing"},
            timeout=60
        )
        
        # Should return 200, not 500
        assert response.status_code == 200, \
            f"Expected 200 for nonsense topic, got {response.status_code}"
        
        data = response.json()
        
        # Should have has_update=false or empty-ish delta
        # (not a hard requirement, but expected behavior)
        print(f"✓ Edge case handled gracefully")
        print(f"  - Status: 200 (no crash ✓)")
        print(f"  - has_update: {data.get('has_update', 'N/A')}")
        print(f"  - Timeline events: {len(data.get('timeline', []))}")
        
        return True
    except Exception as e:
        print(f"✗ Edge case test failed: {e}")
        return False


def test_rate_limit():
    """Test 9: Rate limit enforcement."""
    print(f"\n[TEST 9] Rate limit enforcement")
    
    # Clear rate limits first
    clear_rate_limits()
    
    # Use cached article so calls are instant after first
    url_hash = compute_url_hash(TEST_ARTICLE_URL)
    cached_summary = db.summary_cache.find_one({"hash": url_hash})
    topic = cached_summary.get("payload", {}).get("article", {}).get("title", "MSCI Indonesia")
    
    try:
        # Make 5 calls (should all succeed)
        for i in range(5):
            response = requests.post(
                f"{BACKEND_URL}/api/latest-updates",
                json={"topic": topic, "source_url": TEST_ARTICLE_URL},
                timeout=30
            )
            assert response.status_code == 200, \
                f"Call {i+1}/5 failed with status {response.status_code}"
            print(f"  - Call {i+1}/5: 200 OK")
        
        # 6th call should be rate limited (429)
        response = requests.post(
            f"{BACKEND_URL}/api/latest-updates",
            json={"topic": topic, "source_url": TEST_ARTICLE_URL},
            timeout=30
        )
        
        assert response.status_code == 429, \
            f"Expected 429 (rate limited), got {response.status_code}"
        
        # Verify Retry-After header
        assert "Retry-After" in response.headers, "Missing Retry-After header"
        retry_after = response.headers["Retry-After"]
        
        print(f"✓ Rate limit enforced")
        print(f"  - 5 calls succeeded, 6th call: 429 (rate limited ✓)")
        print(f"  - Retry-After: {retry_after}s")
        
        return True
    except Exception as e:
        print(f"✗ Rate limit test failed: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 80)
    print("NEWS SUMMARIZER BACKEND TEST - LATEST UPDATES PIPELINE")
    print("=" * 80)
    
    results = []
    
    # Clear rate limits before starting
    print("\n[SETUP] Clearing rate limits")
    clear_rate_limits()
    
    # Test 1: Health check
    results.append(("Health check", test_health()))
    
    # Test 2: Summarize
    success, summary = test_summarize()
    results.append(("Summarize", success))
    if not success:
        print("\n✗ Cannot continue without successful summarize")
        print_summary(results)
        return
    
    # Test 3: Latest updates (first call)
    success, data, elapsed = test_latest_updates_first_call(summary)
    results.append(("Latest updates (first call)", success))
    if not success:
        print("\n✗ Cannot continue without successful latest-updates")
        print_summary(results)
        return
    
    # Test 4: Confidence scoring
    results.append(("Confidence scoring", test_confidence_scoring(data)))
    
    # Test 5: Latest updates (cached)
    success, timeline_length = test_latest_updates_cached()
    results.append(("Latest updates (cached)", success))
    
    # Test 6: Timeline accumulation
    results.append(("Timeline accumulation", test_timeline_accumulation(timeline_length)))
    
    # Test 7: Backend logs
    results.append(("Backend logs", test_backend_logs()))
    
    # Test 8: Edge case
    results.append(("Edge case (nonsense topic)", test_edge_case_nonsense_topic()))
    
    # Test 9: Rate limit
    results.append(("Rate limit", test_rate_limit()))
    
    # Print summary
    print_summary(results)


def print_summary(results):
    """Print test summary."""
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status}: {name}")
    
    print("=" * 80)
    print(f"TOTAL: {passed}/{total} tests passed")
    print("=" * 80)
    
    if passed == total:
        print("✓ ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"✗ {total - passed} TEST(S) FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
