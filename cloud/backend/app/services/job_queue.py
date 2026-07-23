"""Create connector jobs, deliver them over the WebSocket hub, persist results.

Only three actions exist: list_companies, fetch_masters, post_tally_xml.
There is deliberately no delete action (docs/07-SECURITY.md).
"""
import datetime as dt
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Connector, Job, MastersCache, TallyCompany
from app.models.job import JOB_ACTIONS
from app.services.connector_hub import ConnectorOffline, hub

log = logging.getLogger(__name__)


def online_connector_for_company(db: Session, company: TallyCompany) -> uuid.UUID | None:
    """Pick a connector to run this company's jobs on.

    Re-pairing creates a fresh Connector row each time, and a per-company sync
    binds the company to whichever connector was live then. So a company can end
    up bound to an older, now-offline connector while a newer one owned by the
    same user is online. Since every connector on that machine talks to the same
    local Tally (and post_tally_xml targets the company by name), we prefer the
    company's own connector when it is online, then fall back to any online
    connector owned by the same user. Returns None only when nothing is usable.
    """
    own = company.connector_id
    if own is not None and hub.is_online(str(own)):
        return own

    owner_id = None
    if own is not None:
        owner = db.get(Connector, own)
        owner_id = owner.created_by if owner is not None else None
    if owner_id is None:
        return own  # unknown owner: keep the original so the caller reports offline

    for c in db.execute(
        select(Connector).where(
            Connector.created_by == owner_id,
            Connector.token_hash.is_not(None),
        )
    ).scalars().all():
        if hub.is_online(str(c.id)):
            return c.id
    return own


async def run_job(
    db: Session,
    connector_id: uuid.UUID,
    action: str,
    payload: dict,
    company_id: uuid.UUID | None = None,
    voucher_id: uuid.UUID | None = None,
) -> tuple[bool, dict | str]:
    """Create a job row, send it, wait for the result and persist it.

    Returns (ok, data) on success or (False, error_message) on failure.
    """
    if action not in JOB_ACTIONS:
        raise ValueError(f"Unknown job action: {action}")

    job = Job(
        connector_id=connector_id,
        company_id=company_id,
        voucher_id=voucher_id,
        action=action,
        payload_json=payload,
        status="pending",
    )
    db.add(job)
    db.commit()

    settings = get_settings()
    try:
        job.status = "sent"
        db.commit()
        message = await hub.send_job_and_wait(
            str(connector_id), str(job.id), action, payload,
            timeout=settings.connector_job_timeout_seconds,
        )
    except ConnectorOffline:
        job.status = "pending"  # stays queued; delivered on reconnect
        db.commit()
        return False, "Connector is offline. The job is queued and will run when it reconnects."
    except TimeoutError:
        job.status = "error"
        job.result_json = {"error": "Connector did not respond in time."}
        job.completed_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        return False, "Connector did not respond in time."

    return _finish_job(db, job, message)


def _finish_job(db: Session, job: Job, message: dict) -> tuple[bool, dict | str]:
    ok = bool(message.get("ok"))
    job.status = "done" if ok else "error"
    job.result_json = message.get("data") if ok else {"error": message.get("error")}
    job.completed_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    if ok:
        apply_side_effects(db, job, message.get("data") or {})
        return True, message.get("data") or {}
    return False, str(message.get("error") or "Connector reported an error.")


def apply_side_effects(db: Session, job: Job, data: dict) -> None:
    """Persist what a successful job result means for the database."""
    if job.action == "list_companies":
        names = data.get("companies") or []
        # A per-company "Sync ledgers" runs list_companies only to VERIFY the one
        # clicked company is open in Tally - it must not import every other
        # company into the cloud. When the job carries an "only" marker, keep
        # just that name so the web dropdown gains exactly the company the user
        # synced (docs/14). A full "sync all" (no marker) still upserts all.
        only = (job.payload_json or {}).get("only")
        if only:
            wanted = str(only).strip()
            names = [n for n in names if (n or "").strip() == wanted]
        upsert_companies(db, job.connector_id, names)
    elif job.action == "fetch_masters" and job.company_id is not None:
        company = db.get(TallyCompany, job.company_id)
        if company is not None:
            store_masters(db, company, data)


def upsert_companies(db: Session, connector_id: uuid.UUID, names: list[str]) -> None:
    # Companies are unique per (connector_id, tally_name) so each user's paired
    # connector keeps its own private rows even for same-named companies (docs/13).
    for name in names:
        clean = (name or "").strip()
        if not clean:
            continue
        existing = db.execute(
            select(TallyCompany).where(
                TallyCompany.tally_name == clean,
                TallyCompany.connector_id == connector_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(TallyCompany(tally_name=clean, connector_id=connector_id, is_active=False))
    db.commit()
    log.info("Companies upserted for connector %s: %s", connector_id, names)


def store_masters(db: Session, company: TallyCompany, data: dict) -> None:
    """Rebuild the masters cache for a company from a fetch_masters result."""
    db.query(MastersCache).filter(MastersCache.company_id == company.id).delete()

    for led in data.get("ledgers") or []:
        db.add(MastersCache(company_id=company.id, kind="ledger",
                            name=led.get("name", ""), parent=led.get("parent")))
    for grp in data.get("groups") or []:
        name = grp.get("name") if isinstance(grp, dict) else grp
        db.add(MastersCache(company_id=company.id, kind="group", name=name or ""))
    for unit in data.get("units") or []:
        name = unit.get("name") if isinstance(unit, dict) else unit
        db.add(MastersCache(company_id=company.id, kind="unit", name=name or ""))
    for si in data.get("stock_items") or []:
        db.add(MastersCache(company_id=company.id, kind="stock",
                            name=si.get("name", ""), parent=si.get("unit")))

    start_date = (data.get("company_start_date") or "").strip()
    if len(start_date) == 8 and start_date.isdigit():
        company.fy_start_date = start_date
    company.last_synced_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    log.info("Masters cache rebuilt for company %s", company.tally_name)


async def sync_connector(db: Session, connector: Connector) -> dict:
    """Data flow A: list companies, then refresh masters for active companies."""
    ok, data = await run_job(db, connector.id, "list_companies", {})
    if not ok:
        return {"ok": False, "error": data}

    synced: list[str] = []
    companies = db.execute(
        select(TallyCompany).where(
            TallyCompany.connector_id == connector.id,
            TallyCompany.is_active.is_(True),
        )
    ).scalars().all()
    for company in companies:
        ok_m, data_m = await run_job(
            db, connector.id, "fetch_masters",
            {"company": company.tally_name}, company_id=company.id,
        )
        if ok_m:
            synced.append(company.tally_name)
        else:
            log.warning("fetch_masters failed for %s: %s", company.tally_name, data_m)
    return {"ok": True, "companies_seen": data.get("companies", []), "masters_synced": synced}


async def sync_one_company(db: Session, connector: Connector, company_name: str) -> dict:
    """Sync a single company's masters, triggered per-row from the connector.

    Discovers the company if the cloud has not seen it yet, marks it active so it
    becomes selectable and pushable in the web app, then fetches its masters.
    Companies stay private to the connector owner (docs/13)."""
    clean = (company_name or "").strip()
    if not clean:
        return {"ok": False, "error": "No company name given."}

    def _find():
        return db.execute(
            select(TallyCompany).where(
                TallyCompany.connector_id == connector.id,
                TallyCompany.tally_name == clean,
            )
        ).scalar_one_or_none()

    company = _find()
    if company is None:
        # The cloud has not listed this company yet - discover it first. The
        # "only" marker limits the upsert to THIS company so clicking "Sync
        # ledgers" on one row never imports the other companies open in Tally.
        ok, data = await run_job(db, connector.id, "list_companies", {"only": clean})
        if not ok:
            return {"ok": False, "company": clean, "error": data}
        company = _find()
    if company is None:
        return {"ok": False, "company": clean,
                "error": f"'{clean}' is not open in Tally."}

    if not company.is_active:
        company.is_active = True
        db.commit()

    ok, data = await run_job(
        db, connector.id, "fetch_masters",
        {"company": company.tally_name}, company_id=company.id,
    )
    if not ok:
        return {"ok": False, "company": clean, "error": data}
    return {
        "ok": True,
        "company": clean,
        "counts": {
            "ledgers": len(data.get("ledgers") or []),
            "stock_items": len(data.get("stock_items") or []),
        },
    }


async def deliver_pending(db: Session, connector: Connector) -> None:
    """Deliver jobs that were created while the connector was offline."""
    settings = get_settings()
    pending = db.execute(
        select(Job).where(Job.connector_id == connector.id, Job.status == "pending")
        .order_by(Job.created_at)
    ).scalars().all()
    for job in pending:
        try:
            job.status = "sent"
            db.commit()
            message = await hub.send_job_and_wait(
                str(connector.id), str(job.id), job.action, job.payload_json or {},
                timeout=settings.connector_job_timeout_seconds,
            )
        except (ConnectorOffline, TimeoutError):
            job.status = "pending"
            db.commit()
            return
        _finish_job(db, job, message)
