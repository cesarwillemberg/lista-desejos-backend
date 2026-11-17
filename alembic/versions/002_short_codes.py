"""Short public codes for shared lists.

Revision ID: 002_short_codes
Revises: 001_accounts
Create Date: 2026-11-17
"""

import secrets

import sqlalchemy as sa
from alembic import op

revision = "002_short_codes"
down_revision = "001_accounts"
branch_labels = None
depends_on = None

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _fresh_code(bind, used: set[str]) -> str:
    for _ in range(30):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(7))
        if code in used:
            continue
        taken = bind.execute(sa.text("SELECT 1 FROM lists WHERE short_code = :code"), {"code": code}).fetchone()
        if taken is None:
            used.add(code)
            return code
    raise RuntimeError("Não foi possível gerar um código curto.")


def upgrade() -> None:
    op.add_column("lists", sa.Column("short_code", sa.Text(), nullable=True))
    op.create_index("uq_lists_short_code", "lists", ["short_code"], unique=True)
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id FROM lists WHERE share_token IS NOT NULL AND short_code IS NULL")
    ).fetchall()
    used: set[str] = set()
    for row in rows:
        list_id = row[0]
        code = _fresh_code(bind, used)
        bind.execute(
            sa.text("UPDATE lists SET short_code = :code WHERE id = :id"),
            {"code": code, "id": list_id},
        )


def downgrade() -> None:
    op.drop_index("uq_lists_short_code", table_name="lists")
    op.drop_column("lists", "short_code")
