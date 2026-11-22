import os

os.environ["QUERO_DISABLE_WEB"] = "1"
os.environ.setdefault("JWT_SECRET", "test-secret-please-change")
os.environ.setdefault("WISHLY_AUTH_RATE", "10000")
os.environ.setdefault("WISHLY_SCRAPE_RATE", "10000")
os.environ.setdefault("WISHLY_SCRAPE_INLINE", "1")

import pytest
from fastapi.testclient import TestClient

from app.main import _scrape_cache, app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("QUERO_DB", str(tmp_path / "wishlist.db"))
    _scrape_cache.clear()
    with TestClient(app) as test_client:
        yield test_client
