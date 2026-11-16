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


def test_health_reports_sqlite_and_allows_pages_origin(client):
    response = client.get("/api/health", headers={"Origin": "https://cesarwillemberg.github.io"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "database": "sqlite"}
    assert response.headers["access-control-allow-origin"] == "https://cesarwillemberg.github.io"


def test_create_lists_total_and_survives_another_request(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    created = client.post(
        "/api/items",
        json={"name": "PlayStation 5", "url": "https://www.kabum.com.br/produto/953110"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["price"] == 6196.39
    assert body["currency"] == "BRL"
    assert body["store"] == "KaBuM!"
    assert body["image_url"].endswith("p.jpg")

    listed = client.get("/api/items")
    assert listed.status_code == 200
    payload = listed.json()
    assert len(payload["items"]) == 1
    assert payload["totals"] == [{"currency": "BRL", "amount": 6196.39}]
    assert payload["unpriced_count"] == 0
    assert payload["items"][0]["id"] == body["id"]


def test_failed_extraction_still_saves(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(
            status="failed",
            message="O Mercado Livre pediu uma verificação de acesso e não entregou a página do produto.",
            final_url=url,
            http_status=200,
        ),
    )
    created = client.post(
        "/api/items",
        json={"name": "Kindle", "url": "https://www.mercadolivre.com.br/p/MLB1"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["price"] is None
    assert body["image_url"] is None
    assert body["status"] == "failed"
    assert "Mercado Livre" in body["message"]
    listed = client.get("/api/items").json()
    assert listed["unpriced_count"] == 1
    assert listed["totals"] == []


def test_patch_name_price_and_image(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(status="failed", message="falhou", final_url=url),
    )
    item = client.post(
        "/api/items",
        json={"name": "Echo", "url": "https://www.amazon.com.br/dp/B09B8XVSDP"},
    ).json()
    updated = client.patch(
        f"/api/items/{item['id']}",
        json={
            "name": "Echo Dot",
            "image_url": "https://m.media-amazon.com/images/I/x.jpg",
            "price": 584.1,
            "currency": "BRL",
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "Echo Dot"
    assert body["price"] == 584.1
    assert body["currency"] == "BRL"
    assert body["status"] == "ok"
    assert body["image_source"] == "manual"


def test_refresh_keeps_previous_when_fetch_fails(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    item = client.post(
        "/api/items",
        json={"name": "PS5", "url": "https://www.kabum.com.br/produto/953110"},
    ).json()
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(status="failed", message="A loja não respondeu.", final_url=url),
    )
    refreshed = client.post(f"/api/items/{item['id']}/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["price"] == 6196.39
    assert body["image_url"].endswith("p.jpg")
    assert "Mantivemos" in body["message"]


def test_delete(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    item = client.post(
        "/api/items",
        json={"name": "PS5", "url": "https://www.kabum.com.br/produto/953110"},
    ).json()
    removed = client.delete(f"/api/items/{item['id']}")
    assert removed.status_code == 204
    assert client.get("/api/items").json()["items"] == []


def test_private_url_is_not_saved(client):
    response = client.post("/api/items", json={"name": "Local", "url": "http://127.0.0.1/admin"})
    assert response.status_code == 400
    assert client.get("/api/items").json()["items"] == []


def test_clear_price(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    item = client.post(
        "/api/items",
        json={"name": "PS5", "url": "https://www.kabum.com.br/produto/953110"},
    ).json()
    updated = client.patch(f"/api/items/{item['id']}", json={"price": None})
    assert updated.status_code == 200
    assert updated.json()["price"] is None
    assert updated.json()["status"] == "partial"
