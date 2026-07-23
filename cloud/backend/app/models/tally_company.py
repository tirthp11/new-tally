import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TallyCompany(Base):
    __tablename__ = "tally_companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Exact company name as Tally reports it; used verbatim in SVCURRENTCOMPANY.
    tally_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    tally_guid: Mapped[str | None] = mapped_column(String, nullable=True)
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connectors.id"), nullable=True
    )
    # Only companies an admin marks active can receive vouchers.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # OFF (default): the AI/OCR extracted date is used as-is.
    # ON: dates are coerced for Tally educational mode (FY shift + 1st/2nd/31st day).
    education_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Financial year start (YYYYMMDD); used only when education_mode is true.
    fy_start_date: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
