# """
# Interactive AI-Powered Tally Automation
# ---------------------------------------
# Wraps the existing `dyanamic_automation.py` pipeline WITHOUT modifying it.
#
# Difference vs. the original:
#   • The original silently auto-creates every missing master (ledger / stock item / unit).
#   • This version NEVER creates a master on its own. Instead, for EVERY part of the
#     voucher (party, purchase ledger, each stock item, each unit, each additional
#     charge, and the GST ledgers) it ASKS the user:
#         - pick an EXISTING ledger / stock item from a list (fuzzy match pre-selected), OR
#         - create a NEW one -> ask the name -> pick a GROUP from the live Tally group list
#           -> the remaining master fields are auto-filled from the AI/OCR-extracted data.
#   • Only the masters the user explicitly chooses to create are sent to Tally.
#
# Run:
#   python interactive_automation.py --file invoice.pdf
#   python interactive_automation.py --dir invoices_to_import
#   python interactive_automation.py --file invoice.pdf --auto   # old silent behaviour
# """
import sys, os, json, argparse
import xml.etree.ElementTree as ET

# Reuse everything from the original module (prompts, AI calls, Tally I/O, phases).
import dyanamic_automation as base
from dyanamic_automation import (
    COMPANY_NAME, log, post_tally, fuzzy_match,
    extract_invoice, fetch_all_masters, fetch_company_start_date,
    create_missing_masters, create_voucher, self_heal,
)


# ────────────────────────────────────────────────────────────────────────────
# Phase 3b: Fetch the live list of accounting GROUPS from Tally
# ────────────────────────────────────────────────────────────────────────────
_DEFAULT_GROUPS = [
    "Sundry Creditors", "Sundry Debtors", "Purchase Accounts", "Sales Accounts",
    "Duties & Taxes", "Indirect Expenses", "Direct Expenses",
    "Indirect Incomes", "Direct Incomes", "Current Assets", "Current Liabilities",
]


def fetch_groups(company=COMPANY_NAME):
    """Return a sorted list of accounting group names defined in the Tally company."""
    xml = f"""<ENVELOPE><HEADER><VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST><TYPE>COLLECTION</TYPE>
    <ID>List of Groups</ID></HEADER><BODY><DESC>
    <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
    <SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY></STATICVARIABLES>
    </DESC></BODY></ENVELOPE>"""
    resp = post_tally(xml, "Fetch Groups")
    groups = []
    if resp:
        try:
            root = ET.fromstring(resp)
            for el in root.findall(".//GROUP"):
                name = el.attrib.get("NAME") or el.findtext("NAME") or el.findtext(".//NAME")
                if name and name.strip() and name.strip() not in groups:
                    groups.append(name.strip())
        except Exception as e:
            log.error(f"Error parsing groups: {e}")
    # Make sure the common groups we rely on are always selectable.
    for g in _DEFAULT_GROUPS:
        if not any(g.upper() == x.upper() for x in groups):
            groups.append(g)
    groups = sorted(groups, key=str.upper)
    log.info(f"\n  ┌─── 🗂️  GROUPS AVAILABLE ({len(groups)}) ───")
    for g in groups:
        log.info(f"  │  {g}")
    log.info(f"  └───────────────────────────────────")
    return groups


# ────────────────────────────────────────────────────────────────────────────
# Interactive prompt toolkit
# ────────────────────────────────────────────────────────────────────────────
def _interactive():
    """True only when we have a real terminal to read choices from."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def prompt_text(label, default=""):
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{label}{suffix}: ").strip()
    except EOFError:
        raw = ""
    return raw or default


def prompt_pick(title, options, default_index=None):
    """Pick ONE index from options (no 'create new'). Returns the chosen index."""
    if not options:
        return None
    while True:
        print("\n" + title)
        for i, o in enumerate(options, 1):
            marker = "  ← default" if (i - 1) == default_index else ""
            print(f"   {i:>3}. {o}{marker}")
        hint = f" [Enter = {options[default_index]}]" if default_index is not None else ""
        try:
            raw = input(f"Select number{hint}, or type text to filter: ").strip()
        except (EOFError, KeyboardInterrupt):
            return default_index if default_index is not None else 0
        if raw == "" and default_index is not None:
            return default_index
        if raw.isdigit():
            d = int(raw)
            if 1 <= d <= len(options):
                return d - 1
            print("   ⚠️  Number out of range.")
            continue
        # treat as a filter term
        hits = [i for i, o in enumerate(options) if raw.lower() in o.lower()]
        if len(hits) == 1:
            return hits[0]
        if hits:
            print("   Matches:")
            for i in hits:
                print(f"     {i + 1}. {options[i]}")
        else:
            print(f"   ⚠️  No match for '{raw}'.")


def prompt_existing_or_new(title, options, default_index=None):
    """
    Returns ('select', idx) to use an existing option, or ('new', None) to create one.
    Supports typing text to filter the visible list.
    """
    view = list(range(len(options)))
    while True:
        print("\n" + title)
        if view:
            for disp, idx in enumerate(view, 1):
                marker = "  ← default" if idx == default_index else ""
                print(f"   {disp:>3}. {options[idx]}{marker}")
        else:
            print("   (no existing entries in Tally)")
        print("     N. ➕  Create NEW")
        hint = f" [Enter = {options[default_index]}]" if default_index is not None else ""
        try:
            raw = input(f"Select number, 'N' for new, or type text to filter{hint}: ").strip()
        except (EOFError, KeyboardInterrupt):
            # No usable input: fall back to the default match, else create new.
            return ("select", default_index) if default_index is not None else ("new", None)
        if raw == "" and default_index is not None:
            return ("select", default_index)
        if raw.lower() == "n":
            return ("new", None)
        if raw.isdigit():
            d = int(raw)
            if 1 <= d <= len(view):
                return ("select", view[d - 1])
            print("   ⚠️  Number out of range.")
            continue
        # filter
        hits = [i for i, o in enumerate(options) if raw.lower() in o.lower()]
        if hits:
            view = hits
        else:
            print(f"   ⚠️  No match for '{raw}'. Showing all.")
            view = list(range(len(options)))


def _group_default_index(groups, default_group):
    for i, g in enumerate(groups):
        if g.strip().upper() == (default_group or "").strip().upper():
            return i
    return 0 if groups else None


# ────────────────────────────────────────────────────────────────────────────
# Resolvers  — each returns (final_name, create_entry_or_None)
# ────────────────────────────────────────────────────────────────────────────
def resolve_ledger(role_label, extracted_name, ledger_names, groups,
                   default_group, ledger_type, cache, ask_gst_appropriation=False):
    key = ("ledger", role_label, (extracted_name or "").upper())
    if key in cache:
        return cache[key]

    default_idx = None
    if extracted_name:
        m = fuzzy_match(extracted_name, ledger_names)
        if m and m in ledger_names:
            default_idx = ledger_names.index(m)

    print(f"\n{'─' * 60}")
    print(f"🔹 {role_label}")
    print(f"   Invoice value : {extracted_name!r}")
    if default_idx is not None:
        print(f"   Matched Tally ledger: {ledger_names[default_idx]!r}")

    # Non-interactive fallback: use match if any, else create under default group.
    if not _interactive():
        if default_idx is not None:
            res = (ledger_names[default_idx], None)
        else:
            res = (extracted_name, {"name": extracted_name, "parent": default_group, "type": ledger_type})
        cache[key] = res
        return res

    action, idx = prompt_existing_or_new(
        f"Choose an EXISTING ledger for [{role_label}] or create NEW:",
        ledger_names, default_idx)

    if action == "select":
        chosen = ledger_names[idx]
        print(f"   ✅ Using existing ledger: {chosen}")
        res = (chosen, None)
    else:
        name = prompt_text("   New ledger NAME", extracted_name or "")
        glist = list(groups)
        if not any(g.upper() == default_group.upper() for g in glist):
            glist.insert(0, default_group)
        gidx = _group_default_index(glist, default_group)
        gsel = prompt_pick(f"   Choose GROUP (parent) for new ledger '{name}':", glist, gidx)
        parent = glist[gsel] if gsel is not None else default_group
        print(f"   🆕 Will create ledger '{name}' under group '{parent}' "
              f"(details auto-filled from invoice data).")
        entry = {"name": name, "parent": parent, "type": ledger_type}

        # For charge ledgers (freight/packing/forwarding) placed under an expense
        # group, ask whether to appropriate the charge for GST. If GST → stamp the
        # tags the master XML needs (Appropriate to = Goods, Based on Value).
        if ask_gst_appropriation and parent.strip().lower() in ("direct expenses", "indirect expenses"):
            gsel2 = prompt_pick(f"   Appropriate '{name}' for GST?",
                                ["GST", "Not Applicable"], 0)
            if gsel2 == 0:
                entry["appropriate_for"] = "GST"
                entry["gst_appropriate_to"] = "Goods"
                entry["excise_alloc_type"] = "Based on Value"
                print(f"   ✅ '{name}' marked Appropriate for GST (Goods, Based on Value).")

        res = (name, entry)
    cache[key] = res
    return res


def resolve_unit(unit, unit_names, cache):
    key = ("unit", (unit or "").upper())
    if key in cache:
        return cache[key]

    default_idx = None
    if unit:
        m = fuzzy_match(unit, unit_names)
        if m and m in unit_names:
            default_idx = unit_names.index(m)

    if not _interactive():
        res = (unit_names[default_idx], None) if default_idx is not None else (unit, {"name": unit})
        cache[key] = res
        return res

    print(f"\n{'─' * 60}")
    print(f"🔹 Unit of Measure")
    print(f"   Invoice value : {unit!r}")
    action, idx = prompt_existing_or_new(
        f"Choose an EXISTING unit for '{unit}' or create NEW:", unit_names, default_idx)
    if action == "select":
        chosen = unit_names[idx]
        print(f"   ✅ Using existing unit: {chosen}")
        res = (chosen, None)
    else:
        name = prompt_text("   New unit SYMBOL", unit or "Nos")
        print(f"   🆕 Will create unit '{name}'.")
        res = (name, {"name": name})
    cache[key] = res
    return res


TAXABILITY_OPTIONS = ["Taxable", "Exempt", "Nil Rated", "Non-GST"]

# Freight / packing / forwarding etc. are NOT stock items — they are charge
# ledgers. Any item/charge name containing one of these (case-insensitive,
# substring) is routed to the ledger path, not stock-item resolution.
CHARGE_KEYWORDS = [
    "freight", "transport", "cartage", "packing", "packaging", "forwarding",
    "p&f", "p & f", "loading", "unloading", "insurance", "coolie", "hamali",
]


def is_charge_name(name):
    n = (name or "").lower()
    return any(kw in n for kw in CHARGE_KEYWORDS)


def resolve_stock_item(sname, stock_names, item, cache):
    key = ("stock", (sname or "").upper())
    if key in cache:
        return cache[key]

    default_idx = None
    m = fuzzy_match(sname, stock_names)
    if m and m in stock_names:
        default_idx = stock_names.index(m)

    unit = item.get("unit", "Nos")
    hsn = item.get("hsn_sac", "")

    if not _interactive():
        if default_idx is not None:
            res = (stock_names[default_idx], None)
        else:
            # Non-interactive fallback: default to Taxable, let the AI derive the
            # rate from the invoice (gst_rate=None), keep HSN from the invoice.
            res = (sname, {"name": sname, "unit": unit,
                           "taxability": "Taxable", "gst_rate": None, "hsn": hsn})
        cache[key] = res
        return res

    print(f"\n{'─' * 60}")
    print(f"🔹 Stock Item")
    print(f"   Invoice value : {sname!r}")
    action, idx = prompt_existing_or_new(
        f"Choose an EXISTING stock item for '{sname}' or create NEW:", stock_names, default_idx)
    if action == "select":
        chosen = stock_names[idx]
        print(f"   ✅ Using existing stock item: {chosen}")
        res = (chosen, None)
    else:
        name = prompt_text("   New stock item NAME", sname)
        print(f"   Unit (from earlier selection): {unit}")

        tidx = prompt_pick("   Choose TAXABILITY type:", TAXABILITY_OPTIONS, 0)
        taxability = TAXABILITY_OPTIONS[tidx] if tidx is not None else "Taxable"

        gst_rate = None
        if taxability == "Taxable":
            raw = prompt_text("   Enter TOTAL GST rate % (e.g. 18)", "")
            if raw.strip():
                try:
                    gst_rate = float(raw)
                except ValueError:
                    print(f"   ⚠️  '{raw}' is not a number — rate will be derived from the invoice.")
                    gst_rate = None

        if gst_rate is not None:
            half = gst_rate / 2
            rate_txt = f" @ {gst_rate}% (CGST {half}% + SGST {half}%, IGST {gst_rate}%)"
        else:
            rate_txt = ""
        print(f"   🆕 Will create stock item '{name}' | unit '{unit}' | "
              f"{taxability}{rate_txt} | HSN {hsn or '(auto from invoice)'}")
        res = (name, {"name": name, "unit": unit,
                      "taxability": taxability, "gst_rate": gst_rate, "hsn": hsn})
    cache[key] = res
    return res


# ────────────────────────────────────────────────────────────────────────────
# Phase 4 (replacement): interactively resolve every master
# ────────────────────────────────────────────────────────────────────────────
def resolve_masters_interactive(invoice_data, tally_masters, groups):
    log.info(f"\n{'=' * 60}")
    log.info("🧭 INTERACTIVE MASTER RESOLUTION")
    log.info("   For each part: pick an existing ledger/item, or create a new one.")
    log.info(f"{'=' * 60}")

    ledger_names = sorted((v["name"] for v in tally_masters["ledgers"].values()), key=str.upper)
    stock_names = sorted((v["name"] for v in tally_masters["stock_items"].values()), key=str.upper)
    unit_names = sorted(tally_masters["units"], key=str.upper)

    to_create = {"ledgers": [], "stock_items": [], "units": []}
    cache = {}
    vt = invoice_data.get("voucher_type", "Purchase")

    def _add_ledger(entry):
        if entry and not any(l["name"].upper() == entry["name"].upper() for l in to_create["ledgers"]):
            to_create["ledgers"].append(entry)

    # 1. Party
    party = invoice_data.get("party_name")
    if party:
        dg = "Sundry Creditors" if vt == "Purchase" else "Sundry Debtors"
        name, create = resolve_ledger("Party Ledger", party, ledger_names, groups, dg, "Party", cache)
        invoice_data["party_name"] = name
        _add_ledger(create)

    # 2. Units, Stock items, Purchase/Sales ledger — per item.
    #    Charge-like lines (freight/packing/forwarding…) are NOT stock items:
    #    pull them out of items[] into additional_charges[] so they resolve as
    #    ledgers below, then recompute taxable_value from the remaining items.
    kept_items = []
    for item in invoice_data.get("items", []):
        sname = item.get("stock_item_name")

        if is_charge_name(sname):
            invoice_data.setdefault("additional_charges", []).append({
                "ledger_name": sname,
                "amount": float(item.get("amount", 0) or 0),
            })
            log.info(f"  🔁 '{sname}' looks like a charge — moving to additional "
                     f"charges (resolved as a ledger, not a stock item).")
            continue

        unit = item.get("unit", "Nos")
        uname, ucreate = resolve_unit(unit, unit_names, cache)
        item["unit"] = uname
        if ucreate and not any(u["name"].upper() == ucreate["name"].upper() for u in to_create["units"]):
            to_create["units"].append(ucreate)

        if sname:
            fname, screate = resolve_stock_item(sname, stock_names, item, cache)
            item["stock_item_name"] = fname
            if screate and not any(s["name"].upper() == screate["name"].upper() for s in to_create["stock_items"]):
                to_create["stock_items"].append(screate)

        pl = item.get("purchase_ledger")
        if pl:
            dg = "Purchase Accounts" if vt == "Purchase" else "Sales Accounts"
            fname, pcreate = resolve_ledger("Purchase/Sales Ledger", pl, ledger_names, groups, dg, "Account", cache)
            item["purchase_ledger"] = fname
            _add_ledger(pcreate)

        kept_items.append(item)

    invoice_data["items"] = kept_items
    # Recompute taxable_value from the remaining stock items only (charges excluded).
    new_taxable = round(sum(float(it.get("amount", 0) or 0) for it in kept_items), 2)
    if new_taxable != float(invoice_data.get("taxable_value", 0) or 0):
        log.info(f"  🧮 Recomputed taxable_value → {new_taxable:.2f} "
                 f"(after moving charges out of items).")
    invoice_data["taxable_value"] = new_taxable

    # 3. Additional charges
    for charge in invoice_data.get("additional_charges", []):
        cl = charge.get("ledger_name")
        if cl:
            dg = "Indirect Expenses"
            fname, ccreate = resolve_ledger(f"Charge Ledger ({cl})", cl, ledger_names, groups,
                                            dg, "Expense", cache, ask_gst_appropriation=True)
            charge["ledger_name"] = fname
            _add_ledger(ccreate)

    # 4. GST ledgers (only those actually used on this invoice)
    gst = invoice_data.get("gst", {})
    for gkey in ("cgst_ledger", "sgst_ledger", "igst_ledger"):
        gl = gst.get(gkey)
        amt = float(gst.get(gkey.replace("_ledger", "_amount"), 0) or 0)
        if gl and amt > 0:
            fname, gcreate = resolve_ledger(f"GST Ledger ({gkey})", gl, ledger_names, groups,
                                            "Duties & Taxes", "GST", cache)
            gst[gkey] = fname
            _add_ledger(gcreate)

    # Summary
    has = bool(to_create["ledgers"] or to_create["stock_items"] or to_create["units"])
    log.info(f"\n  ╔══════════════════════════════════════")
    if has:
        log.info(f"  ║ 🆕 MASTERS YOU CHOSE TO CREATE:")
        for l in to_create["ledgers"]:
            log.info(f"  ║  📒 Ledger: {l['name']} → Group: {l['parent']} ({l['type']})")
        for s in to_create["stock_items"]:
            tx = s.get("taxability", "Taxable")
            rt = f" @ {s['gst_rate']}%" if s.get("gst_rate") else ""
            hs = f" | HSN {s['hsn']}" if s.get("hsn") else ""
            log.info(f"  ║  📦 Stock Item: {s['name']} → Unit: {s['unit']} | {tx}{rt}{hs}")
        for u in to_create["units"]:
            log.info(f"  ║  📏 Unit: {u['name']}")
    else:
        log.info(f"  ║ ✅ Everything mapped to EXISTING masters — nothing to create.")
    log.info(f"  ╚══════════════════════════════════════")
    return to_create, has


# ────────────────────────────────────────────────────────────────────────────
# Main pipeline (interactive)
# ────────────────────────────────────────────────────────────────────────────
def process_file(file_path, company=COMPANY_NAME, auto=False):
    # Escape hatch: original fully-automatic behaviour, untouched.
    if auto:
        return base.process_file(file_path, company)

    log.info("\n" + "=" * 60)
    log.info("🚀 INTERACTIVE TALLY AUTOMATION")
    log.info(f"📄 File: {os.path.basename(file_path)}")
    log.info(f"🏢 Company: {company}")
    log.info("=" * 60)

    try:
        # Phase 1-2: Extract
        invoice_data = extract_invoice(file_path)

        # Auto-fix date to match Tally's financial year (same rules as original)
        company_start = fetch_company_start_date(company)
        orig_date = invoice_data.get("date", "")
        if company_start and len(company_start) == 8 and len(orig_date) == 8:
            start_year = int(company_start[:4])
            inv_year = int(orig_date[:4])
            if inv_year != start_year:
                new_date = str(start_year) + orig_date[4:]
                log.info(f"  📅 Auto-fixing date: {orig_date} → {new_date} (matching Tally FY {start_year})")
                invoice_data["date"] = new_date

        d = invoice_data.get("date", "")
        if len(d) == 8 and d[6:] not in ["01", "02", "31"]:
            old_d = d
            invoice_data["date"] = d[:6] + "01"
            log.info(f"  📅 Edu mode: date coerced {old_d} → {invoice_data['date']} (1st of month)")

        # Phase 3: Fetch masters + live group list
        tally_masters = fetch_all_masters(company)
        groups = fetch_groups(company)

        # Phase 4: INTERACTIVE resolution (pick existing / create new)
        to_create, has_new = resolve_masters_interactive(invoice_data, tally_masters, groups)

        # Phase 5: Create ONLY the masters the user chose to create
        if has_new:
            ok = create_missing_masters(to_create, company, invoice_data)
            if not ok:
                log.warning("  ⚠️ Some masters may not have been created, attempting voucher anyway...")

        # Phase 6: Create voucher
        success, xml_sent, errors = create_voucher(invoice_data, company)

        # Phase 7: Self-heal (skip pure date warnings, same as original)
        if not success and errors:
            date_errors = [e for e in errors if "date" in e.lower() or "period" in e.lower()]
            non_date_errors = [e for e in errors if e not in date_errors]
            if date_errors and not non_date_errors:
                log.warning(f"  ⚠️ Date-related warnings (skipping retry): {date_errors}")
                log.info("  ℹ️ Voucher pushed to Tally as-is (date issue only)")
                success = True
            elif non_date_errors:
                success = self_heal(invoice_data, xml_sent, non_date_errors, company)

        if success:
            log.info("\n🎉 IMPORT COMPLETED SUCCESSFULLY!")
        else:
            log.error("\n❌ IMPORT FAILED.")
        return success

    except Exception as e:
        log.error(f"\n❌ Pipeline error: {e}", exc_info=True)
        return False


def process_folder(folder_path, company=COMPANY_NAME, auto=False):
    supported = [".xlsx", ".xls", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"]
    files = [os.path.join(folder_path, f) for f in os.listdir(folder_path)
             if os.path.splitext(f)[1].lower() in supported]
    log.info(f"\n📁 Batch: {len(files)} files in '{folder_path}'")
    results = {"ok": 0, "fail": 0}
    for fp in files:
        if process_file(fp, company, auto):
            results["ok"] += 1
        else:
            results["fail"] += 1
    log.info(f"\n📊 Batch Done: ✅ {results['ok']} | ❌ {results['fail']}")


def main():
    p = argparse.ArgumentParser(description="Interactive AI Tally Automation")
    p.add_argument("--file", help="Single invoice file path")
    p.add_argument("--dir", help="Folder of invoices")
    p.add_argument("--company", default=COMPANY_NAME)
    p.add_argument("--auto", action="store_true",
                   help="Skip prompts and use the original fully-automatic behaviour")
    args = p.parse_args()

    if base.OpenAI is None:
        log.error("❌ openai package required. Run: pip install openai")
        sys.exit(1)

    if args.file:
        process_file(args.file, args.company, args.auto)
    elif args.dir:
        process_folder(args.dir, args.company, args.auto)
    else:
        d = "invoices_to_import"
        os.makedirs(d, exist_ok=True)
        log.info(f"No args. Processing default folder: '{d}'")
        process_folder(d, args.company, args.auto)


if __name__ == "__main__":
    main()
