"""Admin-only management: users, connectors, active companies, audit log."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.security import generate_pairing_code, hash_password, hash_token
from app.db.session import get_db
from app.models import AuditLog, Connector, TallyCompany, User
from app.schemas.admin import (
    AuditOut,
    CompanyActiveUpdate,
    CompanyOut,
    ConnectorOut,
    EducationModeUpdate,
    PairingCodeRequest,
    PairingCodeResponse,
)
from app.schemas.auth import UserCreate, UserOut, UserUpdate
from app.services.audit import write_audit
from app.services.connector_hub import hub

router = APIRouter(prefix="/admin", tags=["admin"])


# -- Users --------------------------------------------------------------------
@router.get("/users", response_model=list[UserOut])
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.execute(select(User).order_by(User.created_at)).scalars().all()


@router.post("/users", response_model=UserOut)
def create_user(body: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if body.role not in ("admin", "operator"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Role must be admin or operator")
    email = body.email.lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    name = (body.name or "").strip() or None
    user = User(name=name, email=email, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    db.commit()
    db.refresh(user)
    write_audit(db, admin.id, "user.create", "user", user.id, {"email": email, "role": body.role})
    return user


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if body.name is not None:
        user.name = body.name.strip() or None
    if body.email is not None:
        new_email = body.email.lower()
        if new_email != user.email:
            clash = db.execute(
                select(User).where(User.email == new_email, User.id != user.id)
            ).scalar_one_or_none()
            if clash is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
            user.email = new_email
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.role is not None:
        if body.role not in ("admin", "operator"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Role must be admin or operator")
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    write_audit(db, admin.id, "user.update", "user", user.id, None)
    return user


@router.delete("/users/{user_id}")
def delete_user(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Remove a user account and ALL the data that belongs to that user.

    A user who synced or pushed owns rows in several tables (a connector row is
    created when they pair, and companies/masters/vouchers/jobs hang off it), so
    a plain delete hit a foreign-key violation on connectors.created_by. This
    now cascades the user's own footprint, in FK-safe order:

        connectors (created_by)
          -> tally_companies (connector_id)   the user's private companies
               -> masters_cache, vouchers, jobs
        documents (uploaded_by)
          -> vouchers (document_id)           work the user uploaded

    Isolation: everything removed is scoped to THIS user's connectors and
    documents. Companies are private per connector owner (docs/13), so another
    user's identically-named company (a different connector row) is untouched.
    The audit trail is preserved - audit rows and other users' vouchers that
    this user had soft-deleted keep their history with the actor cleared to null.

    Guards: you cannot delete yourself and the last remaining admin cannot be
    deleted. Database-only; never touches Tally (docs/07).
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    if user.role == "admin":
        admins_left = db.execute(
            select(User).where(User.role == "admin", User.id != user.id)
        ).scalars().first()
        if admins_left is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The last admin cannot be deleted")

    deleted_email = user.email
    from app.models import Document, Job, MastersCache, Voucher  # local import to avoid cycles

    # Resolve the user's owned rows up front.
    connector_ids = db.execute(
        select(Connector.id).where(Connector.created_by == user.id)
    ).scalars().all()
    company_ids = db.execute(
        select(TallyCompany.id).where(TallyCompany.connector_id.in_(connector_ids))
    ).scalars().all() if connector_ids else []
    document_ids = db.execute(
        select(Document.id).where(Document.uploaded_by == user.id)
    ).scalars().all()
    # Vouchers to remove: those on the user's companies plus those uploaded by the
    # user (their document), even if that document's voucher targets a company
    # owned by someone else.
    voucher_id_set: set[uuid.UUID] = set()
    if company_ids:
        voucher_id_set.update(db.execute(
            select(Voucher.id).where(Voucher.company_id.in_(company_ids))
        ).scalars().all())
    if document_ids:
        voucher_id_set.update(db.execute(
            select(Voucher.id).where(Voucher.document_id.in_(document_ids))
        ).scalars().all())
    voucher_ids = list(voucher_id_set)

    counts = {
        "email": deleted_email,
        "connectors": len(connector_ids),
        "companies": len(company_ids),
        "documents": len(document_ids),
        "vouchers": len(voucher_ids),
    }
    # Audit BEFORE the delete so the row survives (actor is the admin, not the
    # user being removed).
    write_audit(db, admin.id, "user.delete", "user", user_id, counts)

    # Detach references we keep (preserve the audit trail and other users'
    # soft-delete history) before removing the user.
    db.query(AuditLog).filter(AuditLog.actor_id == user.id).update(
        {AuditLog.actor_id: None}, synchronize_session=False)
    db.query(Voucher).filter(Voucher.deleted_by == user.id).update(
        {Voucher.deleted_by: None}, synchronize_session=False)

    # Delete the user's own data, children before parents.
    # 1. jobs (reference connectors / companies / vouchers)
    if connector_ids:
        db.query(Job).filter(Job.connector_id.in_(connector_ids)).delete(
            synchronize_session=False)
    if company_ids:
        db.query(Job).filter(Job.company_id.in_(company_ids)).delete(
            synchronize_session=False)
    if voucher_ids:
        db.query(Job).filter(Job.voucher_id.in_(voucher_ids)).delete(
            synchronize_session=False)
    # 2. vouchers (reference documents + companies)
    if voucher_ids:
        db.query(Voucher).filter(Voucher.id.in_(voucher_ids)).delete(
            synchronize_session=False)
    # 3. documents (now free of vouchers)
    if document_ids:
        db.query(Document).filter(Document.id.in_(document_ids)).delete(
            synchronize_session=False)
    # 4. masters cache (references companies)
    if company_ids:
        db.query(MastersCache).filter(MastersCache.company_id.in_(company_ids)).delete(
            synchronize_session=False)
    # 5. companies (reference connectors)
    if company_ids:
        db.query(TallyCompany).filter(TallyCompany.id.in_(company_ids)).delete(
            synchronize_session=False)
    # 6. connectors (reference users)
    if connector_ids:
        db.query(Connector).filter(Connector.id.in_(connector_ids)).delete(
            synchronize_session=False)
    # 7. the user
    db.query(User).filter(User.id == user.id).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, **counts}


# -- Connectors ---------------------------------------------------------------
@router.get("/connectors")
def list_connectors(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    connectors = db.execute(select(Connector).order_by(Connector.created_at)).scalars().all()
    out = []
    for c in connectors:
        item = ConnectorOut.model_validate(c).model_dump()
        item["online"] = hub.is_online(str(c.id))
        out.append(item)
    return out


@router.post("/connectors/pairing-code", response_model=PairingCodeResponse)
def create_pairing_code(
    body: PairingCodeRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    code = generate_pairing_code()
    connector = Connector(name=body.name, pairing_code_hash=hash_token(code),
                          status="offline", created_by=admin.id)
    db.add(connector)
    db.commit()
    db.refresh(connector)
    write_audit(db, admin.id, "connector.pairing_code", "connector", connector.id, {"name": body.name})
    # The code is shown once; only its hash is stored.
    return PairingCodeResponse(connector_id=connector.id, pairing_code=code)


@router.delete("/connectors/{connector_id}")
def revoke_connector(
    connector_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    connector = db.get(Connector, connector_id)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector not found")
    connector.token_hash = None
    connector.pairing_code_hash = None
    connector.status = "offline"
    db.commit()
    write_audit(db, admin.id, "connector.revoke", "connector", connector.id, None)
    return {"ok": True}


# -- Companies ----------------------------------------------------------------
@router.put("/companies/{company_id}/active", response_model=CompanyOut)
def set_company_active(
    company_id: uuid.UUID,
    body: CompanyActiveUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    company = db.get(TallyCompany, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    company.is_active = body.is_active
    db.commit()
    db.refresh(company)
    action = "company.activate" if body.is_active else "company.deactivate"
    write_audit(db, admin.id, action, "tally_company", company.id, {"name": company.tally_name})
    return company


@router.put("/companies/{company_id}/education-mode", response_model=CompanyOut)
def set_education_mode(
    company_id: uuid.UUID,
    body: EducationModeUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    company = db.get(TallyCompany, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    company.education_mode = body.education_mode
    if body.fy_start_date is not None:
        company.fy_start_date = body.fy_start_date
    db.commit()
    db.refresh(company)
    write_audit(db, admin.id, "company.education_mode", "tally_company", company.id, {
        "education_mode": body.education_mode,
    })
    return company


# -- Audit log ----------------------------------------------------------------
@router.get("/audit", response_model=list[AuditOut])
def read_audit(
    action: str | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
    return db.execute(query.order_by(AuditLog.created_at.desc()).limit(500)).scalars().all()
