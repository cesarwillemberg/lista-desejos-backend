"""Persistence for wishlist items.

`DATABASE_URL` (Postgres, including Render, Neon, or Supabase) is used when
set. Otherwise the list is a SQLite file at `QUERO_DB` or `data/wishlist.db`.
Render's free web disk is ephemeral, so a hosted Postgres URL is what keeps
the list after a restart.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  image_url TEXT,
  price REAL,
  currency TEXT,
  store TEXT,
  status TEXT NOT NULL,
  message TEXT,
  image_source TEXT,
  price_source TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def database_url() -> str | None:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    return url or None


def engine_name() -> str:
    return "postgres" if database_url() else "sqlite"


def sqlalchemy_url() -> str:
    url = database_url()
    if url:
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        return url
    path = db_path().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + path.as_posix()


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url())
    command.upgrade(cfg, "head")


def db_path() -> Path:
    configured = os.environ.get("QUERO_DB")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "data" / "wishlist.db"


class _Conn:
    def __init__(self, raw, postgres: bool):
        self._raw = raw
        self._postgres = postgres

    def execute(self, sql: str, params: tuple = ()):
        statement = sql.replace("?", "%s") if self._postgres else sql
        return self._raw.execute(statement, params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self._raw.rollback()
            else:
                self._raw.commit()
        finally:
            self._raw.close()
        return False


def connect() -> _Conn:
    url = database_url()
    if url:
        import psycopg
        from psycopg.rows import dict_row

        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        raw = psycopg.connect(url, row_factory=dict_row, connect_timeout=15)
        return _Conn(raw, postgres=True)
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path, timeout=15)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=ON")
    return _Conn(raw, postgres=False)


def init_db() -> None:
    with _lock, connect() as conn:
        conn.execute(SCHEMA)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _money(value):
    if value is None:
        return None
    return round(float(value), 2)


def _count(row) -> int:
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(next(iter(row.values())))
    try:
        return int(row["n"])
    except (KeyError, TypeError, IndexError):
        return int(row[0])


def row_to_user(row) -> dict:
    return {"id": row["id"], "email": row["email"], "created_at": row["created_at"]}


def row_to_list(row, items: list[dict] | None = None) -> dict:
    priced = [item for item in (items or []) if item["price"] is not None]
    buckets: dict[str, float] = {}
    for item in priced:
        code = item["currency"] or ""
        buckets[code] = buckets.get(code, 0.0) + float(item["price"])
    totals = [
        {"currency": key or None, "amount": round(value, 2)}
        for key, value in sorted(buckets.items(), key=lambda pair: (pair[0] != "BRL", pair[0] == "", pair[0]))
    ]
    return {
        "id": row["id"],
        "name": row["name"],
        "user_id": row["user_id"],
        "share_token": row["share_token"],
        "short_code": row["short_code"],
        "item_count": len(items or []),
        "unpriced_count": sum(1 for item in (items or []) if item["price"] is None),
        "totals": totals,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_item(row) -> dict:
    return {
        "id": row["id"],
        "list_id": row["list_id"],
        "category_id": row["category_id"] if "category_id" in row.keys() else None,
        "name": row["name"],
        "url": row["url"],
        "image_url": row["image_url"],
        "price": _money(row["price"]),
        "currency": row["currency"],
        "store": row["store"],
        "status": row["status"],
        "message": row["message"],
        "image_source": row["image_source"],
        "price_source": row["price_source"],
        "reserved": bool(row["reserved_at"]),
        "reserved_label": row["reserved_label"],
        "reserved_token_hash": row["reserved_token_hash"],
        "reserved_at": row["reserved_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def public_item(item: dict, *, mine: bool) -> dict:
    data = {key: value for key, value in item.items() if key not in {"reserved_token_hash", "reserved_label"}}
    data["reserved_by_me"] = mine
    return data


def get_user(user_id: str) -> dict | None:
    with _lock, connect() as conn:
        row = conn.execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_user(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    with _lock, connect() as conn:
        row = conn.execute("SELECT id, email, password_hash, created_at FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        return None
    user = row_to_user(row)
    user["password_hash"] = row["password_hash"]
    return user


def register_user(email: str, password_hash: str) -> dict:
    now = utc_now()
    user_id = str(uuid4())
    list_id = str(uuid4())
    with _lock, connect() as conn:
        exists = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            raise ValueError("Este e-mail já tem uma conta.")
        conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, email, password_hash, now),
        )
        conn.execute(
            "INSERT INTO lists (id, user_id, name, share_token, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (list_id, user_id, "Minha lista", None, now, now),
        )
        count = _count(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone())
        if count == 1:
            _adopt_legacy(conn, list_id, now)
    user = get_user(user_id)
    assert user is not None
    return user


def _table_exists(conn: _Conn, name: str) -> bool:
    if conn._postgres:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = ?",
            (name,),
        ).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row is not None


def _adopt_legacy(conn: _Conn, list_id: str, now: str) -> None:
    if not _table_exists(conn, "legacy_items"):
        return
    rows = conn.execute("SELECT * FROM legacy_items").fetchall()
    for row in rows:
        data = dict(row)
        conn.execute(
            """
            INSERT INTO items (
              id, list_id, name, url, image_url, price, currency, store, status, message,
              image_source, price_source, reserved_label, reserved_token_hash, reserved_at,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("id") or str(uuid4()),
                list_id,
                data.get("name") or "Item",
                data.get("url") or "https://example.invalid",
                data.get("image_url"),
                data.get("price"),
                data.get("currency"),
                data.get("store"),
                data.get("status") or "failed",
                data.get("message"),
                data.get("image_source"),
                data.get("price_source"),
                None,
                None,
                None,
                data.get("created_at") or now,
                data.get("updated_at") or now,
            ),
        )
    if conn._postgres:
        conn.execute("DROP TABLE legacy_items")
    else:
        conn.execute("DROP TABLE legacy_items")


def lists_for_user(user_id: str) -> list[dict]:
    with _lock, connect() as conn:
        rows = conn.execute(
            "SELECT * FROM lists WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        result = []
        for row in rows:
            items = [
                row_to_item(item)
                for item in conn.execute(
                    "SELECT * FROM items WHERE list_id = ? ORDER BY created_at DESC",
                    (row["id"],),
                ).fetchall()
            ]
            result.append(row_to_list(row, items))
    return result


def get_owned_list(list_id: str, user_id: str) -> dict | None:
    with _lock, connect() as conn:
        row = conn.execute("SELECT * FROM lists WHERE id = ? AND user_id = ?", (list_id, user_id)).fetchone()
        if row is None:
            return None
        items = [
            row_to_item(item)
            for item in conn.execute("SELECT * FROM items WHERE list_id = ?", (list_id,)).fetchall()
        ]
    return row_to_list(row, items)


def create_list(user_id: str, name: str) -> dict:
    now = utc_now()
    list_id = str(uuid4())
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO lists (id, user_id, name, share_token, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (list_id, user_id, name, None, now, now),
        )
    found = get_owned_list(list_id, user_id)
    assert found is not None
    return found


def rename_list(list_id: str, user_id: str, name: str) -> dict | None:
    now = utc_now()
    with _lock, connect() as conn:
        cursor = conn.execute(
            "UPDATE lists SET name = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (name, now, list_id, user_id),
        )
        if cursor.rowcount == 0:
            return None
    return get_owned_list(list_id, user_id)


def delete_list(list_id: str, user_id: str) -> bool:
    with _lock, connect() as conn:
        cursor = conn.execute("DELETE FROM lists WHERE id = ? AND user_id = ?", (list_id, user_id))
        return cursor.rowcount > 0


def short_code_taken(code: str, *, except_list_id: str | None = None) -> bool:
    with _lock, connect() as conn:
        if except_list_id:
            row = conn.execute(
                "SELECT 1 FROM lists WHERE short_code = ? AND id != ?",
                (code, except_list_id),
            ).fetchone()
        else:
            row = conn.execute("SELECT 1 FROM lists WHERE short_code = ?", (code,)).fetchone()
    return row is not None


def set_share(list_id: str, user_id: str, token: str | None, code: str | None) -> dict | None:
    now = utc_now()
    with _lock, connect() as conn:
        if code:
            clash = conn.execute(
                "SELECT id FROM lists WHERE short_code = ? AND id != ?",
                (code, list_id),
            ).fetchone()
            if clash:
                raise ValueError("Esse código curto já está em uso.")
        cursor = conn.execute(
            "UPDATE lists SET share_token = ?, short_code = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (token, code, now, list_id, user_id),
        )
        if cursor.rowcount == 0:
            return None
    return get_owned_list(list_id, user_id)


def get_list_by_share_token(token: str) -> dict | None:
    with _lock, connect() as conn:
        row = conn.execute(
            "SELECT * FROM lists WHERE short_code = ? OR share_token = ?",
            (token, token),
        ).fetchone()
        if row is None:
            return None
        items = [
            row_to_item(item)
            for item in conn.execute(
                "SELECT * FROM items WHERE list_id = ? ORDER BY created_at DESC",
                (row["id"],),
            ).fetchall()
        ]
    payload = row_to_list(row, items)
    payload["items"] = items
    return payload


def list_items(list_id: str) -> list[dict]:
    with _lock, connect() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE list_id = ? ORDER BY created_at DESC",
            (list_id,),
        ).fetchall()
    return [row_to_item(row) for row in rows]


def get_item(item_id: str) -> dict | None:
    with _lock, connect() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return row_to_item(row) if row else None


def user_owns_item(user_id: str, item_id: str) -> dict | None:
    with _lock, connect() as conn:
        row = conn.execute(
            """
            SELECT items.* FROM items
            JOIN lists ON lists.id = items.list_id
            WHERE items.id = ? AND lists.user_id = ?
            """,
            (item_id, user_id),
        ).fetchone()
    return row_to_item(row) if row else None


def insert_item(list_id: str, fields: dict) -> dict:
    now = utc_now()
    item_id = str(uuid4())
    with _lock, connect() as conn:
        conn.execute(
            """
            INSERT INTO items (
              id, list_id, category_id, name, url, image_url, price, currency, store, status, message,
              image_source, price_source, reserved_label, reserved_token_hash, reserved_at,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                list_id,
                fields.get("category_id"),
                fields["name"],
                fields["url"],
                fields.get("image_url"),
                fields.get("price"),
                fields.get("currency"),
                fields.get("store"),
                fields.get("status") or "failed",
                fields.get("message"),
                fields.get("image_source"),
                fields.get("price_source"),
                None,
                None,
                None,
                now,
                now,
            ),
        )
        conn.execute("UPDATE lists SET updated_at = ? WHERE id = ?", (now, list_id))
    item = get_item(item_id)
    assert item is not None
    return item


def update_item(item_id: str, fields: dict) -> dict | None:
    current = get_item(item_id)
    if current is None:
        return None
    allowed = {
        "name",
        "url",
        "image_url",
        "price",
        "currency",
        "store",
        "status",
        "message",
        "image_source",
        "price_source",
        "category_id",
        "reserved_label",
        "reserved_token_hash",
        "reserved_at",
    }
    changes = {key: value for key, value in fields.items() if key in allowed}
    if not changes:
        return current
    changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in changes)
    with _lock, connect() as conn:
        conn.execute(
            f"UPDATE items SET {assignments} WHERE id = ?",
            (*changes.values(), item_id),
        )
    return get_item(item_id)


def row_to_category(row) -> dict:
    return {
        "id": row["id"],
        "list_id": row["list_id"],
        "name": row["name"],
        "position": int(row["position"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_categories(list_id: str) -> list[dict]:
    with _lock, connect() as conn:
        rows = conn.execute(
            "SELECT * FROM categories WHERE list_id = ? ORDER BY position ASC, created_at ASC",
            (list_id,),
        ).fetchall()
    return [row_to_category(row) for row in rows]


def get_category(category_id: str) -> dict | None:
    with _lock, connect() as conn:
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    return row_to_category(row) if row else None


def create_category(list_id: str, name: str) -> dict:
    now = utc_now()
    category_id = str(uuid4())
    with _lock, connect() as conn:
        current = _count(conn.execute("SELECT COUNT(*) AS n FROM categories WHERE list_id = ?", (list_id,)).fetchone())
        conn.execute(
            "INSERT INTO categories (id, list_id, name, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (category_id, list_id, name, current, now, now),
        )
    found = get_category(category_id)
    assert found is not None
    return found


def rename_category(category_id: str, name: str) -> dict | None:
    now = utc_now()
    with _lock, connect() as conn:
        cursor = conn.execute(
            "UPDATE categories SET name = ?, updated_at = ? WHERE id = ?",
            (name, now, category_id),
        )
        if cursor.rowcount == 0:
            return None
    return get_category(category_id)


def delete_category(category_id: str) -> bool:
    with _lock, connect() as conn:
        conn.execute("UPDATE items SET category_id = NULL WHERE category_id = ?", (category_id,))
        cursor = conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        return cursor.rowcount > 0


def reorder_categories(list_id: str, ids: list[str]) -> list[dict]:
    now = utc_now()
    with _lock, connect() as conn:
        rows = conn.execute("SELECT id FROM categories WHERE list_id = ?", (list_id,)).fetchall()
        existing = {row["id"] for row in rows}
        if set(ids) != existing:
            raise ValueError("A ordem precisa incluir todas as categorias da lista.")
        for position, category_id in enumerate(ids):
            conn.execute(
                "UPDATE categories SET position = ?, updated_at = ? WHERE id = ? AND list_id = ?",
                (position, now, category_id, list_id),
            )
    return list_categories(list_id)


def delete_item(item_id: str) -> bool:
    current = get_item(item_id)
    if current is None:
        return False
    with _lock, connect() as conn:
        cursor = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.execute("UPDATE lists SET updated_at = ? WHERE id = ?", (utc_now(), current["list_id"]))
        return cursor.rowcount > 0
