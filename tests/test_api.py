import sqlite3

from app.extractor import Extracted


def _ok(url="https://www.kabum.com.br/produto/1"):
    return Extracted(
        image_url="https://images.kabum.com.br/p.jpg",
        price=6196.39,
        currency="BRL",
        title="PS5",
        status="ok",
        message=None,
        image_source="json-ld",
        price_source="json-ld",
        http_status=200,
        final_url=url,
    )


def _auth(client, email="ana@example.com", password="senha-forte"):
    response = client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}, response.json()["user"]


def test_health_reports_sqlite_and_allows_pages_origin(client):
    response = client.get("/api/health", headers={"Origin": "https://cesarwillemberg.github.io"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "database": "sqlite"}
    assert response.headers["access-control-allow-origin"] == "https://cesarwillemberg.github.io"


def test_register_login_and_reject_bad_password(client):
    headers, user = _auth(client)
    assert user["email"] == "ana@example.com"
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["id"] == user["id"]
    bad = client.post("/api/auth/login", json={"email": "ana@example.com", "password": "errada-demais"})
    assert bad.status_code == 401
    duplicate = client.post("/api/auth/register", json={"email": "Ana@example.com", "password": "senha-forte"})
    assert duplicate.status_code == 400
    assert client.get("/api/lists").status_code == 401


def test_lists_items_are_private_to_the_owner(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    ana, _user = _auth(client, "ana@example.com")
    bia, _user = _auth(client, "bia@example.com")
    created = client.post("/api/lists", json={"name": "Natal"}, headers=ana)
    assert created.status_code == 201
    list_id = created.json()["id"]
    renamed = client.patch(f"/api/lists/{list_id}", json={"name": "Natal 2026"}, headers=ana)
    assert renamed.json()["name"] == "Natal 2026"
    assert client.get(f"/api/lists/{list_id}/items", headers=bia).status_code == 404

    item = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "PS5", "url": "https://www.kabum.com.br/produto/953110"},
        headers=ana,
    )
    assert item.status_code == 201
    body = item.json()
    assert body["price"] == 6196.39
    assert body["store"] == "KaBuM!"
    listed = client.get(f"/api/lists/{list_id}/items", headers=ana).json()
    assert listed["totals"] == [{"currency": "BRL", "amount": 6196.39}]
    assert client.delete(f"/api/lists/{list_id}", headers=bia).status_code == 404
    assert client.delete(f"/api/lists/{list_id}", headers=ana).status_code == 204


def test_share_link_is_read_only_and_can_be_revoked(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    headers, _user = _auth(client)
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    item = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "PS5", "url": "https://www.kabum.com.br/produto/953110"},
        headers=headers,
    ).json()
    hidden = client.get("/api/share/nao-existe")
    assert hidden.status_code == 404

    shared = client.post(f"/api/lists/{list_id}/share", headers=headers).json()
    token = shared["share_token"]
    assert shared["share_path"] == f"/l/{token}"
    public = client.get(f"/api/share/{token}")
    assert public.status_code == 200
    assert public.json()["items"][0]["name"] == "PS5"
    assert public.json()["items"][0]["reserved_label"] is None
    assert client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "Outro", "url": "https://www.kabum.com.br/produto/1"},
        headers={"Authorization": "Bearer guest"},
    ).status_code == 401

    claim = client.post(f"/api/share/{token}/items/{item['id']}/reservation", json={"name": "Marina"})
    assert claim.status_code == 200
    claim_token = claim.json()["claim_token"]
    again = client.post(f"/api/share/{token}/items/{item['id']}/reservation", json={"name": "Outra"})
    assert again.status_code == 409
    seen = client.get(f"/api/share/{token}", headers={"X-Claims": f"{item['id']}:{claim_token}"}).json()
    assert seen["items"][0]["reserved_by_me"] is True
    assert seen["items"][0]["reserved_label"] == "Marina"
    stranger = client.get(f"/api/share/{token}").json()
    assert stranger["items"][0]["reserved"] is True
    assert stranger["items"][0]["reserved_label"] is None
    owner = client.get(f"/api/lists/{list_id}/items", headers=headers).json()
    assert owner["items"][0]["reserved_label"] == "Marina"

    cleared = client.delete(
        f"/api/share/{token}/items/{item['id']}/reservation",
        headers={"X-Claim-Token": claim_token},
    )
    assert cleared.status_code == 204

    regenerated = client.post(f"/api/lists/{list_id}/share/regenerate", headers=headers).json()
    assert regenerated["share_token"] != token
    assert client.get(f"/api/share/{token}").status_code == 404
    revoked = client.delete(f"/api/lists/{list_id}/share", headers=headers).json()
    assert revoked["share_token"] is None
    assert client.get(f"/api/share/{regenerated['share_token']}").status_code == 404


def test_refresh_keeps_previous_when_fetch_fails(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    headers, _user = _auth(client)
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    item = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "PS5", "url": "https://www.kabum.com.br/produto/953110"},
        headers=headers,
    ).json()
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(status="failed", message="A loja não respondeu.", final_url=url),
    )
    refreshed = client.post(f"/api/items/{item['id']}/refresh", headers=headers)
    assert refreshed.status_code == 200
    assert refreshed.json()["price"] == 6196.39
    assert "Mantivemos" in refreshed.json()["message"]


def test_private_url_is_not_saved(client):
    headers, _user = _auth(client)
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    response = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "Local", "url": "http://127.0.0.1/admin"},
        headers=headers,
    )
    assert response.status_code == 400
    assert client.get(f"/api/lists/{list_id}/items", headers=headers).json()["items"] == []


def test_first_user_adopts_legacy_items(tmp_path, monkeypatch):
    db_file = tmp_path / "old.db"
    monkeypatch.setenv("QUERO_DB", str(db_file))
    raw = sqlite3.connect(db_file)
    raw.execute(
        """
        CREATE TABLE items (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, image_url TEXT,
          price REAL, currency TEXT, store TEXT, status TEXT NOT NULL, message TEXT,
          image_source TEXT, price_source TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    raw.execute(
        "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-1",
            "Kindle",
            "https://www.amazon.com.br/dp/x",
            None,
            None,
            None,
            "Amazon",
            "failed",
            "bloqueado",
            None,
            None,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    raw.commit()
    raw.close()

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        first, _user = _auth(client, "primeiro@example.com")
        lists = client.get("/api/lists", headers=first).json()["lists"]
        assert lists[0]["name"] == "Minha lista"
        items = client.get(f"/api/lists/{lists[0]['id']}/items", headers=first).json()["items"]
        assert [item["name"] for item in items] == ["Kindle"]
        second, _user = _auth(client, "segundo@example.com")
        second_lists = client.get("/api/lists", headers=second).json()["lists"]
        second_items = client.get(f"/api/lists/{second_lists[0]['id']}/items", headers=second).json()["items"]
        assert second_items == []
