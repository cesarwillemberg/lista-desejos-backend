"""Mark items as already bought. Existing rows stay unpurchased.

Revision ID: 005_purchased
Revises: 004_members
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "005_purchased"
down_revision = "004_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("purchased", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("items", sa.Column("purchased_at", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("purchased_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "purchased_by")
    op.drop_column("items", "purchased_at")
    op.drop_column("items", "purchased")
