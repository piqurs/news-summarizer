"""Shared fixtures for News Summarizer backend tests."""
import os
import re
from pathlib import Path
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

if not os.environ.get("REACT_APP_BACKEND_URL"):
    txt = Path("/app/frontend/.env").read_text()
    m = re.search(r"REACT_APP_BACKEND_URL=(\S+)", txt)
    if m:
        os.environ["REACT_APP_BACKEND_URL"] = m.group(1)

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


@pytest.fixture(scope="session", autouse=True)
def reset_mongo_state():
    """Reset rate_limit BEFORE the test session so the 5-per-hour budget is
    predictable. summary_cache is NOT wiped here — tests that need a cache
    miss do a per-URL delete themselves (keeps other test modules from
    losing pre-seeded articles).
    Uses the same MongoDB the backend uses (localhost)."""
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db = client[os.environ.get("DB_NAME", "news_summarizer")]
    db.rate_limit.delete_many({})
    print("[reset_mongo_state] cleared rate_limit")
    yield
    client.close()


@pytest.fixture(scope="session")
def api_client():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL
