import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MastersCache(Base):
    """One row per Tally master (ledger/group/unit/stock). Rebuilt on each sync."""

    __tablename__ = "masters_cache"
    __table_args__ = (Index("ix_masters_cache_company_kind", "company_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tally_companies.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)  # ledger | group | unit | stock
    name: Mapped[str] = mapped_column(String, nullable=False)
    # For a ledger, its group; for stock, its base unit.
    parent: Mapped[str | None] = mapped_column(String, nullable=True)
    meta_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
