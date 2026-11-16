import os

os.environ["QUERO_DISABLE_WEB"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("QUERO_DB", str(tmp_path / "wishlist.db"))
    with TestClient(app) as test_client:
        yield test_client
