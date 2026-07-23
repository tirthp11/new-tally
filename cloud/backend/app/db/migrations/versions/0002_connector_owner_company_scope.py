"""Per-user private companies/masters (docs/13).

- connectors.created_by: the web user who paired the connector (owner).
- Existing connectors are assigned to the first admin so the admin keeps seeing
  everything already synced (decision B).
- Companies become unique per (connector_id, tally_name) instead of globally,
  so different users' connectors keep separate private company rows.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connectors",
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    # Legacy connectors -> first admin, so nothing already synced disappears for
    # admins (operators only see connectors they pair from now on).
    op.execute(
        "UPDATE connectors SET created_by = "
        "(SELECT id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1) "
        "WHERE created_by IS NULL"
    )
    # Company name is unique per connector now, not globally.
    op.drop_constraint("tally_companies_tally_name_key", "tally_companies", type_="unique")
    op.create_unique_constraint(
        "uq_tally_company_connector_name",
        "tally_companies",
        ["connector_id", "tally_name"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_tally_company_connector_name", "tally_companies", type_="unique")
    op.create_unique_constraint(
        "tally_companies_tally_name_key", "tally_companies", ["tally_name"]
    )
    op.drop_column("connectors", "created_by")
