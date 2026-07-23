"""Fuzzy matching and review-screen suggestions.

Ported from find_missing_masters / the interactive resolvers, but instead of
prompting in a terminal it returns a suggestion structure the review screen
renders. The user makes every existing-vs-new decision in the browser.
"""
import difflib

FUZZY_THRESHOLD = 0.85

TAXABILITY_OPTIONS = ["Taxable", "Exempt", "Nil Rated", "Non-GST"]

# Freight / packing / forwarding etc. are NOT stock items - they are charge
# ledgers. Any item name containing one of these (case-insensitive substring)
# is offered as a charge ledger instead (same as is_charge_name in the script).
CHARGE_KEYWORDS = [
    "freight", "transport", "cartage", "packing", "packaging", "forwarding",
    "p&f", "p & f", "loading", "unloading", "insurance", "coolie", "hamali",
    "outward", "handling", "labour",
]

# CGST/SGST/IGST/UTGST are taxes, NOT charges and NOT stock items. They have a
# dedicated GST section on the review screen built from invoice["gst"]. If the
# extraction wrongly repeats them in items[] or additional_charges[], they must
# be excluded from stock items and charges so GST is never shown or pushed twice.
GST_KEYWORDS = ["cgst", "sgst", "igst", "utgst"]

# Groups that must always be selectable even if Tally's list is missing them.
DEFAULT_GROUPS = [
    "Sundry Creditors", "Sundry Debtors", "Purchase Accounts", "Sales Accounts",
    "Duties & Taxes", "Indirect Expenses", "Direct Expenses",
    "Indirect Incomes", "Direct Incomes", "Current Assets", "Current Liabilities",
]


def fuzzy_match(name: str | None, existing_list: list[str], threshold: float = FUZZY_THRESHOLD) -> str | None:
    if not name:
        return None
    for ex in existing_list:
        if ex.strip().upper() == name.strip().upper():
            return ex
    matches = difflib.get_close_matches(name, existing_list, n=1, cutoff=threshold)
    return matches[0] if matches else None


def is_charge_name(name: str | None) -> bool:
    n = (name or "").lower()
    return any(kw in n for kw in CHARGE_KEYWORDS)


def is_gst_name(name: str | None) -> bool:
    n = (name or "").lower()
    return any(kw in n for kw in GST_KEYWORDS)


def route_charges(invoice: dict) -> bool:
    """Move charge-like lines (freight/packing/forwarding/outward...) out of
    items[] into additional_charges[], mirroring interactive_automation.py.

    Charges are ledger entries, never stock items. This runs on the voucher's
    working copy (never the raw extracted document). Idempotent: calling it
    again does nothing once the lines are already moved. Returns True if it
    changed the invoice, so callers can decide whether to persist.
    """
    items = invoice.get("items", []) or []
    kept: list[dict] = []
    moved: list[dict] = []
    dropped_gst = False
    for item in items:
        sname = item.get("stock_item_name")
        if is_gst_name(sname):
            # GST is represented only in invoice["gst"], never as a line item.
            dropped_gst = True
            continue
        if is_charge_name(sname):
            moved.append({
                "ledger_name": sname,
                "amount": float(item.get("amount", 0) or 0),
            })
        else:
            kept.append(item)

    existing = invoice.get("additional_charges", []) or []
    # Strip any GST lines the extraction wrongly placed in charges - they belong
    # only in the GST section, so they never become charge ledgers.
    gst_in_charges = [c for c in existing if is_gst_name(c.get("ledger_name"))]
    existing = [c for c in existing if not is_gst_name(c.get("ledger_name"))]

    if not moved and not dropped_gst and not gst_in_charges:
        return False

    invoice["items"] = kept
    # Avoid re-adding a charge that is already present (idempotency guard).
    existing_names = {(c.get("ledger_name") or "").strip().upper() for c in existing}
    for charge in moved:
        if (charge["ledger_name"] or "").strip().upper() not in existing_names:
            existing.append(charge)
    invoice["additional_charges"] = existing

    # Recompute taxable_value from the remaining real stock items only.
    invoice["taxable_value"] = round(
        sum(float(i.get("amount", 0) or 0) for i in kept), 2)
    return True


def merge_groups(tally_groups: list[str]) -> list[str]:
    groups = list(tally_groups)
    for g in DEFAULT_GROUPS:
        if not any(g.upper() == x.upper() for x in groups):
            groups.append(g)
    return sorted(groups, key=str.upper)


def _suggest(extracted: str | None, existing: list[str], default_group: str) -> dict:
    """One suggestion entry: pre-select the closest existing master, else new."""
    match = fuzzy_match(extracted, existing)
    if match:
        return {"mode": "existing", "name": match, "extracted": extracted, "group": default_group}
    return {"mode": "new", "name": extracted, "extracted": extracted, "group": default_group}


def build_suggestions(invoice: dict, masters: dict) -> dict:
    """Build the pre-selected resolution the review screen starts from.

    masters: {"ledgers": [names], "stock": [names], "units": [names], "groups": [names]}
    Returns the same shape as edited_json.resolution (docs/03-DATA-MODEL.md).
    """
    ledgers = masters.get("ledgers", [])
    stock = masters.get("stock", [])
    units = masters.get("units", [])
    groups = merge_groups(masters.get("groups", []))

    vt = invoice.get("voucher_type", "Purchase")
    party_group = "Sundry Creditors" if vt == "Purchase" else "Sundry Debtors"
    account_group = "Purchase Accounts" if vt == "Purchase" else "Sales Accounts"

    resolution: dict = {
        "party": _suggest(invoice.get("party_name"), ledgers, party_group),
        "purchase": None,
        "units": [],
        "stock_items": [],
        "charges": [],
        "gst_ledgers": [],
    }

    seen_units: set[str] = set()
    stock_index = 0

    for item in invoice.get("items", []):
        sname = item.get("stock_item_name")

        # GST components are never stock items or charges - they live only in the
        # GST section (built from invoice["gst"] below).
        if is_gst_name(sname):
            continue

        # Charge-like item names are offered as charge ledgers, not stock items.
        if is_charge_name(sname):
            entry = _suggest(sname, ledgers, "Indirect Expenses")
            entry["from_item"] = True
            entry["amount"] = item.get("amount")
            resolution["charges"].append(entry)
            continue

        unit = item.get("unit", "Nos")
        if unit and unit.upper() not in seen_units:
            seen_units.add(unit.upper())
            resolution["units"].append(_suggest(unit, units, ""))

        # ONE stock entry per line, keyed by line position (index) and never
        # deduplicated by name. Two lines with the same extracted name (e.g. the
        # AI repeating "PVC Fills") stay independent, so correcting one line's
        # stock item never rewrites another's. Push maps back to items by index.
        entry = _suggest(sname, stock, "")
        entry.update({
            "index": stock_index,
            "taxability": "Taxable",
            "gst_rate": None,
            "hsn": item.get("hsn_sac", ""),
            "unit": unit,
        })
        resolution["stock_items"].append(entry)
        stock_index += 1

        pl = item.get("purchase_ledger")
        if pl and resolution["purchase"] is None:
            resolution["purchase"] = _suggest(pl, ledgers, account_group)

    for charge in invoice.get("additional_charges", []):
        cl = charge.get("ledger_name")
        # Skip GST that the extraction wrongly placed in charges - it is shown in
        # the GST section instead, so it is never offered as a charge ledger.
        if cl and not is_gst_name(cl):
            entry = _suggest(cl, ledgers, "Indirect Expenses")
            entry["amount"] = charge.get("amount")
            resolution["charges"].append(entry)

    gst = invoice.get("gst", {}) or {}
    for gkey in ("cgst_ledger", "sgst_ledger", "igst_ledger"):
        gl = gst.get(gkey)
        try:
            amt = float(gst.get(gkey.replace("_ledger", "_amount"), 0) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if gl and amt > 0:
            entry = _suggest(gl, ledgers, "Duties & Taxes")
            entry["role"] = gkey
            resolution["gst_ledgers"].append(entry)

    return {
        "resolution": resolution,
        "groups": groups,
        "taxability_options": TAXABILITY_OPTIONS,
    }


def build_missing_masters(resolution: dict) -> dict:
    """Turn the user's create-new choices into the missing-masters JSON the
    MASTER_XML_PROMPT expects. Only mode == "new" entries are included."""
    missing = {"ledgers": [], "stock_items": [], "units": []}

    def add_ledger(entry: dict | None, ledger_type: str) -> None:
        if not entry or entry.get("mode") != "new" or not entry.get("name"):
            return
        if any(l["name"].upper() == entry["name"].upper() for l in missing["ledgers"]):
            return
        led = {"name": entry["name"], "parent": entry.get("group") or "", "type": ledger_type}
        if entry.get("appropriate_for_gst"):
            led["appropriate_for"] = "GST"
            led["gst_appropriate_to"] = "Goods"
            led["excise_alloc_type"] = "Based on Value"
        missing["ledgers"].append(led)

    add_ledger(resolution.get("party"), "Party")
    add_ledger(resolution.get("purchase"), "Account")
    for charge in resolution.get("charges", []):
        add_ledger(charge, "Expense")
    for gl in resolution.get("gst_ledgers", []):
        add_ledger(gl, "GST")

    for unit in resolution.get("units", []):
        if unit.get("mode") == "new" and unit.get("name"):
            if not any(u["name"].upper() == unit["name"].upper() for u in missing["units"]):
                missing["units"].append({"name": unit["name"]})

    for si in resolution.get("stock_items", []):
        if si.get("mode") == "new" and si.get("name"):
            if not any(s["name"].upper() == si["name"].upper() for s in missing["stock_items"]):
                missing["stock_items"].append({
                    "name": si["name"],
                    "unit": si.get("unit", "Nos"),
                    "taxability": si.get("taxability", "Taxable"),
                    "gst_rate": si.get("gst_rate"),
                    "hsn": si.get("hsn", ""),
                })

    has_missing = bool(missing["ledgers"] or missing["stock_items"] or missing["units"])
    return missing if has_missing else {"ledgers": [], "stock_items": [], "units": []}
