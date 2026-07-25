"""Shared fixtures for News Summarizer backend tests."""
import os
import pytest
import requests
from pymongo import MongoClient


BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


@pytest.fixture(scope="session", autouse=True)
def reset_mongo_state():
    """Reset rate_limit + cache collections BEFORE the test session starts so the
    5-per-hour limits are predictable and the cache tests are deterministic.
    Uses the same MongoDB the backend uses (localhost)."""
    client = MongoClient("mongodb://localhost:27017")
    db = client["test_database"]
    db.rate_limit.delete_many({})
    db.summary_cache.delete_many({})
    print("[reset_mongo_state] cleared rate_limit + summary_cache")
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
