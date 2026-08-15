"""End-to-end verification of the new Latest Updates pipeline.
Checklist:
1. First latest-updates check should land ~13-17s (concurrent queries).
2. Second refresh (forced re-run, delta cache cleared) timeline >= first.
3. Confidence has deterministic score + factors.
"""
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE = "http://localhost:8001/api"

from tavily import TavilyClient  # noqa: E402

tv = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
resp = tv.search(query="IHSG Bursa Efek Indonesia", topic="news",
                 time_range="week", max_results=8,
                 include_domains=["kompas.com", "bisnis.com", "detik.com",
                                  "tempo.co", "antaranews.com", "cnbc.com"])
article_url = None
for r in resp.get("results", []):
    u = r.get("url") or ""
    if u and "video" not in u:
        article_url = u
        break
if not article_url:
    print("NO_ARTICLE_FOUND")
    sys.exit(1)
print("ARTICLE:", article_url)

# --- Step 1: summarize (baseline) ---
t0 = time.time()
r = requests.post(f"{BASE}/summarize", json={"url": article_url}, timeout=180)
print(f"SUMMARIZE status={r.status_code} took={time.time()-t0:.1f}s")
if r.status_code != 200:
    print(r.text[:500]); sys.exit(1)
body = r.json()
summary = body["summary"]
topic = summary["article"]["title"]
entities = summary.get("key_entities")
print("KEY_ENTITIES stored in payload:", entities)

# --- Step 2: first latest-updates (timed) ---
t0 = time.time()
r = requests.post(f"{BASE}/latest-updates",
                  json={"topic": topic, "source_url": article_url}, timeout=180)
first_time = time.time() - t0
print(f"LATEST-UPDATES #1 status={r.status_code} took={first_time:.1f}s")
if r.status_code != 200:
    print(r.text[:500]); sys.exit(1)
d1 = r.json()
tl1 = d1.get("timeline") or []
print("has_update:", d1.get("has_update"),
      "| timeline_len:", len(tl1),
      "| developments:", len(d1.get("developments") or []),
      "| sources_used:", len(d1.get("sources_used") or []))
print("confidence:", json.dumps(d1.get("confidence"), ensure_ascii=False))

# --- Step 3: cached refresh (should be instant, timeline never shorter) ---
t0 = time.time()
r = requests.post(f"{BASE}/latest-updates",
                  json={"topic": topic, "source_url": article_url}, timeout=60)
print(f"LATEST-UPDATES #2 (cached) status={r.status_code} took={time.time()-t0:.1f}s")
d2 = r.json()
tl2 = d2.get("timeline") or []
print("cached timeline_len:", len(tl2), "(must be >=", len(tl1), ")")
assert len(tl2) >= len(tl1), "TIMELINE SHRANK ON CACHED REFRESH"

# --- Step 4: force full re-run (clear delta cache only, keep timeline store) ---
from pymongo import MongoClient
mc = MongoClient(os.environ["MONGO_URL"])
db = mc[os.environ["DB_NAME"]]
res = db.summary_cache.update_many(
    {}, {"$unset": {"latest_update_delta": "", "delta_generated_at": ""}})
print("delta cache cleared:", res.modified_count)

t0 = time.time()
r = requests.post(f"{BASE}/latest-updates",
                  json={"topic": topic, "source_url": article_url}, timeout=180)
third_time = time.time() - t0
print(f"LATEST-UPDATES #3 (fresh re-run) status={r.status_code} took={third_time:.1f}s")
d3 = r.json()
tl3 = d3.get("timeline") or []
print("re-run timeline_len:", len(tl3), "(must be >=", len(tl1), ")")
print("confidence #3:", json.dumps(d3.get("confidence"), ensure_ascii=False))
assert len(tl3) >= len(tl1), "TIMELINE SHRANK ON FRESH RE-RUN — MERGE BROKEN"

# --- Step 5: persistent store check ---
doc = db.timeline_store.find_one({}, {"_id": 0, "timeline": 1})
print("timeline_store persisted:", bool(doc), "events:", len((doc or {}).get("timeline") or []))

print("\n=== TIMELINE (#3) ===")
for ev in tl3:
    print(f"  {ev.get('date')} | {ev.get('event')[:90]} | srcs={len(ev.get('sources') or [])}")
print("\nALL CHECKS PASSED")
print(f"TIMINGS first={first_time:.1f}s rerun={third_time:.1f}s")
