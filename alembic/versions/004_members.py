"""Collaborators on existing and new lists.

Every current list keeps its owner. Invite codes and item authorship are added
without rewriting item rows.

Revision ID: 004_members
Revises: 003_categories
Create Date: 2026-11-17
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "004_members"
down_revision = "003_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "list_members",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("list_id", sa.Text(), sa.ForeignKey("lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("list_id", "user_id", name="uq_list_members"),
    )
    op.create_index("ix_list_members_user", "list_members", ["user_id"])
    op.add_column("lists", sa.Column("invite_editor_code", sa.Text(), nullable=True))
    op.add_column("lists", sa.Column("invite_viewer_code", sa.Text(), nullable=True))
    op.create_index("uq_lists_invite_editor", "lists", ["invite_editor_code"], unique=True)
    op.create_index("uq_lists_invite_viewer", "lists", ["invite_viewer_code"], unique=True)
    op.add_column("items", sa.Column("added_by", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("reserved_by", sa.Text(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, user_id, created_at FROM lists")).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        bind.execute(
            sa.text(
                "INSERT INTO list_members (id, list_id, user_id, role, created_at) "
                "VALUES (:id, :list_id, :user_id, 'owner', :created_at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "list_id": row[0],
                "user_id": row[1],
                "created_at": row[2] or now,
            },
        )


def downgrade() -> None:
    op.drop_column("items", "reserved_by")
    op.drop_column("items", "added_by")
    op.drop_index("uq_lists_invite_viewer", table_name="lists")
    op.drop_index("uq_lists_invite_editor", table_name="lists")
    op.drop_column("lists", "invite_viewer_code")
    op.drop_column("lists", "invite_editor_code")
    op.drop_index("ix_list_members_user", table_name="list_members")
    op.drop_table("list_members")
