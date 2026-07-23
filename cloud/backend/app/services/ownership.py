"""Per-user visibility of connectors, companies and masters (docs/13).

A company (and its cached masters) is private to the web user who paired the
connector it was synced through. Admins see everything.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Connector, TallyCompany, User


def owned_connector_ids(db: Session, user: User) -> set[uuid.UUID] | None:
    """Connector ids this user owns. Returns None for admins, meaning 'all'."""
    if user.role == "admin":
        return None
    rows = db.execute(
        select(Connector.id).where(Connector.created_by == user.id)
    ).scalars().all()
    return set(rows)


def company_visible(db: Session, user: User, company: TallyCompany | None) -> bool:
    """True if this user may see/use the company. Admins: always."""
    if user.role == "admin":
        return True
    if company is None or company.connector_id is None:
        return False
    return company.connector_id in owned_connector_ids(db, user)
