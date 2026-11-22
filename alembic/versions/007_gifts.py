"""Price history, alerts, reservations privacy, priority, and budget.

Revision ID: 007_gifts
Revises: 006_scrape_error
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "007_gifts"
down_revision = "006_scrape_error"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_price_alerts", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("lists", sa.Column("reservations_enabled", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("lists", sa.Column("show_reservations", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("lists", sa.Column("budget", sa.Float(), nullable=True))
    op.add_column("lists", sa.Column("budget_currency", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("priority", sa.Text(), nullable=False, server_default="quero"))
    op.create_table(
        "price_history",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("item_id", sa.Text(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_price_history_item", "price_history", ["item_id", "checked_at"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=True),
        sa.Column("list_id", sa.Text(), nullable=True),
        sa.Column("read_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_notifications_user", "notifications", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_user", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_price_history_item", table_name="price_history")
    op.drop_table("price_history")
    op.drop_column("items", "priority")
    op.drop_column("lists", "budget_currency")
    op.drop_column("lists", "budget")
    op.drop_column("lists", "show_reservations")
    op.drop_column("lists", "reservations_enabled")
    op.drop_column("users", "email_price_alerts")
