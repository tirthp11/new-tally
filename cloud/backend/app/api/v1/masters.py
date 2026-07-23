"""Companies and the cached Tally masters the voucher grid reads."""
import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db, get_sessionmaker
from app.models import Connector, Document, Job, MastersCache, TallyCompany, User, Voucher
from app.schemas.admin import CompanyOut, EducationModeUpdate, MasterOut
from app.services.audit import write_audit
from app.services.job_queue import online_connector_for_company, run_job
from app.services.ownership import company_visible, owned_connector_ids

log = logging.getLogger(__name__)

router = APIRouter(tags=["masters"])


def _require_visible_company(db: Session, user: User, company_id: uuid.UUID) -> TallyCompany:
    company = db.get(TallyCompany, company_id)
    if company is None or not company_visible(db, user, company):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return company


def _attach_synced_by_names(db: Session, companies: list[TallyCompany]) -> None:
    """Admin-only: label each company with the operator who synced it.

    Attribution is the owner of the connector the company was synced through
    (connectors.created_by). The name is shown only when that owner is a
    non-admin operator; companies synced by an admin stay unlabelled. The value
    is a transient (non-persisted) attribute read by CompanyOut.
    """
    connector_ids = {c.connector_id for c in companies if c.connector_id is not None}
    owner_by_connector: dict[uuid.UUID, str] = {}
    if connector_ids:
        rows = db.execute(
            select(Connector.id, User.role, User.name, User.email)
            .join(User, Connector.created_by == User.id)
            .where(Connector.id.in_(connector_ids))
        ).all()
        for conn_id, role, name, email in rows:
            if role != "admin":  # admin-synced companies stay unlabelled
                owner_by_connector[conn_id] = (name or "").strip() or email
    for c in companies:
        c.synced_by_name = owner_by_connector.get(c.connector_id)


@router.get("/companies", response_model=list[CompanyOut])
def list_companies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # A user sees only companies synced through connectors they paired (docs/13);
    # admins see all.
    query = select(TallyCompany).order_by(TallyCompany.tally_name)
    owned = owned_connector_ids(db, user)
    if owned is not None:
        if not owned:
            return []
        query = query.where(TallyCompany.connector_id.in_(owned))
    companies = db.execute(query).scalars().all()
    # Only admins see who synced each company; operators never receive names.
    if user.role == "admin":
        _attach_synced_by_names(db, companies)
    return companies


@router.delete("/companies/{company_id}")
def delete_company(
    company_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permanently remove a company and EVERYTHING derived from it in this web
    app: its vouchers (so its dashboard/history/voucher-grid data disappears),
    the now-orphaned uploaded-invoice records, its job history and its masters
    cache. This is a database-only delete - it never touches Tally and never
    creates a connector job (there is no connector delete action by design,
    docs/07-SECURITY.md).

    Isolation: only rows belonging to THIS company row are removed. Companies are
    private per connector owner (docs/13), and `_require_visible_company` 404s a
    non-admin touching a company they do not own, so one user's delete can never
    affect another user's companies or vouchers.
    """
    company = _require_visible_company(db, user, company_id)
    name = company.tally_name

    vouchers = db.execute(
        select(Voucher).where(Voucher.company_id == company_id)
    ).scalars().all()
    voucher_ids = [v.id for v in vouchers]
    document_ids = {v.document_id for v in vouchers}

    write_audit(db, user.id, "company.delete", "tally_company", company_id, {
        "name": name,
        "vouchers_deleted": len(voucher_ids),
        "note": "Web-only database delete. Tally untouched.",
    })

    # Order respects foreign keys: jobs -> vouchers -> documents -> masters -> company.
    if voucher_ids:
        db.query(Job).filter(Job.voucher_id.in_(voucher_ids)).delete(synchronize_session=False)
    db.query(Job).filter(Job.company_id == company_id).delete(synchronize_session=False)
    db.query(Voucher).filter(Voucher.company_id == company_id).delete(synchronize_session=False)
    db.flush()
    # Remove uploaded-invoice records that now have no vouchers left anywhere.
    for doc_id in document_ids:
        still_used = db.execute(
            select(Voucher.id).where(Voucher.document_id == doc_id).limit(1)
        ).first()
        if still_used is None:
            doc = db.get(Document, doc_id)
            if doc is not None:
                db.delete(doc)
    db.query(MastersCache).filter(MastersCache.company_id == company_id).delete(
        synchronize_session=False)
    db.delete(company)
    db.commit()
    return {
        "ok": True,
        "deleted": name,
        "vouchers_deleted": len(voucher_ids),
        "note": "Company and its records removed from this web app only. Nothing was deleted from Tally.",
    }


async def _refresh_masters_background(connector_id: uuid.UUID, company_id: uuid.UUID,
                                      tally_name: str) -> None:
    """Fetch masters for a just-selected company without blocking the response.
    Uses its own DB session; failures are logged, never raised to the user."""
    db = get_sessionmaker()()
    try:
        ok, data = await run_job(
            db, connector_id, "fetch_masters",
            {"company": tally_name}, company_id=company_id,
        )
        if not ok:
            log.warning("Masters refresh after select failed for %s: %s", tally_name, data)
    except Exception:
        log.exception("Masters refresh after select crashed for %s", tally_name)
    finally:
        db.close()


@router.put("/companies/{company_id}/select", response_model=CompanyOut)
async def select_company(
    company_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The user picked this company in the header dropdown. Selecting activates
    it (it never deactivates anything) and refreshes its masters in the
    background so ledger dropdowns fill up without a manual sync."""
    company = _require_visible_company(db, user, company_id)
    if not company.is_active:
        company.is_active = True
        db.commit()
        db.refresh(company)
        write_audit(db, user.id, "company.select_activate", "tally_company", company.id,
                    {"name": company.tally_name})
    if company.connector_id is not None:
        cid = online_connector_for_company(db, company)
        if cid is not None:
            asyncio.create_task(_refresh_masters_background(
                cid, company.id, company.tally_name))
    return company


@router.put("/companies/{company_id}/education-mode", response_model=CompanyOut)
def set_education_mode(
    company_id: uuid.UUID,
    body: EducationModeUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Education mode toggle, reachable from the company menu (all users)."""
    company = _require_visible_company(db, user, company_id)
    company.education_mode = body.education_mode
    if body.fy_start_date is not None:
        company.fy_start_date = body.fy_start_date
    db.commit()
    db.refresh(company)
    write_audit(db, user.id, "company.education_mode", "tally_company", company.id,
                {"education_mode": body.education_mode})
    return company


@router.post("/companies/{company_id}/sync")
async def sync_company_masters(
    company_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    company = _require_visible_company(db, user, company_id)
    if company.connector_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Company has no connector assigned")
    connector_id = online_connector_for_company(db, company)
    connector = db.get(Connector, connector_id) if connector_id is not None else None
    if connector is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Connector not found")

    ok, data = await run_job(
        db, connector.id, "fetch_masters",
        {"company": company.tally_name}, company_id=company.id,
    )
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(data))
    return {"ok": True, "company": company.tally_name}


@router.get("/companies/{company_id}/masters", response_model=list[MasterOut])
def list_masters(
    company_id: uuid.UUID,
    kind: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_visible_company(db, user, company_id)
    query = select(MastersCache).where(MastersCache.company_id == company_id)
    if kind:
        query = query.where(MastersCache.kind == kind)
    return db.execute(query.order_by(MastersCache.name)).scalars().all()
