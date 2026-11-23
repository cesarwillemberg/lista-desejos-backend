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


def _saved(client, headers, list_id, item_id):
    listed = client.get(f"/api/lists/{list_id}/items", headers=headers).json()
    for item in listed["items"]:
        if item["id"] == item_id:
            return item
    raise AssertionError(item_id)


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
    assert body["status"] == "pending"
    assert body["store"] == "KaBuM!"
    body = _saved(client, ana, list_id, body["id"])
    assert body["price"] == 6196.39
    assert body["status"] == "ok"
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
    code = shared["short_code"]
    assert len(code) == 7
    assert shared["share_path"] == f"/l/{code}"
    assert shared["short_url"].endswith(f"/s/{code}")
    public = client.get(f"/api/share/{token}")
    assert public.status_code == 200
    by_code = client.get(f"/api/share/{code}")
    assert by_code.status_code == 200
    page = client.get(f"/s/{code}")
    assert page.status_code == 200
    assert "og:title" in page.text
    assert "og:description" in page.text
    assert f"#/l/{code}" in page.text
    assert client.get(f"/s/{token}").status_code == 200
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
    assert owner["items"][0]["reserved"] is False
    shown = client.patch(f"/api/lists/{list_id}/settings", json={"show_reservations": True}, headers=headers)
    assert shown.status_code == 200
    revealed = client.get(f"/api/lists/{list_id}/items", headers=headers).json()
    assert revealed["items"][0]["reserved_label"] == "Marina"

    cleared = client.delete(
        f"/api/share/{token}/items/{item['id']}/reservation",
        headers={"X-Claim-Token": claim_token},
    )
    assert cleared.status_code == 204

    regenerated = client.post(f"/api/lists/{list_id}/share/regenerate", headers=headers).json()
    assert regenerated["share_token"] != token
    assert regenerated["short_code"] != code
    assert client.get(f"/api/share/{token}").status_code == 404
    assert client.get(f"/s/{code}").status_code == 404
    assert client.get(f"/s/{regenerated['short_code']}").status_code == 200
    revoked = client.delete(f"/api/lists/{list_id}/share", headers=headers).json()
    assert revoked["share_token"] is None
    assert revoked["short_code"] is None
    assert client.get(f"/api/share/{regenerated['share_token']}").status_code == 404
    assert client.get(f"/s/{regenerated['short_code']}").status_code == 404


def test_short_code_skips_collisions(client, monkeypatch):
    headers, _user = _auth(client, "codes@example.com")
    lists = client.get("/api/lists", headers=headers).json()["lists"]
    other = client.post("/api/lists", json={"name": "Outra"}, headers=headers).json()
    first = client.post(f"/api/lists/{lists[0]['id']}/share", headers=headers).json()
    taken = first["short_code"]
    calls = {"n": 0}

    def fake_code():
        calls["n"] += 1
        return taken if calls["n"] == 1 else "Zz99abc"

    monkeypatch.setattr("app.main.new_short_code", fake_code)
    second = client.post(f"/api/lists/{other['id']}/share", headers=headers).json()
    assert second["short_code"] == "Zz99abc"
    assert calls["n"] >= 2


def test_categories_belong_to_a_list_and_delete_clears_items(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    headers, _user = _auth(client, "casa@example.com")
    other, _user = _auth(client, "vizinho@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    sala = client.post(f"/api/lists/{list_id}/categories", json={"name": "Sala"}, headers=headers)
    cozinha = client.post(f"/api/lists/{list_id}/categories", json={"name": "Cozinha"}, headers=headers)
    assert sala.status_code == 201
    sala_id = sala.json()["id"]
    cozinha_id = cozinha.json()["id"]
    assert sala.json()["position"] == 0
    assert cozinha.json()["position"] == 1
    lamp = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "Luminária", "url": "https://www.kabum.com.br/produto/1", "category_id": sala_id},
        headers=headers,
    )
    assert lamp.status_code == 201
    assert lamp.json()["category_id"] == sala_id
    loose = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "Cabo", "url": "https://www.kabum.com.br/produto/2"},
        headers=headers,
    )
    assert loose.json()["category_id"] is None
    ordered = client.post(
        f"/api/lists/{list_id}/categories/reorder",
        json={"ids": [cozinha_id, sala_id]},
        headers=headers,
    )
    assert [item["id"] for item in ordered.json()["categories"]] == [cozinha_id, sala_id]
    renamed = client.patch(f"/api/categories/{sala_id}", json={"name": "Sala de estar"}, headers=headers)
    assert renamed.json()["name"] == "Sala de estar"
    assert client.delete(f"/api/categories/{sala_id}", headers=other).status_code == 404
    assert client.delete(f"/api/categories/{sala_id}", headers=headers).status_code == 204
    items = client.get(f"/api/lists/{list_id}/items", headers=headers).json()["items"]
    assert all(item["category_id"] is None for item in items)
    public = client.post(f"/api/lists/{list_id}/share", headers=headers).json()
    shared = client.get(f"/api/share/{public['short_code']}").json()
    assert "categories" in shared


def test_well_known_links(client, monkeypatch):
    monkeypatch.setenv("ANDROID_PACKAGE", "com.wishly.app")
    monkeypatch.setenv("ANDROID_SHA256_FINGERPRINTS", "AA:BB, CC:DD")
    monkeypatch.setenv("IOS_APP_ID", "TEAMID.com.wishly.app")
    asset = client.get("/.well-known/assetlinks.json")
    assert asset.status_code == 200
    body = asset.json()
    assert body[0]["target"]["package_name"] == "com.wishly.app"
    assert body[0]["target"]["sha256_cert_fingerprints"] == ["AA:BB", "CC:DD"]
    apple = client.get("/.well-known/apple-app-site-association")
    assert apple.status_code == 200
    assert apple.json()["applinks"]["details"][0]["paths"] == ["/s/*", "/c/*"]
    assert apple.json()["applinks"]["details"][0]["appID"] == "TEAMID.com.wishly.app"


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
    assert refreshed.json()["status"] == "pending"
    settled = _saved(client, headers, list_id, item["id"])
    assert settled["price"] == 6196.39
    assert settled["status"] == "failed"
    assert "Mantivemos" not in (settled["message"] or "")
    assert settled["message"] == "Não consegui atualizar agora. Edite o preço ou toque em Tentar de novo."
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(status="failed", message="Essa loja bloqueia a busca automática; edite o preço manualmente", scrape_error="blocked", final_url=url),
    )
    blocked = client.post(f"/api/items/{item['id']}/refresh", headers=headers)
    assert blocked.status_code == 200
    kept = _saved(client, headers, list_id, item["id"])
    assert kept["price"] == 6196.39
    assert kept["status"] == "failed"
    assert kept["message"] == "A loja bloqueou a busca automática. Edite o preço ou toque em Tentar de novo."
    assert "Mantivemos" not in kept["message"]


def test_rate_limit_blocks_repeated_login(client):
    from app.limit import auth_limiter

    auth_limiter.limit = 2
    auth_limiter.reset()
    try:
        payload = {"email": "limite@example.com", "password": "senha-forte"}
        assert client.post("/api/auth/register", json=payload).status_code == 201
        assert client.post("/api/auth/login", json=payload).status_code == 200
        blocked = client.post("/api/auth/login", json=payload)
        assert blocked.status_code == 429
        assert "minuto" in blocked.json()["detail"]
    finally:
        auth_limiter.limit = 10000
        auth_limiter.reset()


def test_scraper_rejects_private_and_non_http(client):
    headers, _user = _auth(client, "rede@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    for url in ("file:///etc/passwd", "ftp://example.com/a", "http://127.0.0.1/x", "http://10.1.1.1/x", "http://169.254.1.1/x"):
        response = client.post(f"/api/lists/{list_id}/items", json={"name": "Local", "url": url}, headers=headers)
        assert response.status_code == 400, url


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


def test_purchased_splits_totals_and_viewers_cannot_toggle(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    ana, _ana = _auth(client, "dona@example.com")
    editor, _editor = _auth(client, "edita@example.com")
    viewer, _viewer = _auth(client, "olha@example.com")
    list_id = client.get("/api/lists", headers=ana).json()["lists"][0]["id"]
    assert client.post(
        f"/api/lists/{list_id}/members",
        json={"email": "edita@example.com", "role": "editor"},
        headers=ana,
    ).status_code == 200
    assert client.post(
        f"/api/lists/{list_id}/members",
        json={"email": "olha@example.com", "role": "viewer"},
        headers=ana,
    ).status_code == 200
    first = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "PS5", "url": "https://www.kabum.com.br/produto/1"},
        headers=ana,
    )
    assert first.status_code == 201
    assert first.json()["purchased"] is False
    second = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "Echo", "url": "https://www.kabum.com.br/produto/2"},
        headers=editor,
    ).json()
    marked = client.patch(f"/api/items/{first.json()['id']}", json={"purchased": True}, headers=editor)
    assert marked.status_code == 200
    assert marked.json()["purchased"] is True
    assert marked.json()["purchased_at"]
    assert marked.json()["purchased_by_label"] == "Edita"
    assert client.patch(f"/api/items/{first.json()['id']}", json={"purchased": False}, headers=viewer).status_code == 403
    listed = client.get(f"/api/lists/{list_id}/items", headers=viewer).json()
    assert listed["items"][0]["purchased"] is True or any(item["purchased"] for item in listed["items"])
    assert listed["totals"] == [{"currency": "BRL", "amount": 6196.39}]
    assert listed["purchased_totals"] == [{"currency": "BRL", "amount": 6196.39}]
    shared = client.post(f"/api/lists/{list_id}/share", headers=ana).json()
    public = client.get(f"/api/share/{shared['short_code']}").json()
    bought = next(item for item in public["items"] if item["name"] == "PS5")
    assert bought["purchased"] is True
    assert public["purchased_totals"] == [{"currency": "BRL", "amount": 6196.39}]
    cleared = client.patch(f"/api/items/{first.json()['id']}", json={"purchased": False}, headers=ana)
    assert cleared.json()["purchased"] is False
    assert cleared.json()["purchased_by_label"] is None
    assert second["id"]


def test_existing_lists_gain_an_owner_membership(tmp_path, monkeypatch):
    db_file = tmp_path / "existing.db"
    monkeypatch.setenv("QUERO_DB", str(db_file))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from app import db as database

    root = Path(database.__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database.sqlalchemy_url())
    command.upgrade(cfg, "003_categories")
    raw = sqlite3.connect(db_file)
    raw.execute(
        "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
        ("user-1", "ana@example.com", "hash", "2026-01-01T00:00:00+00:00"),
    )
    raw.execute(
        "INSERT INTO lists (id, user_id, name, share_token, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("list-1", "user-1", "Natal", None, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    raw.commit()
    raw.close()
    command.upgrade(cfg, "head")
    raw = sqlite3.connect(db_file)
    row = raw.execute("SELECT user_id, role FROM list_members WHERE list_id = ?", ("list-1",)).fetchone()
    raw.close()
    assert row == ("user-1", "owner")


def test_collaboration_roles_invites_and_viewer_cannot_write(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    ana, _ana = _auth(client, "ana@example.com")
    bia, bia_user = _auth(client, "bia@example.com")
    caio, _caio = _auth(client, "caio@example.com")
    list_id = client.get("/api/lists", headers=ana).json()["lists"][0]["id"]
    assert client.get("/api/lists", headers=ana).json()["lists"][0]["role"] == "owner"
    assert client.get("/api/lists", headers=ana).json()["lists"][0]["collaborative"] is False

    missing = client.post(
        f"/api/lists/{list_id}/members",
        json={"email": "ninguem@example.com", "role": "editor"},
        headers=ana,
    )
    assert missing.status_code == 404
    assert "link de convite" in missing.json()["detail"]

    added = client.post(
        f"/api/lists/{list_id}/members",
        json={"email": "bia@example.com", "role": "editor"},
        headers=ana,
    )
    assert added.status_code == 200
    assert {member["email"]: member["role"] for member in added.json()["members"]}["bia@example.com"] == "editor"
    assert client.post(
        f"/api/lists/{list_id}/members",
        json={"email": "bia@example.com", "role": "editor"},
        headers=bia,
    ).status_code == 403

    item = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "PS5", "url": "https://www.kabum.com.br/produto/1"},
        headers=bia,
    )
    assert item.status_code == 201
    assert item.json()["added_by_label"] == "Bia"
    assert client.post(f"/api/lists/{list_id}/categories", json={"name": "Sala"}, headers=bia).status_code == 201
    assert client.delete(f"/api/lists/{list_id}", headers=bia).status_code == 403
    assert client.post(f"/api/lists/{list_id}/share", headers=bia).status_code == 403
    assert client.patch(f"/api/lists/{list_id}", json={"name": "Outra"}, headers=bia).status_code == 403

    stranger = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "X", "url": "https://www.kabum.com.br/produto/9"},
        headers=caio,
    )
    assert stranger.status_code == 404
    assert client.get(f"/api/lists/{list_id}/items", headers=caio).status_code == 404

    viewed = client.post(
        f"/api/lists/{list_id}/members",
        json={"email": "caio@example.com", "role": "viewer"},
        headers=ana,
    )
    assert viewed.status_code == 200
    listed = client.get("/api/lists", headers=caio).json()["lists"]
    assert listed[0]["id"] == list_id
    assert listed[0]["role"] == "viewer"
    assert listed[0]["collaborative"] is True
    assert listed[0]["share_token"] is None
    assert any(member["user_id"] == bia_user["id"] for member in listed[0]["members"])
    seen = client.get(f"/api/lists/{list_id}/items", headers=caio)
    assert seen.status_code == 200
    assert seen.json()["categories"]
    assert seen.json()["totals"]
    assert client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "Nope", "url": "https://www.kabum.com.br/produto/3"},
        headers=caio,
    ).status_code == 403
    assert client.patch(f"/api/items/{item.json()['id']}", json={"name": "Mudou"}, headers=caio).status_code == 403
    assert client.delete(f"/api/items/{item.json()['id']}", headers=caio).status_code == 403
    category_id = seen.json()["categories"][0]["id"]
    assert client.delete(f"/api/categories/{category_id}", headers=caio).status_code == 403

    reserved = client.post(f"/api/items/{item.json()['id']}/reservation", json={"name": ""}, headers=caio)
    assert reserved.status_code == 200
    assert reserved.json()["reserved_by_me"] is True
    assert client.delete(f"/api/items/{item.json()['id']}/reservation", headers=bia).status_code == 204

    member_id = next(member["user_id"] for member in viewed.json()["members"] if member["email"] == "caio@example.com")
    promoted = client.patch(
        f"/api/lists/{list_id}/members/{member_id}",
        json={"role": "editor"},
        headers=ana,
    )
    assert promoted.status_code == 200
    assert client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "Agora", "url": "https://www.kabum.com.br/produto/4"},
        headers=caio,
    ).status_code == 201
    demoted = client.patch(
        f"/api/lists/{list_id}/members/{member_id}",
        json={"role": "viewer"},
        headers=ana,
    )
    assert demoted.status_code == 200
    owner_id = next(member["user_id"] for member in demoted.json()["members"] if member["role"] == "owner")
    assert client.patch(
        f"/api/lists/{list_id}/members/{owner_id}",
        json={"role": "viewer"},
        headers=ana,
    ).status_code == 404

    editor_link = client.post(f"/api/lists/{list_id}/invites/editor", headers=ana).json()
    viewer_link = client.post(f"/api/lists/{list_id}/invites/viewer", headers=ana).json()
    editor_code = editor_link["invite_editor_url"].rstrip("/").split("/")[-1]
    viewer_code = viewer_link["invite_viewer_url"].rstrip("/").split("/")[-1]
    assert editor_code != viewer_code
    assert len(editor_code) == 7
    page = client.get(f"/c/{editor_code}")
    assert page.status_code == 200
    assert "og:title" in page.text
    assert f"#/c/{editor_code}" in page.text
    assert "pode editar" in page.text
    view_page = client.get(f"/c/{viewer_code}")
    assert "só pode ver" in view_page.text

    dina, _dina = _auth(client, "dina@example.com")
    preview = client.get(f"/api/invites/{editor_code}", headers=dina)
    assert preview.json()["role"] == "editor"
    accepted = client.post(f"/api/invites/{editor_code}/accept", headers=dina)
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "editor"
    assert client.get(f"/api/lists/{list_id}/items", headers=dina).status_code == 200

    regenerated = client.post(f"/api/lists/{list_id}/invites/editor/regenerate", headers=ana).json()
    new_editor = regenerated["invite_editor_url"].rstrip("/").split("/")[-1]
    assert new_editor != editor_code
    assert regenerated["invite_viewer_url"].endswith(viewer_code)
    assert client.get(f"/c/{editor_code}").status_code == 404
    assert client.post(f"/api/invites/{editor_code}/accept", headers=dina).status_code == 404
    disabled = client.delete(f"/api/lists/{list_id}/invites/viewer", headers=ana).json()
    assert disabled["invite_viewer_url"] is None
    assert client.get(f"/c/{viewer_code}").status_code == 404
    assert client.post(f"/api/lists/{list_id}/invites/editor/regenerate", headers=bia).status_code == 403

    assert client.post(f"/api/lists/{list_id}/leave", headers=ana).status_code == 400
    assert client.delete(f"/api/lists/{list_id}/members/{member_id}", headers=bia).status_code == 403
    removed = client.delete(f"/api/lists/{list_id}/members/{member_id}", headers=ana)
    assert removed.status_code == 200
    assert client.get(f"/api/lists/{list_id}/items", headers=caio).status_code == 404
    assert client.post(f"/api/lists/{list_id}/leave", headers=bia).status_code == 204
    assert client.get(f"/api/lists/{list_id}/items", headers=bia).status_code == 404
    assert client.get(f"/api/lists/{list_id}/items", headers=ana).status_code == 200


def test_changing_the_link_replaces_store_and_price(client, monkeypatch):
    def fake(url):
        if "amazon." in url:
            return Extracted(
                image_url="https://m.media-amazon.com/images/I/echo.jpg",
                price=199.0,
                currency="BRL",
                title="Echo Dot",
                status="ok",
                image_source="json-ld",
                price_source="json-ld",
                final_url=url,
            )
        return _ok(url)

    monkeypatch.setattr("app.main.extract_product", fake)
    headers, _user = _auth(client, "link@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    created = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "PS5", "url": "https://www.kabum.com.br/produto/953110"},
        headers=headers,
    )
    assert created.status_code == 201
    item_id = created.json()["id"]
    original = _saved(client, headers, list_id, item_id)
    assert original["store"] == "KaBuM!"
    assert original["price"] == 6196.39
    edited = client.patch(
        f"/api/items/{item_id}",
        json={
            "name": "PS5",
            "url": "https://www.amazon.com.br/dp/B09B8XVSDP",
            "price": 6196.39,
            "image_url": original["image_url"],
            "currency": "BRL",
        },
        headers=headers,
    )
    assert edited.status_code == 200
    assert edited.json()["status"] == "pending"
    assert edited.json()["store"] == "Amazon"
    assert edited.json()["price"] is None
    settled = _saved(client, headers, list_id, item_id)
    assert settled["store"] == "Amazon"
    assert settled["price"] == 199.0
    assert settled["name"] == "Echo Dot"
    assert "amazon" in (settled["image_url"] or "")
    assert settled["status"] == "ok"


def test_blank_name_uses_the_page_title_and_a_typed_name_stays(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(
            title="Smart Tv 50u7066",
            price=1788.8,
            currency="BRL",
            image_url="https://images.kabum.com.br/tv.jpg",
            status="ok",
            price_source="kabum-api",
            scrape_error="ok kabum-api http=200",
            final_url=url,
        ),
    )
    headers, _user = _auth(client, "nome@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    created = client.post(
        f"/api/lists/{list_id}/items",
        json={"url": "https://www.kabum.com.br/produto/1071513/smart-tv-50u7066-78g"},
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Buscando nome…"
    saved = _saved(client, headers, list_id, created.json()["id"])
    assert saved["name"] == "Smart Tv 50u7066"
    assert saved["price"] == 1788.8
    assert "kabum-api" in (saved["scrape_error"] or "")
    typed = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "Minha TV", "url": "https://www.kabum.com.br/produto/1/outra-tv"},
        headers=headers,
    )
    assert _saved(client, headers, list_id, typed.json()["id"])["name"] == "Minha TV"


def test_block_page_title_is_replaced_by_the_slug(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(
            title="Access Denied",
            status="failed",
            message="Essa loja bloqueia a busca automática; edite o preço manualmente",
            http_status=403,
            scrape_error="blocked reason=http-403 http=403 via=curl_cffi len=491",
            final_url=url,
        ),
    )
    headers, _user = _auth(client, "bloqueio@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    created = client.post(
        f"/api/lists/{list_id}/items",
        json={"url": "https://www.kabum.com.br/produto/1071513/smart-tv-50u7066-78g-dled-50-4k-wi-fi-roku-tv-aoc"},
        headers=headers,
    )
    saved = _saved(client, headers, list_id, created.json()["id"])
    assert saved["name"] == "Smart Tv 50u7066 78g Dled 50 4k Wi Fi Roku Tv Aoc"
    assert saved["message"].startswith("Essa loja bloqueia")


def test_refresh_renames_an_access_denied_item(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(
            status="failed",
            message="Essa loja bloqueia a busca automática; edite o preço manualmente",
            http_status=403,
            scrape_error="blocked reason=http-403",
            final_url=url,
        ),
    )
    headers, _user = _auth(client, "renomear@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    created = client.post(
        f"/api/lists/{list_id}/items",
        json={
            "name": "Access Denied",
            "url": "https://www.kabum.com.br/produto/1071513/smart-tv-50u7066-78g-dled-50-4k-wi-fi-roku-tv-aoc",
        },
        headers=headers,
    )
    item_id = created.json()["id"]
    refreshed = client.post(f"/api/items/{item_id}/refresh", headers=headers)
    assert refreshed.status_code == 200
    assert _saved(client, headers, list_id, item_id)["name"] == "Smart Tv 50u7066 78g Dled 50 4k Wi Fi Roku Tv Aoc"


def test_failed_scrape_names_the_item_from_the_slug(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(status="failed", message="Não consegui ler o preço; edite manualmente", scrape_error="parse http=200", final_url=url),
    )
    headers, _user = _auth(client, "slug@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    created = client.post(
        f"/api/lists/{list_id}/items",
        json={"url": "https://www.kabum.com.br/produto/1071513/smart-tv-50u7066-78g"},
        headers=headers,
    )
    saved = _saved(client, headers, list_id, created.json()["id"])
    assert saved["name"].startswith("Smart Tv 50u7066 78g")
    assert saved["message"] == "Não consegui ler o preço; edite manualmente"


def test_refresh_replaces_a_missing_price(client, monkeypatch):
    calls = {"n": 0}

    def fake(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return Extracted(status="failed", message="Não consegui ler o preço; edite manualmente", scrape_error="parse", title="TV", final_url=url)
        return Extracted(
            status="ok",
            title="TV",
            price=1788.8,
            currency="BRL",
            image_url="https://images.kabum.com.br/tv.jpg",
            price_source="kabum-api",
            scrape_error="ok kabum-api",
            final_url=url,
        )

    monkeypatch.setattr("app.main.extract_product", fake)
    headers, _user = _auth(client, "atualizar@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    created = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "TV da sala", "url": "https://www.kabum.com.br/produto/1071513/smart-tv"},
        headers=headers,
    )
    item_id = created.json()["id"]
    assert _saved(client, headers, list_id, item_id)["price"] is None
    refreshed = client.post(f"/api/items/{item_id}/refresh", headers=headers)
    assert refreshed.status_code == 200
    saved = _saved(client, headers, list_id, item_id)
    assert saved["name"] == "TV da sala"
    assert saved["price"] == 1788.8
    assert saved["image_url"].endswith("tv.jpg")


def test_price_drop_is_recorded_and_cron_stays_off(client, monkeypatch):
    prices = {"n": 0}

    def fake(url):
        prices["n"] += 1
        return Extracted(title="TV", price=100 if prices["n"] == 1 else 80, currency="BRL", status="ok", image_url="https://images.example.com/tv.jpg", final_url=url)

    monkeypatch.setattr("app.main.extract_product", fake)
    headers, _user = _auth(client, "preco@example.com")
    assert client.post("/api/cron/check-prices").status_code == 404
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    created = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "TV", "url": "https://www.kabum.com.br/produto/1/tv"},
        headers=headers,
    )
    item_id = created.json()["id"]
    client.post(f"/api/items/{item_id}/refresh", headers=headers)
    listed = client.get(f"/api/lists/{list_id}/items", headers=headers).json()
    item = next(entry for entry in listed["items"] if entry["id"] == item_id)
    assert item["price"] == 80
    assert item["price_drop"] == 20
    history = client.get(f"/api/items/{item_id}/prices", headers=headers).json()
    assert history["lowest"] == 80
    assert history["highest"] == 100
    notes = client.get("/api/notifications", headers=headers).json()
    assert notes["unread"] >= 1


def test_notes_details_and_manual_order(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: Extracted(status="failed", final_url=url))
    headers, _user = _auth(client, "ordem@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]

    def add(name):
        response = client.post(
            f"/api/lists/{list_id}/items",
            json={"name": name, "url": f"https://www.kabum.com.br/produto/{name}", "priority": "talvez", "note": "rascunho"},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    first = add("alfa")
    second = add("beta")
    patched = client.patch(
        f"/api/items/{first}",
        json={"priority": "muito", "note": "Presente da sala", "color": "azul", "size_label": "M", "voltage": "110V", "quantity": 2},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["priority"] == "muito"
    assert body["note"] == "Presente da sala"
    assert body["color"] == "azul"
    assert body["size_label"] == "M"
    assert body["voltage"] == "110V"
    assert body["quantity"] == 2
    cleared = client.patch(f"/api/items/{first}", json={"color": "", "quantity": None}, headers=headers)
    assert cleared.json()["color"] is None
    assert cleared.json()["quantity"] is None
    rejected = client.post(f"/api/lists/{list_id}/items/reorder", json={"ids": [second]}, headers=headers)
    assert rejected.status_code == 400
    ordered = client.post(f"/api/lists/{list_id}/items/reorder", json={"ids": [second, first]}, headers=headers)
    assert ordered.status_code == 200
    listed = client.get(f"/api/lists/{list_id}/items", headers=headers).json()["items"]
    by_id = {item["id"]: item for item in listed}
    assert by_id[second]["position"] < by_id[first]["position"]
    assert by_id[second]["note"] == "rascunho"
    assert by_id[second]["priority"] == "talvez"
    again = client.get(f"/api/lists/{list_id}/items", headers=headers).json()["items"]
    again_by_id = {item["id"]: item for item in again}
    assert again_by_id[second]["position"] < again_by_id[first]["position"]
    kitchen = client.post(f"/api/lists/{list_id}/categories", json={"name": "Cozinha"}, headers=headers)
    assert kitchen.status_code == 201
    kitchen_id = kitchen.json()["id"]
    placed = []
    for name in ("panela", "copo", "prato"):
        created = client.post(
            f"/api/lists/{list_id}/items",
            json={"name": name, "url": f"https://www.kabum.com.br/produto/{name}", "category_id": kitchen_id},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        placed.append(created.json()["id"])
    everyone = client.get(f"/api/lists/{list_id}/items", headers=headers).json()["items"]
    ids = [item["id"] for item in sorted(everyone, key=lambda item: item["position"])]
    moved = [placed[2], placed[0], placed[1]] + [item_id for item_id in ids if item_id not in placed]
    saved = client.post(f"/api/lists/{list_id}/items/reorder", json={"ids": moved}, headers=headers)
    assert saved.status_code == 200, saved.text
    persisted = client.get(f"/api/lists/{list_id}/items", headers=headers).json()["items"]
    order = [item["id"] for item in sorted(persisted, key=lambda item: (item["position"], item["created_at"]))]
    assert order == moved
    kitchen_items = [item for item in persisted if item["category_id"] == kitchen_id]
    kitchen_items.sort(key=lambda item: item["position"])
    assert [item["id"] for item in kitchen_items] == [placed[2], placed[0], placed[1]]


def test_create_keeps_typed_price_and_details(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(title="Da loja", price=50, currency="USD", status="ok", image_url="https://images.example.com/loja.jpg", final_url=url),
    )
    headers, _user = _auth(client, "detalhe@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    created = client.post(
        f"/api/lists/{list_id}/items",
        json={
            "url": "https://www.kabum.com.br/produto/9/caixa",
            "priority": "muito",
            "note": "azul marinho",
            "color": "azul",
            "size_label": "M",
            "voltage": "220V",
            "quantity": 2,
            "price": 12.5,
            "currency": "BRL",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = _saved(client, headers, list_id, created.json()["id"])
    assert body["name"] == "Da loja"
    assert body["priority"] == "muito"
    assert body["note"] == "azul marinho"
    assert body["color"] == "azul"
    assert body["size_label"] == "M"
    assert body["voltage"] == "220V"
    assert body["quantity"] == 2
    assert body["price"] == 12.5
    assert body["currency"] == "BRL"


def test_extension_origin_is_allowed(client):
    response = client.get("/api/health", headers={"Origin": "chrome-extension://abcdefghijklmnop"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "chrome-extension://abcdefghijklmnop"


def test_debug_fetch_stays_off_unless_enabled(client, monkeypatch):
    assert client.get("/api/debug/fetch", params={"url": "https://example.com/produto"}).status_code == 404
    monkeypatch.setenv("WISHLY_DEBUG_FETCH", "1")
    monkeypatch.setattr(
        "app.main.extract_product",
        lambda url: Extracted(status="ok", price=10, currency="BRL", title="Copo", scrape_error="ok parse", http_status=200, via="test", final_url=url),
    )
    opened = client.get("/api/debug/fetch", params={"url": "https://example.com/produto"})
    assert opened.status_code == 200
    assert opened.json()["scrape_error"] == "ok parse"
    assert opened.json()["price"] == 10


def test_health_can_time_the_database(client):
    response = client.get("/api/health?db=1")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["database"] == "sqlite"
    assert body["db_ms"] >= 0


def test_list_reads_use_one_connection(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: _ok(url))
    headers, _user = _auth(client, "pool@example.com")
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "PS5", "url": "https://www.kabum.com.br/produto/953110"},
        headers=headers,
    )
    from app import db

    db.checkouts = 0
    items = client.get(f"/api/lists/{list_id}/items", headers=headers)
    assert items.status_code == 200
    assert items.json()["totals"] == [{"currency": "BRL", "amount": 6196.39}]
    assert db.checkouts == 1
    db.checkouts = 0
    listed = client.get("/api/lists", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["lists"][0]["item_count"] == 1
    assert db.checkouts == 1


def test_login_rehashes_an_old_password(client):
    from argon2 import PasswordHasher

    headers, user = _auth(client, "rehash@example.com")
    old = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
    from app import db

    db.set_password_hash(user["id"], old.hash("senha-forte"))
    logged = client.post("/api/auth/login", json={"email": "rehash@example.com", "password": "senha-forte"})
    assert logged.status_code == 200
    stored = db.get_user_by_email("rehash@example.com")
    assert stored is not None
    from app.auth import password_needs_rehash

    assert password_needs_rehash(stored["password_hash"]) is False
    assert client.get("/api/auth/me", headers=headers).status_code == 200


def test_profile_name_email_password_and_avatar(client, monkeypatch):
    monkeypatch.setattr("app.main.extract_product", lambda url: Extracted(status="failed", final_url=url))
    headers, user = _auth(client, "perfil@example.com")
    renamed = client.patch("/api/me", json={"display_name": "Ana Souza"}, headers=headers)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["display_name"] == "Ana Souza"
    assert "token_version" not in renamed.json()
    list_id = client.get("/api/lists", headers=headers).json()["lists"][0]["id"]
    members = client.get(f"/api/lists/{list_id}/members", headers=headers).json()["members"]
    assert members[0]["label"] == "Ana Souza"
    created = client.post(
        f"/api/lists/{list_id}/items",
        json={"name": "Copo", "url": "https://www.kabum.com.br/produto/1/copo"},
        headers=headers,
    )
    assert created.json()["added_by_label"] == "Ana Souza"
    reserved = client.post(f"/api/items/{created.json()['id']}/reservation", json={}, headers=headers)
    assert reserved.status_code == 200
    assert reserved.json()["reserved_label"] == "Ana Souza"

    clash = client.post("/api/auth/register", json={"email": "outra@example.com", "password": "senha-forte"})
    assert clash.status_code == 201
    taken = client.post("/api/me/email", json={"email": "outra@example.com", "password": "errada"}, headers=headers)
    assert taken.status_code == 401
    taken = client.post("/api/me/email", json={"email": "outra@example.com", "password": "senha-forte"}, headers=headers)
    assert taken.status_code == 409
    moved = client.post("/api/me/email", json={"email": "ana.nova@example.com", "password": "senha-forte"}, headers=headers)
    assert moved.status_code == 200, moved.text
    headers = {"Authorization": f"Bearer {moved.json()['token']}"}
    assert moved.json()["user"]["email"] == "ana.nova@example.com"

    same = client.post(
        "/api/me/password",
        json={"current_password": "senha-forte", "new_password": "curta"},
        headers=headers,
    )
    assert same.status_code == 400
    changed = client.post(
        "/api/me/password",
        json={"current_password": "senha-forte", "new_password": "senha-nova-1"},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text
    assert client.get("/api/me", headers=headers).status_code == 401
    headers = {"Authorization": f"Bearer {changed.json()['token']}"}
    assert client.get("/api/me", headers=headers).status_code == 200

    junk = client.put("/api/me/avatar", content=b"not-an-image", headers={**headers, "content-type": "image/jpeg"})
    assert junk.status_code == 400
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    saved = client.put("/api/me/avatar", content=jpeg, headers={**headers, "content-type": "image/jpeg"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["has_avatar"] is True
    photo = client.get(f"/api/users/{user['id']}/avatar")
    assert photo.status_code == 200
    assert photo.content == jpeg
    assert photo.headers["etag"]
    cached = client.get(f"/api/users/{user['id']}/avatar", headers={"If-None-Match": photo.headers["etag"]})
    assert cached.status_code == 304
    removed = client.delete("/api/me/avatar", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["has_avatar"] is False
    assert client.get(f"/api/users/{user['id']}/avatar").status_code == 404
