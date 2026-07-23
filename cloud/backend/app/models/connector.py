import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Connector(Base):
    """A registered desktop connector.

    Pairing flow: an admin generates a pairing code (its hash is stored in
    pairing_code_hash). The connector exchanges that code once for a long-lived
    token; the token's hash replaces it in token_hash and the code is cleared.
    """

    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # The web user who generated this connector's pairing code. Companies synced
    # through the connector (and their masters) are private to this user; admins
    # see everything (docs/13).
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    pairing_code_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="offline")  # online | offline
    app_version: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
