"""Manual order, notes, and quick item details.

Revision ID: 008_notes_order
Revises: 007_gifts
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "008_notes_order"
down_revision = "007_gifts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("items", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("color", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("size_label", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("voltage", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("quantity", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "quantity")
    op.drop_column("items", "voltage")
    op.drop_column("items", "size_label")
    op.drop_column("items", "color")
    op.drop_column("items", "note")
    op.drop_column("items", "position")
