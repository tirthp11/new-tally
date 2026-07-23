import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Voucher(Base):
    """One invoice ready to push or already pushed. Supports soft delete.

    Deleting sets deleted_at/deleted_by only. It never creates a connector job
    and never touches Tally - that invariant is enforced structurally in the
    API layer (the delete endpoint has no job-creation code path).
    """

    __tablename__ = "vouchers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tally_companies.id"), nullable=True
    )
    voucher_type: Mapped[str] = mapped_column(String, nullable=False, default="Purchase")
    party_name: Mapped[str | None] = mapped_column(String, nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String, nullable=True)
    invoice_date: Mapped[str | None] = mapped_column(String, nullable=True)  # YYYYMMDD
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    # The user-corrected invoice plus master resolution choices
    # (shape documented in docs/03-DATA-MODEL.md).
    edited_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    # draft | queued | pushed | failed  (deleted is expressed via deleted_at)
    tally_voucher_number: Mapped[str | None] = mapped_column(String, nullable=True)
    tally_masterid: Mapped[str | None] = mapped_column(String, nullable=True)
    error_log: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pushed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
