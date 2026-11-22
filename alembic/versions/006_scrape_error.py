"""Store a short diagnostic from the last product read.

Revision ID: 006_scrape_error
Revises: 005_purchased
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "006_scrape_error"
down_revision = "005_purchased"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("scrape_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "scrape_error")
