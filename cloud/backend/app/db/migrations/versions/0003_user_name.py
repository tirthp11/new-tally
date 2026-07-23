"""Add a display name to users (for reference in the admin panel).

The column is nullable so every existing user row stays valid; the admin can
fill names in later via Edit user.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "name")
