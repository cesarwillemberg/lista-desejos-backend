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


def row_to_item(row) -> dict:
    return {
        "id": row["id"],
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
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_items() -> list[dict]:
    with _lock, connect() as conn:
        rows = conn.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
    return [row_to_item(row) for row in rows]


def get_item(item_id: str) -> dict | None:
    with _lock, connect() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return row_to_item(row) if row else None


def insert_item(fields: dict) -> dict:
    now = utc_now()
    item_id = str(uuid4())
    with _lock, connect() as conn:
        conn.execute(
            """
            INSERT INTO items (
              id, name, url, image_url, price, currency, store, status, message,
              image_source, price_source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
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
                now,
                now,
            ),
        )
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


def delete_item(item_id: str) -> bool:
    with _lock, connect() as conn:
        cursor = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        return cursor.rowcount > 0
