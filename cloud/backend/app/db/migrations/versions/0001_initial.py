"""Initial schema: all eight tables from docs/03-DATA-MODEL.md.

Revision ID: 0001
Revises:
Create Date: 2026-07-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "connectors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("pairing_code_hash", sa.String(), nullable=True),
        sa.Column("token_hash", sa.String(), nullable=True, index=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("app_version", sa.String(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "tally_companies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tally_name", sa.String(), nullable=False, unique=True),
        sa.Column("tally_guid", sa.String(), nullable=True),
        sa.Column("connector_id", UUID(as_uuid=True), sa.ForeignKey("connectors.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("education_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("fy_start_date", sa.String(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "masters_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("tally_companies.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("parent", sa.String(), nullable=True),
        sa.Column("meta_json", JSONB(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_masters_cache_company_kind", "masters_cache", ["company_id", "kind"])

    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("uploaded_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("mime", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("extracted_json", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "vouchers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("tally_companies.id"), nullable=True),
        sa.Column("voucher_type", sa.String(), nullable=False),
        sa.Column("party_name", sa.String(), nullable=True),
        sa.Column("invoice_number", sa.String(), nullable=True),
        sa.Column("invoice_date", sa.String(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("edited_json", JSONB(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("tally_voucher_number", sa.String(), nullable=True),
        sa.Column("tally_masterid", sa.String(), nullable=True),
        sa.Column("error_log", JSONB(), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("connector_id", UUID(as_uuid=True), sa.ForeignKey("connectors.id"), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("tally_companies.id"), nullable=True),
        sa.Column("voucher_id", UUID(as_uuid=True), sa.ForeignKey("vouchers.id"), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("result_json", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("entity", sa.String(), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("detail_json", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("jobs")
    op.drop_table("vouchers")
    op.drop_table("documents")
    op.drop_index("ix_masters_cache_company_kind", table_name="masters_cache")
    op.drop_table("masters_cache")
    op.drop_table("tally_companies")
    op.drop_table("connectors")
    op.drop_table("users")
