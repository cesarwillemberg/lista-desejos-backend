"""Per-list categories. Existing items stay uncategorized.

Revision ID: 003_categories
Revises: 002_short_codes
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "003_categories"
down_revision = "002_short_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("list_id", sa.Text(), sa.ForeignKey("lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_categories_list", "categories", ["list_id"])
    op.add_column("items", sa.Column("category_id", sa.Text(), nullable=True))
    op.create_index("ix_items_category", "items", ["category_id"])


def downgrade() -> None:
    op.drop_index("ix_items_category", table_name="items")
    op.drop_column("items", "category_id")
    op.drop_index("ix_categories_list", table_name="categories")
    op.drop_table("categories")
