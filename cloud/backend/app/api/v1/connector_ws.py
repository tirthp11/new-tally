"""The WebSocket endpoint the desktop connector dials, plus the one-time
pairing exchange (code -> long-lived token).

Message contract: docs/04-API-AND-MESSAGES.md and shared/contracts/ws-messages.md.
"""
import asyncio
import datetime as dt
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import generate_connector_token, generate_pairing_code, hash_token
from app.db.session import get_db, get_sessionmaker
from app.models import Connector, Job, TallyCompany, User
from app.schemas.admin import PairingCodeRequest, PairingCodeResponse
from app.services import job_queue
from app.services.audit import write_audit
from app.services.connector_hub import hub

log = logging.getLogger(__name__)
router = APIRouter(tags=["connector"])


class PairRequest(BaseModel):
    pairing_code: str


class PairResponse(BaseModel):
    connector_id: uuid.UUID
    token: str


@router.post("/connector/pairing-code", response_model=PairingCodeResponse)
def create_pairing_code(
    body: PairingCodeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Any signed-in user can pair a desktop connector (sidebar action).
    The code is shown once; only its hash is stored."""
    code = generate_pairing_code()
    connector = Connector(name=body.name, pairing_code_hash=hash_token(code),
                          status="offline", created_by=user.id)
    db.add(connector)
    db.commit()
    db.refresh(connector)
    write_audit(db, user.id, "connector.pairing_code", "connector", connector.id,
                {"name": body.name})
    return PairingCodeResponse(connector_id=connector.id, pairing_code=code)


@router.get("/connector/status")
def connector_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lightweight status for the sidebar dot. Scoped to the caller's OWN
    connectors (created_by), for everyone including admins: a connector is only
    'online' for the user who paired it - another user, or an admin, sees their
    own connectors only (docs/23)."""
    connectors = db.execute(
        select(Connector).where(Connector.created_by == user.id)
    ).scalars().all()
    online = [c.name for c in connectors if hub.is_online(str(c.id))]
    last_seen = max((c.last_seen_at for c in connectors if c.last_seen_at), default=None)
    return {
        "online": len(online) > 0,
        "online_names": online,
        "total": len(connectors),
        "last_seen_at": last_seen.isoformat() if last_seen else None,
    }


@router.get("/connector/list")
def list_my_connectors(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The caller's own connectors (pairing history), newest first.

    Returns the name, live status and whether a code is still waiting to be
    used - never the pairing code or any hash. code_pending exists because codes
    do not expire: one generated days ago is still valid, and without this flag
    the modal cannot tell, so the user generates another and ends up with a
    second connector row for the same machine."""
    connectors = db.execute(
        select(Connector)
        .where(Connector.created_by == user.id)
        .order_by(Connector.created_at.desc())
    ).scalars().all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "online": hub.is_online(str(c.id)),
            "code_pending": c.pairing_code_hash is not None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in connectors
    ]


@router.post("/connector/{connector_id}/pairing-code", response_model=PairingCodeResponse)
def repair_connector_code(
    connector_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Issue a fresh pairing code for an EXISTING connector, keeping its id.

    A code is single-use (see pair_connector below) and only its hash is ever
    stored, so an old code can never be reused - the plaintext is gone. Without
    this route the only way back is "Generate code", which creates a brand new
    connector row: the companies already synced stay bound to the dead row, and
    pushes then depend on the same-owner fallback in job_queue.online_connector_
    for_company. Re-coding the existing row keeps company.connector_id valid, so
    the synced companies and their masters keep working with no re-sync.

    token_hash is deliberately left alone: the currently paired machine keeps
    working until someone actually redeems this code, and pair_connector then
    overwrites token_hash - which invalidates the old one at that moment. That
    avoids a window where no machine can push.
    """
    connector = db.get(Connector, connector_id)
    if connector is None or connector.created_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector not found")
    code = generate_pairing_code()
    connector.pairing_code_hash = hash_token(code)
    db.commit()
    write_audit(db, user.id, "connector.repair_code", "connector", connector.id,
                {"name": connector.name})
    # The code is shown once; only its hash is stored.
    return PairingCodeResponse(connector_id=connector.id, pairing_code=code)


@router.delete("/connector/{connector_id}")
def delete_my_connector(
    connector_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete one of the caller's own connectors (a pairing-history entry).

    Blocked (409) if any company was synced through it - the user must delete
    those companies first, so their masters/vouchers are never orphaned. This is
    a database-only delete; it never touches Tally (docs/07, docs/23)."""
    connector = db.get(Connector, connector_id)
    if connector is None or connector.created_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector not found")
    company_count = db.execute(
        select(func.count()).select_from(TallyCompany)
        .where(TallyCompany.connector_id == connector_id)
    ).scalar_one()
    if company_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This connector has synced companies. Delete those companies first, "
            "then delete the connector.",
        )
    name = connector.name
    # Drop any queued jobs that reference this connector before removing the row.
    db.query(Job).filter(Job.connector_id == connector_id).delete(synchronize_session=False)
    db.delete(connector)
    write_audit(db, user.id, "connector.delete", "connector", connector_id, {"name": name})
    return {"ok": True}


@router.post("/connector/pair", response_model=PairResponse)
def pair_connector(body: PairRequest):
    """Exchange a one-time pairing code for a long-lived connector token.
    The code stops working immediately after a successful exchange."""
    db = get_sessionmaker()()
    try:
        code_hash = hash_token(body.pairing_code.strip().upper())
        connector = db.execute(
            select(Connector).where(Connector.pairing_code_hash == code_hash)
        ).scalar_one_or_none()
        if connector is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid pairing code")
        token = generate_connector_token()
        connector.token_hash = hash_token(token)
        connector.pairing_code_hash = None  # single use
        db.commit()
        return PairResponse(connector_id=connector.id, token=token)
    finally:
        db.close()


@router.websocket("/connector/ws")
async def connector_ws(websocket: WebSocket):
    await websocket.accept()

    # -- Handshake: first message must be hello with a valid token ------------
    try:
        hello = await websocket.receive_json()
    except (WebSocketDisconnect, Exception):
        await websocket.close()
        return

    connector = None
    if hello.get("type") == "hello" and hello.get("token"):
        db = get_sessionmaker()()
        try:
            connector = db.execute(
                select(Connector).where(Connector.token_hash == hash_token(hello["token"]))
            ).scalar_one_or_none()
            if connector is not None:
                connector.status = "online"
                connector.app_version = hello.get("app_version")
                connector.last_seen_at = dt.datetime.now(dt.timezone.utc)
                db.commit()
        finally:
            db.close()

    if connector is None:
        await websocket.send_json({"type": "auth_error", "reason": "invalid token"})
        await websocket.close()
        return

    connector_id = connector.id
    await websocket.send_json({"type": "auth_ok", "connector_id": str(connector_id)})
    await hub.register(str(connector_id), websocket)

    # Post-connect work (deliver queued jobs, refresh the company list) MUST
    # run as a background task: awaiting a job result here would deadlock,
    # because results arrive on the receive loop below.
    asyncio.create_task(_post_connect(connector_id))

    # -- Main receive loop -----------------------------------------------------
    try:
        while True:
            message = await websocket.receive_json()
            mtype = message.get("type")

            if mtype == "ping":
                await websocket.send_json({"type": "pong"})
                _touch(connector_id)

            elif mtype == "job_result":
                handled = hub.handle_result(message)
                if not handled:
                    # No coroutine is waiting (e.g. server restarted): persist anyway.
                    _persist_orphan_result(message)

            elif mtype == "sync_now":
                # The user clicked "Sync companies & ledgers to cloud" in the
                # connector window. Runs in a background task for the same
                # reason as _post_connect: this loop must stay free to
                # receive the job results the sync waits for.
                asyncio.create_task(_sync_and_reply(connector_id, websocket))

            elif mtype == "sync_company":
                # The user clicked "Sync ledgers" on a single company row in the
                # connector window (docs/14). Background task, same reason.
                asyncio.create_task(
                    _sync_company_and_reply(connector_id, message.get("company"), websocket))

    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # Raised by starlette when this socket was closed server-side - which
        # happens when the same connector opens a NEW connection and the hub
        # replaces this one. Normal handover, not an error.
        log.info("Connection replaced by a newer one: %s", connector_id)
    except Exception:
        log.exception("Connector socket error: %s", connector_id)
    finally:
        await hub.unregister(str(connector_id), websocket)
        # Only mark the connector offline if no other live connection took over.
        if not hub.is_online(str(connector_id)):
            db = get_sessionmaker()()
            try:
                fresh = db.get(Connector, connector_id)
                if fresh is not None:
                    fresh.status = "offline"
                    db.commit()
            finally:
                db.close()


async def _post_connect(connector_id: uuid.UUID) -> None:
    # Nothing is synced automatically on connect (docs/14): only jobs the user
    # explicitly queued while the connector was offline are delivered. Company
    # discovery + masters happen when the user clicks "Sync ledgers".
    db = get_sessionmaker()()
    try:
        fresh = db.get(Connector, connector_id)
        if fresh is not None:
            await job_queue.deliver_pending(db, fresh)
    except Exception:
        log.exception("Post-connect delivery failed for connector %s", connector_id)
    finally:
        db.close()


async def _sync_and_reply(connector_id: uuid.UUID, websocket: WebSocket) -> None:
    db = get_sessionmaker()()
    try:
        fresh = db.get(Connector, connector_id)
        if fresh is None:
            return
        result = await job_queue.sync_connector(db, fresh)
        try:
            await websocket.send_json({"type": "sync_done", **result})
        except Exception:
            pass  # socket may have dropped; the sync results are already stored
    except Exception:
        log.exception("sync_now failed for connector %s", connector_id)
    finally:
        db.close()


async def _sync_company_and_reply(
    connector_id: uuid.UUID, company_name: str | None, websocket: WebSocket
) -> None:
    db = get_sessionmaker()()
    try:
        fresh = db.get(Connector, connector_id)
        if fresh is None:
            return
        result = await job_queue.sync_one_company(db, fresh, company_name or "")
        try:
            await websocket.send_json({"type": "company_synced", **result})
        except Exception:
            pass  # socket may have dropped; the masters are already stored
    except Exception:
        log.exception("sync_company failed for connector %s", connector_id)
    finally:
        db.close()


def _touch(connector_id: uuid.UUID) -> None:
    db = get_sessionmaker()()
    try:
        connector = db.get(Connector, connector_id)
        if connector is not None:
            connector.last_seen_at = dt.datetime.now(dt.timezone.utc)
            connector.status = "online"
            db.commit()
    finally:
        db.close()


def _persist_orphan_result(message: dict) -> None:
    """Persist a job_result that no coroutine is waiting for."""
    job_id = message.get("job_id")
    if not job_id:
        return
    db = get_sessionmaker()()
    try:
        job = db.get(Job, uuid.UUID(str(job_id)))
        if job is not None and job.status in ("pending", "sent"):
            ok = bool(message.get("ok"))
            job.status = "done" if ok else "error"
            job.result_json = message.get("data") if ok else {"error": message.get("error")}
            job.completed_at = dt.datetime.now(dt.timezone.utc)
            db.commit()
            if ok:
                job_queue.apply_side_effects(db, job, message.get("data") or {})
    except Exception:
        log.exception("Failed to persist orphan job result %s", job_id)
    finally:
        db.close()
