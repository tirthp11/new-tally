"""The "Push to Tally" orchestration (docs/06-WEB-APP.md section 3,
revised by docs/09-UI-UX-OVERHAUL.md section 6).

Sequence: date handling per education mode -> create-only pre-check against
the masters cache -> build missing masters from the user's create-new choices
-> generate and validate XML -> queue connector jobs -> read Tally's response
-> update status and audit.

There is NO self-heal retry: the first failure stops the push and the reason
is stored on the voucher for the UI. Existing masters are never altered -
an attempted alter is a hard failure.

All AI calls run in a worker thread so the event loop stays responsive.
"""
import asyncio
import copy
import datetime as dt
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MastersCache, TallyCompany, Voucher
from app.services import resolver
from app.services.audit import write_audit
from app.services.connector_hub import hub
from app.services.job_queue import online_connector_for_company, run_job
from app.services.tally_xml.healer import split_date_errors
from app.services.tally_xml.master_builder import build_masters_xml
from app.services.tally_xml.response import check_tally_response
from app.services.tally_xml.validate import balance_difference
from app.services.tally_xml.voucher_builder import build_voucher_xml

log = logging.getLogger(__name__)

EDU_ALLOWED_DAYS = ("01", "02", "31")


def compute_date_preview(extracted_date: str | None, company: TallyCompany) -> dict:
    """What push will do to the invoice date. Never applied silently -
    the review screen shows this before the user pushes."""
    date = (extracted_date or "").strip()
    if not company.education_mode:
        return {
            "extracted_date": date or None,
            "final_date": date or None,
            "changed": False,
            "reason": "Education mode is off: the AI/OCR extracted date is used as-is.",
        }

    final = date
    reasons = []
    fy = (company.fy_start_date or "").strip()
    if len(fy) == 8 and len(final) == 8 and fy[:4] != final[:4]:
        final = fy[:4] + final[4:]
        reasons.append(f"year shifted to the Tally financial year {fy[:4]}")
    if len(final) == 8 and final[6:] not in EDU_ALLOWED_DAYS:
        final = final[:6] + "01"
        reasons.append("day coerced to the 1st (educational Tally accepts 1st/2nd/31st only)")

    return {
        "extracted_date": date or None,
        "final_date": final or None,
        "changed": final != date,
        "reason": "; ".join(reasons) if reasons else "No change needed for education mode.",
    }


def _apply_resolution_names(invoice: dict, resolution: dict) -> dict:
    """Write the user's final master choices into the invoice copy used for XML."""

    def final_name(entry: dict | None) -> str | None:
        return entry.get("name") if entry else None

    def match_entry(entries: list[dict], value: str | None) -> dict | None:
        v = (value or "").strip().upper()
        for e in entries:
            if (e.get("extracted") or "").strip().upper() == v:
                return e
            if (e.get("name") or "").strip().upper() == v:
                return e
        return None

    party = final_name(resolution.get("party"))
    if party:
        invoice["party_name"] = party

    purchase = final_name(resolution.get("purchase"))
    units = resolution.get("units", [])
    stock_items = resolution.get("stock_items", [])
    # Stock items map back to lines by POSITION (index), never by name, so two
    # lines with the same extracted name can never cross-contaminate each other.
    stock_by_index = {e["index"]: e for e in stock_items if e.get("index") is not None}

    kept_items = []
    moved_charges = []
    stock_pos = 0
    for item in invoice.get("items", []):
        sname = item.get("stock_item_name")
        # GST never becomes a stock item or a charge - it is emitted only from
        # invoice["gst"]. Drop any GST line the extraction left in items[].
        if resolver.is_gst_name(sname):
            continue
        # Charge-like lines become charge ledger entries, not stock items
        # (same routing as the interactive script).
        if resolver.is_charge_name(sname):
            moved_charges.append({
                "ledger_name": sname,
                "amount": float(item.get("amount", 0) or 0),
            })
            continue
        ue = match_entry(units, item.get("unit"))
        if ue and ue.get("name"):
            item["unit"] = ue["name"]
        # k-th kept stock line -> the resolution entry with index k (positional
        # fallback if an older entry lacks an index).
        se = stock_by_index.get(stock_pos)
        if se is None and stock_pos < len(stock_items):
            se = stock_items[stock_pos]
        if se and se.get("name"):
            item["stock_item_name"] = se["name"]
        if purchase:
            item["purchase_ledger"] = purchase
        kept_items.append(item)
        stock_pos += 1

    invoice["items"] = kept_items
    if moved_charges:
        invoice.setdefault("additional_charges", []).extend(moved_charges)
        new_taxable = round(sum(float(i.get("amount", 0) or 0) for i in kept_items), 2)
        invoice["taxable_value"] = new_taxable

    # Drop GST lines the extraction wrongly placed in additional_charges so they
    # are never built into the voucher XML as charge ledgers (GST comes only from
    # invoice["gst"]). Without this, GST would be posted to Tally twice.
    if invoice.get("additional_charges"):
        invoice["additional_charges"] = [
            c for c in invoice["additional_charges"]
            if not resolver.is_gst_name(c.get("ledger_name"))
        ]

    charges = resolution.get("charges", [])
    for charge in invoice.get("additional_charges", []):
        ce = match_entry(charges, charge.get("ledger_name"))
        if ce and ce.get("name"):
            charge["ledger_name"] = ce["name"]

    gst = invoice.get("gst", {}) or {}
    confirmed_roles = set()
    for gl in resolution.get("gst_ledgers", []):
        role = gl.get("role")
        if role in ("cgst_ledger", "sgst_ledger", "igst_ledger") and gl.get("name"):
            gst[role] = gl["name"]
            confirmed_roles.add(role)

    # The extraction fills all three GST legs even when only one applies (e.g. a
    # Gujarat invoice carries a zero-amount IGST leg alongside the real CGST/SGST).
    # That ambiguity makes the voucher AI sometimes emit the wrong (zero) leg and
    # unbalance the voucher. When the user's resolution confirms which legs apply,
    # blank every non-confirmed leg so only the correct one reaches the builder.
    if confirmed_roles:
        for role in ("cgst_ledger", "sgst_ledger", "igst_ledger"):
            if role not in confirmed_roles:
                gst[role] = ""
                gst[role.replace("_ledger", "_rate")] = 0
                gst[role.replace("_ledger", "_amount")] = 0

    return invoice


async def push_voucher(
    db: Session,
    voucher: Voucher,
    company: TallyCompany,
    actor_id: uuid.UUID,
    confirmed_date: str | None = None,
) -> dict:
    """Run the full push. Returns {"ok": bool, "errors": [...], ...}."""
    edited = voucher.edited_json or {}
    invoice = copy.deepcopy(edited.get("invoice") or {})
    resolution = edited.get("resolution") or {}
    if not invoice:
        return {"ok": False, "errors": ["Voucher has no invoice data to push."]}
    if company.connector_id is None:
        return {"ok": False, "errors": ["This company has no connector assigned."]}
    # Route to a currently-online connector for this company's owner (a company
    # may be bound to an older connector row from a previous pairing).
    connector_id = online_connector_for_company(db, company)
    if connector_id is None or not hub.is_online(str(connector_id)):
        return {"ok": False, "errors": [
            "The desktop connector is offline. Open the connector on the Tally "
            "PC and wait until it shows 'Cloud: connected', then push again."]}

    # 1. Date handling - already previewed to the user; confirmed_date wins.
    preview = compute_date_preview(invoice.get("date"), company)
    invoice["date"] = confirmed_date or preview["final_date"] or invoice.get("date")

    # Apply the user's master choices to the XML input.
    invoice = _apply_resolution_names(invoice, resolution)

    # 2. Masters the user chose to create.
    missing = resolver.build_missing_masters(resolution)
    has_missing = bool(missing["ledgers"] or missing["stock_items"] or missing["units"])

    # 2a. Create-only pre-check: a "create new" name that already exists in
    # Tally's cached masters would make Tally ALTER it - never allowed.
    if has_missing:
        duplicates = _find_existing_masters(db, company.id, missing)
        if duplicates:
            return _fail(db, voucher, actor_id, duplicates, [], stage="precheck")

    voucher.status = "queued"
    voucher.company_id = company.id
    voucher.invoice_date = invoice.get("date")
    db.commit()

    errors_log: list[dict] = []

    # Build all XML up front, OUTSIDE the Tally-write lock below, so several
    # vouchers pushed at once generate their (slow) AI XML in parallel. The XML
    # content depends only on the resolved invoice, never on Tally state, so it
    # is safe to build the voucher XML before the masters are posted.
    masters_xml = None
    if has_missing:
        try:
            masters_xml = await asyncio.to_thread(build_masters_xml, missing, company.tally_name, invoice)
        except ValueError as e:
            return _fail(db, voucher, actor_id, [str(e)], errors_log, stage="masters")
        log.info(
            "AI-generated MASTERS XML for voucher %s (company %s, creating: %s):\n%s",
            voucher.id, company.tally_name,
            ", ".join([m["name"] for m in missing["ledgers"]] +
                      [m["name"] for m in missing["stock_items"]] +
                      [m["name"] for m in missing["units"]]),
            masters_xml,
        )

    # Voucher XML. One attempt only - no self-heal retries.
    try:
        voucher_xml = await asyncio.to_thread(build_voucher_xml, invoice, company.tally_name)
    except ValueError as e:
        return _fail(db, voucher, actor_id, [str(e)], errors_log, stage="voucher")
    log.info(
        "AI-generated VOUCHER XML for voucher %s (invoice %s, company %s):\n%s",
        voucher.id, invoice.get("voucher_number"), company.tally_name, voucher_xml,
    )

    # Pre-flight balance guard: never send an unbalanced voucher to Tally. A
    # voucher whose debits != credits is rejected with EXCEPTIONS, but Tally
    # still keeps the broken copy in its exception list - so the user would have
    # to clean up in BOTH Tally and here. Catching it before the write leaves
    # Tally untouched, so a fix-and-retry only ever happens on the web side.
    diff = balance_difference(voucher_xml)
    if diff is not None and abs(diff) >= 0.01:
        return _fail(db, voucher, actor_id, [
            f"Not sent to Tally: the voucher does not balance by Rs. {abs(diff):.2f} "
            "(debits do not equal credits - check the Amount against items + GST + "
            "charges). Nothing was created in Tally, so just fix the value and push "
            "again."
        ], errors_log, stage="balance")

    # Serialize the Tally writes for this connector: this voucher's masters (if
    # any) land immediately before its own voucher, and no other voucher pushed
    # at the same time can interleave between them. Held only around the connector
    # jobs, so the AI generation above stays parallel across vouchers.
    async with hub.post_lock(str(connector_id)):
        # 3-5. Masters first, if anything to create. Any failure stops the push -
        # the voucher is never attempted on top of broken masters.
        if masters_xml is not None:
            ok, data = await run_job(
                db, connector_id, "post_tally_xml",
                {"company": company.tally_name, "label": "Create Masters", "xml": masters_xml},
                company_id=company.id, voucher_id=voucher.id,
            )
            if not ok:
                return _fail(db, voucher, actor_id, [str(data)], errors_log, stage="connector")
            m_ok, m_errs, m_info = check_tally_response(data.get("raw_response", ""), "Create Masters")
            if not m_ok:
                return _fail(db, voucher, actor_id, m_errs, errors_log, stage="masters")
            if m_info.get("altered", 0) > 0:
                return _fail(db, voucher, actor_id, [
                    "Tally reports an existing ledger was altered "
                    f"(Altered={m_info['altered']}). Updating ledgers is not allowed "
                    "from this app. Choose the existing master from the dropdown "
                    "instead of creating a new one.",
                ], errors_log, stage="masters")

        # 6. Voucher import.
        ok, data = await run_job(
            db, connector_id, "post_tally_xml",
            {"company": company.tally_name, "label": "Voucher Import", "xml": voucher_xml},
            company_id=company.id, voucher_id=voucher.id,
        )
        if not ok:
            return _fail(db, voucher, actor_id, [str(data)], errors_log, stage="connector")

        v_ok, v_errs, info = check_tally_response(data.get("raw_response", ""), "Voucher Import")

    # Classification, not retry: education-mode pushes produce harmless
    # date notices; only-date warnings still count as success.
    if not v_ok and v_errs:
        date_errs, real_errs = split_date_errors(v_errs)
        if date_errs and not real_errs:
            log.warning("Date-related warnings only, accepting push: %s", date_errs)
            v_ok = True
            v_errs = []

    if not v_ok:
        return _fail(db, voucher, actor_id, v_errs, errors_log, stage="voucher")

    voucher.status = "pushed"
    voucher.pushed_at = dt.datetime.now(dt.timezone.utc)
    voucher.tally_voucher_number = invoice.get("voucher_number")
    voucher.tally_masterid = info.get("last_vch_id")
    voucher.error_log = {"history": errors_log} if errors_log else None
    db.commit()
    write_audit(db, actor_id, "voucher.push", "voucher", voucher.id, {
        "company": company.tally_name,
        "invoice_number": invoice.get("voucher_number"),
        "amount": invoice.get("total_amount"),
        "date_used": invoice.get("date"),
        "date_preview": preview,
    })
    return {"ok": True, "tally_voucher_number": voucher.tally_voucher_number}


def _find_existing_masters(db: Session, company_id: uuid.UUID, missing: dict) -> list[str]:
    """Names the user chose to CREATE that already exist in the cached Tally
    masters. Any hit blocks the push before anything is sent to Tally."""
    cached = db.execute(
        select(MastersCache.kind, MastersCache.name)
        .where(MastersCache.company_id == company_id)
    ).all()
    by_kind: dict[str, set[str]] = {}
    for kind, name in cached:
        by_kind.setdefault(kind, set()).add((name or "").strip().upper())

    def exists(kind: str, name: str) -> bool:
        return (name or "").strip().upper() in by_kind.get(kind, set())

    problems: list[str] = []
    for led in missing["ledgers"]:
        if exists("ledger", led["name"]):
            problems.append(
                f"Ledger '{led['name']}' already exists in Tally. You cannot "
                "update a ledger - choose the existing one from the dropdown instead."
            )
    for si in missing["stock_items"]:
        if exists("stock", si["name"]):
            problems.append(
                f"Stock item '{si['name']}' already exists in Tally. Choose the "
                "existing one from the dropdown instead."
            )
    for unit in missing["units"]:
        if exists("unit", unit["name"]):
            problems.append(
                f"Unit '{unit['name']}' already exists in Tally. Choose the "
                "existing one from the dropdown instead."
            )
    return problems


def _fail(db: Session, voucher: Voucher, actor_id: uuid.UUID,
          errors: list[str], history: list[dict], stage: str = "voucher") -> dict:
    voucher.status = "failed"
    voucher.error_log = {"stage": stage, "errors": errors, "history": history}
    db.commit()
    write_audit(db, actor_id, "voucher.push_failed", "voucher", voucher.id,
                {"stage": stage, "errors": errors})
    return {"ok": False, "stage": stage, "errors": errors}
