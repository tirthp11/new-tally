"""Audit log helper. Every push and every delete goes through here."""
import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def write_audit(
    db: Session,
    actor_id: uuid.UUID | None,
    action: str,
    entity: str,
    entity_id: uuid.UUID | None,
    detail: dict | None = None,
) -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, entity=entity,
                    entity_id=entity_id, detail_json=detail))
    db.commit()
