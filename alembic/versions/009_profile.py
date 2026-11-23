"""Display name, avatar bytes, and session version.

Revision ID: 009_profile
Revises: 008_notes_order
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "009_profile"
down_revision = "008_notes_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "user_avatars",
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_avatars")
    op.drop_column("users", "token_version")
    op.drop_column("users", "display_name")
