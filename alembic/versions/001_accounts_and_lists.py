"""Accounts, wishlists, and items.

Revision ID: 001_accounts
Revises:
Create Date: 2026-11-16
"""

from alembic import op
import sqlalchemy as sa

revision = "001_accounts"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    names = set(inspector.get_table_names())
    if "items" in names and "users" not in names:
        op.rename_table("items", "legacy_items")

    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "lists",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("share_token", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("share_token", name="uq_lists_share_token"),
    )
    op.create_index("ix_lists_user", "lists", ["user_id"])
    op.create_table(
        "items",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("list_id", sa.Text(), sa.ForeignKey("lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text()),
        sa.Column("price", sa.Float()),
        sa.Column("currency", sa.Text()),
        sa.Column("store", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("image_source", sa.Text()),
        sa.Column("price_source", sa.Text()),
        sa.Column("reserved_label", sa.Text()),
        sa.Column("reserved_token_hash", sa.Text()),
        sa.Column("reserved_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_items_list", "items", ["list_id"])


def downgrade() -> None:
    op.drop_index("ix_items_list", table_name="items")
    op.drop_table("items")
    op.drop_index("ix_lists_user", table_name="lists")
    op.drop_table("lists")
    op.drop_table("users")
