"""Review data, push, list, delete.

INVARIANT (docs/07-SECURITY.md): the delete endpoint below sets deleted_at and
writes an audit row. It contains no code that creates a connector job, and no
connector action exists that could delete from Tally.
"""
import copy
import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import Document, MastersCache, TallyCompany, User, Voucher
from app.schemas.voucher import (
    DatePreview,
    DraftResponse,
    VoucherCreate,
    VoucherDetail,
    VoucherOut,
    VoucherPush,
    VoucherUpdate,
)
from app.services import push_service, resolver
from app.services.amounts import display_amount
from app.services.audit import write_audit
from app.services.ownership import company_visible, owned_connector_ids

router = APIRouter(prefix="/vouchers", tags=["vouchers"])


def _masters_for_company(db: Session, company_id: uuid.UUID | None) -> dict:
    masters = {"ledgers": [], "stock": [], "units": [], "groups": []}
    if company_id is None:
        return masters
    rows = db.execute(
        select(MastersCache).where(MastersCache.company_id == company_id)
    ).scalars().all()
    kind_map = {"ledger": "ledgers", "stock": "stock", "unit": "units", "group": "groups"}
    for row in rows:
        key = kind_map.get(row.kind)
        if key:
            masters[key].append(row.name)
    return masters


def _get_voucher(db: Session, voucher_id: uuid.UUID) -> Voucher:
    voucher = db.get(Voucher, voucher_id)
    if voucher is None or voucher.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voucher not found")
    return voucher


def _check_company(voucher: Voucher, company_id: uuid.UUID | None) -> None:
    """Enforce company isolation: a voucher may only be accessed under the
    company it belongs to. When company_id is None the caller did not scope the
    request (e.g. a plain detail load) and no check is applied."""
    if (company_id is not None and voucher.company_id is not None
            and voucher.company_id != company_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voucher not found for this company")


def _owner_filter(query, user: User):
    """Per-user isolation (docs/12): a non-admin only sees vouchers behind
    documents they uploaded. Ownership is derived from Document.uploaded_by;
    admins see everyone's data."""
    if user.role != "admin":
        query = (query.join(Document, Voucher.document_id == Document.id)
                 .where(Document.uploaded_by == user.id))
    return query


def _attach_uploader_names(db: Session, vouchers: list[Voucher]) -> None:
    """Admin-only: label each voucher with the operator who uploaded its source
    document (documents.uploaded_by). The name is shown only when that uploader
    is a non-admin operator; admin uploads stay unlabelled. The value is a
    transient (non-persisted) attribute read by VoucherOut."""
    document_ids = {v.document_id for v in vouchers}
    name_by_document: dict[uuid.UUID, str] = {}
    if document_ids:
        rows = db.execute(
            select(Document.id, User.role, User.name, User.email)
            .join(User, Document.uploaded_by == User.id)
            .where(Document.id.in_(document_ids))
        ).all()
        for doc_id, role, name, email in rows:
            if role != "admin":  # admin uploads stay unlabelled
                name_by_document[doc_id] = (name or "").strip() or email
    for v in vouchers:
        v.uploaded_by_name = name_by_document.get(v.document_id)


def _check_owner(db: Session, voucher: Voucher, user: User) -> None:
    """404 for a non-admin trying to touch a voucher they do not own."""
    if user.role == "admin":
        return
    doc = db.get(Document, voucher.document_id)
    if doc is None or doc.uploaded_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voucher not found")


@router.post("", response_model=DraftResponse)
def create_draft(
    body: VoucherCreate,
    company_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.get(Document, body.document_id)
    if doc is None or doc.status != "extracted" or not doc.extracted_json:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Document has no extracted data")
    # A non-admin may only build a voucher from their own upload.
    if user.role != "admin" and doc.uploaded_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    if company_id is None:
        q = (select(TallyCompany).where(TallyCompany.is_active.is_(True))
             .order_by(TallyCompany.tally_name))
        owned = owned_connector_ids(db, user)
        if owned is not None and not owned:
            first_active = None
        else:
            if owned is not None:
                q = q.where(TallyCompany.connector_id.in_(owned))
            first_active = db.execute(q).scalars().first()
        company_id = first_active.id if first_active else None
    elif not company_visible(db, user, db.get(TallyCompany, company_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

    # Work on a copy so the document keeps the AI output exactly as extracted.
    # Charge-like lines (freight/packing/outward...) are ledgers, not stock
    # items - move them before building suggestions, same as the reference script.
    invoice = copy.deepcopy(doc.extracted_json)
    resolver.route_charges(invoice)
    suggestions = resolver.build_suggestions(invoice, _masters_for_company(db, company_id))

    voucher = Voucher(
        document_id=doc.id,
        company_id=company_id,
        voucher_type=invoice.get("voucher_type", "Purchase"),
        party_name=invoice.get("party_name"),
        invoice_number=invoice.get("voucher_number"),
        invoice_date=invoice.get("date"),
        amount=display_amount(invoice),
        edited_json={"invoice": invoice, "resolution": suggestions["resolution"]},
        status="draft",
    )
    db.add(voucher)
    db.commit()
    db.refresh(voucher)
    return DraftResponse(voucher=VoucherDetail.model_validate(voucher), suggestions=suggestions)


@router.get("/{voucher_id}/suggestions")
def get_suggestions(
    voucher_id: uuid.UUID,
    company_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    voucher = _get_voucher(db, voucher_id)
    _check_owner(db, voucher, user)
    _check_company(voucher, company_id)
    invoice = (voucher.edited_json or {}).get("invoice") or {}
    return resolver.build_suggestions(invoice, _masters_for_company(db, company_id))


@router.get("/{voucher_id}/date-preview", response_model=DatePreview)
def date_preview(
    voucher_id: uuid.UUID,
    company_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    voucher = _get_voucher(db, voucher_id)
    _check_owner(db, voucher, user)
    _check_company(voucher, company_id)
    company = db.get(TallyCompany, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    invoice = (voucher.edited_json or {}).get("invoice") or {}
    return push_service.compute_date_preview(invoice.get("date"), company)


@router.put("/{voucher_id}", response_model=VoucherDetail)
def save_voucher(
    voucher_id: uuid.UUID,
    body: VoucherUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    voucher = _get_voucher(db, voucher_id)
    _check_owner(db, voucher, user)
    if voucher.status == "pushed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A pushed voucher cannot be edited")
    # A voucher may not be moved to a different company once it belongs to one.
    if (body.company_id is not None and voucher.company_id is not None
            and body.company_id != voucher.company_id):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This voucher belongs to a different company and cannot be reassigned.",
        )
    edited = body.edited_json
    if "invoice" not in edited or "resolution" not in edited:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "edited_json must contain 'invoice' and 'resolution'",
        )
    voucher.edited_json = edited
    invoice = edited["invoice"]
    voucher.party_name = invoice.get("party_name")
    voucher.invoice_number = invoice.get("voucher_number")
    voucher.invoice_date = invoice.get("date")
    voucher.amount = display_amount(invoice)
    if body.company_id is not None:
        voucher.company_id = body.company_id
    db.commit()
    db.refresh(voucher)
    return voucher


@router.post("/{voucher_id}/push")
async def push(
    voucher_id: uuid.UUID,
    body: VoucherPush,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    voucher = _get_voucher(db, voucher_id)
    _check_owner(db, voucher, user)
    if voucher.status == "pushed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Voucher is already pushed")
    if voucher.company_id is not None and voucher.company_id != body.company_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This voucher belongs to a different company and cannot be pushed here.",
        )
    company = db.get(TallyCompany, body.company_id)
    if company is None or not company_visible(db, user, company):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    if not company.is_active:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This company is not active. An admin must mark it active first.",
        )
    result = await push_service.push_voucher(db, voucher, company, user.id, body.confirmed_date)
    return result


@router.get("", response_model=list[VoucherOut])
def list_vouchers(
    status_filter: str | None = None,
    company_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_deleted: bool = False,
    sort: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """sort:
      "upload_asc"  - oldest upload first, so the voucher grid lists files in the
                      order they were picked (the grid's sequence rule).
      "pushed_desc" - most recent push to Tally first, for History. Sorted on
                      pushed_at, the same field History displays and filters on.
      default       - newest upload first (unchanged legacy behaviour).

    Every branch orders DESC in SQL before LIMIT so the cap always keeps the most
    RECENT 500 rows; "upload_asc" reverses in Python afterwards. Ordering ASC in
    SQL instead would pin the cap to the oldest 500 and silently hide new
    vouchers once a company passes that many.
    """
    query = _owner_filter(select(Voucher), user)
    if not include_deleted:
        query = query.where(Voucher.deleted_at.is_(None))
    if status_filter:
        query = query.where(Voucher.status == status_filter)
    if company_id:
        query = query.where(Voucher.company_id == company_id)
    if date_from:
        query = query.where(Voucher.invoice_date >= date_from)
    if date_to:
        query = query.where(Voucher.invoice_date <= date_to)
    # Voucher.id is a tie-breaker: two rows sharing a timestamp would otherwise
    # come back in arbitrary order, and the sequence would wobble between loads.
    if sort == "pushed_desc":
        order = (Voucher.pushed_at.desc().nulls_last(),
                 Voucher.created_at.desc(), Voucher.id.desc())
    else:
        order = (Voucher.created_at.desc(), Voucher.id.desc())
    vouchers = db.execute(query.order_by(*order).limit(500)).scalars().all()
    if sort == "upload_asc":
        vouchers = list(reversed(vouchers))
    # Only admins see who uploaded each voucher; operators never receive names.
    if user.role == "admin":
        _attach_uploader_names(db, vouchers)
    return vouchers


# Declared BEFORE /{voucher_id} - otherwise "stats" is matched as a voucher id
# and rejected as an invalid UUID.
@router.get("/stats")
def voucher_stats(
    company_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Exact dashboard counts, straight from COUNT(). The list endpoint above is
    capped at 500 rows, so counting there would silently under-report once a
    company passes that many vouchers. Same owner/company isolation as the list.

    "month" is the current UTC month (YYYY-MM); the dashboard passes it back as
    the History month filter so both screens agree on which month is "this" one.
    """
    def count(*conditions) -> int:
        query = _owner_filter(select(func.count()).select_from(Voucher), user)
        query = query.where(Voucher.deleted_at.is_(None), *conditions)
        if company_id:
            query = query.where(Voucher.company_id == company_id)
        return db.execute(query).scalar_one()

    now = dt.datetime.now(dt.timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "open": count(Voucher.status.in_(("draft", "queued"))),
        "failed": count(Voucher.status == "failed"),
        "pushed_month": count(Voucher.status == "pushed",
                              Voucher.pushed_at >= month_start),
        "pushed_total": count(Voucher.status == "pushed"),
        "month": now.strftime("%Y-%m"),
    }


# Declared BEFORE /{voucher_id} - like /stats - so "grid" is not parsed as a
# voucher id.
@router.get("/grid")
def voucher_grid(
    company_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One call that returns everything the voucher grid needs: the company's
    masters once, plus every open voucher already carrying its suggestions and
    date preview. This replaces the old per-row fan-out (list + detail +
    suggestions + date-preview per voucher = 3N+4 requests) with a single
    request, so a page switch no longer pays the network round-trip 30+ times.

    The per-voucher work moved here (build_suggestions, compute_date_preview,
    the charge-routing repair) is exactly what GET /{id}, /suggestions and
    /date-preview each did on their own - the same helpers, run server-side in a
    loop where there is no network cost between them.
    """
    company = db.get(TallyCompany, company_id)
    if company is None or not company_visible(db, user, company):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

    masters = _masters_for_company(db, company_id)

    # Same filters/order/cap as the grid's old list call (sort=upload_asc): open
    # vouchers only, oldest upload first, newest 500 kept then reversed.
    query = _owner_filter(select(Voucher), user).where(
        Voucher.deleted_at.is_(None),
        Voucher.company_id == company_id,
        Voucher.status != "pushed",
    )
    vouchers = db.execute(
        query.order_by(Voucher.created_at.desc(), Voucher.id.desc()).limit(500)
    ).scalars().all()
    vouchers = list(reversed(vouchers))
    if user.role == "admin":
        _attach_uploader_names(db, vouchers)

    rows = []
    dirty = False
    for v in vouchers:
        # Repair older drafts (freight/packing/outward -> charges), identical to
        # get_voucher, so the grid shows them as ledgers not stock items.
        if v.status != "pushed" and v.edited_json:
            edited = dict(v.edited_json)
            invoice = edited.get("invoice") or {}
            if resolver.route_charges(invoice):
                edited["invoice"] = invoice
                v.edited_json = edited
                flag_modified(v, "edited_json")
                dirty = True
        invoice = (v.edited_json or {}).get("invoice") or {}
        rows.append({
            **VoucherDetail.model_validate(v).model_dump(mode="json"),
            "suggestions": resolver.build_suggestions(invoice, masters),
            "date_preview": push_service.compute_date_preview(invoice.get("date"), company),
        })
    if dirty:
        db.commit()

    return {"masters": masters, "vouchers": rows}


@router.get("/{voucher_id}", response_model=VoucherDetail)
def get_voucher(
    voucher_id: uuid.UUID,
    company_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    voucher = _get_voucher(db, voucher_id)
    _check_owner(db, voucher, user)
    _check_company(voucher, company_id)
    # Repair older drafts created before charge-routing: pull freight/packing/
    # outward lines out of items[] into charges so the grid shows them as
    # ledgers, not stock items. Pushed vouchers are left untouched.
    if voucher.status != "pushed" and voucher.edited_json:
        edited = dict(voucher.edited_json)
        invoice = edited.get("invoice") or {}
        if resolver.route_charges(invoice):
            edited["invoice"] = invoice
            voucher.edited_json = edited
            flag_modified(voucher, "edited_json")
            db.commit()
            db.refresh(voucher)
    # Only admins see who uploaded the voucher; operators never receive a name.
    if user.role == "admin":
        _attach_uploader_names(db, [voucher])
    return voucher


@router.delete("/{voucher_id}")
def delete_voucher(
    voucher_id: uuid.UUID,
    company_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete the voucher row from THIS database only. Never touches Tally.

    By design this endpoint writes an audit row and removes the record. It
    must never create a connector job - and no connector action capable of
    deleting exists anyway. Anything already imported into Tally stays in
    Tally.
    """
    from app.models import Job

    voucher = _get_voucher(db, voucher_id)
    _check_owner(db, voucher, user)
    _check_company(voucher, company_id)
    write_audit(db, user.id, "voucher.delete", "voucher", voucher.id, {
        "note": "Web-only database delete. Tally untouched.",
        "invoice_number": voucher.invoice_number,
        "status_at_delete": voucher.status,
    })
    # Remove this voucher's job history rows (its post_tally_xml push records),
    # then the voucher and its now-orphaned document metadata. The push itself
    # stays recorded in the audit log, so accountability is preserved.
    db.query(Job).filter(Job.voucher_id == voucher.id).delete(synchronize_session=False)
    document_id = voucher.document_id
    db.delete(voucher)
    db.flush()
    others = db.execute(
        select(Voucher).where(Voucher.document_id == document_id)
    ).scalars().first()
    if others is None:
        doc = db.get(Document, document_id)
        if doc is not None:
            db.delete(doc)
    db.commit()
    return {"ok": True, "note": "Removed from this web app only. Nothing was deleted from Tally."}
