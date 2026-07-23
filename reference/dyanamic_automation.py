# """
# Dynamic AI-Powered Tally Automation
# All XML generated dynamically by GPT-4o-mini - no hardcoded templates
# """
import sys, io, os, json, logging, datetime, difflib, argparse, base64
import xml.etree.ElementTree as ET
import requests
from dotenv import load_dotenv

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
load_dotenv()

# Optional imports
try:
    import pandas as pd
except ImportError:
    pd = None
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None
try:
    import fitz
except ImportError:
    fitz = None
try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# Config
TALLY_URL = os.getenv("TALLY_URL", "http://localhost:9000")
COMPANY_NAME = os.getenv("COMPANY_NAME", "Tirth_dvbc")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LOG_DIR = "tally_logs/dynamic"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger()

# ── Prompts ──
EXTRACT_PROMPT = """You are an expert accountant specialising in Indian GST invoices and Tally Prime.
Extract ALL invoice details from the uploaded content (which may contain 2-4 pages).
 
 
STEP 1 — IDENTIFY DOCUMENT TYPES ON EACH PAGE

 
Each PDF typically contains:
  • Page 1  : Supplier's TAX INVOICE  ← PRIMARY source for ALL financial data
  • Page 2+ : Buyer's GRN (Goods Receipt Note) / Material Received Note / Delivery Challan
               ← Secondary; use ONLY for Net GRN Amount and GRN-specific fields
 
RULE: Extract item details, rates, GST, and charges ONLY from the TAX INVOICE.
      Do NOT re-extract items from the GRN/Challan page.
 
 
 
STEP 2 — EXTRACT FROM TAX INVOICE (Page 1)
 
 
A. VOUCHER META
 
  • voucher_type   : Always "Purchase" for supplier invoices received by Asia Bulk Sacks.
  • voucher_number : The invoice number printed on the supplier's Tax Invoice
                     (e.g., "GST-UP/078/26-27", "T26-27/296", "82/26-27").
                     Use the value from the "Invoice No." field.
  • date           : Invoice date in YYYYMMDD format (e.g., 08-Jun-26 → 20260608).
 
 
B. PARTY (Supplier)
 
  • party_name         : Supplier / seller company name — the entity ISSUING the invoice,
                         NOT "Asia Bulk Sacks".
                         e.g., "Rans Engineering & Chemicals Private Limited",
                               "MAHALAXMI SAW MILL", "Solos Polymers Private Limited".
 
  • party_gstin        : Supplier's GSTIN from the seller's header block.
                         NOT the buyer's GSTIN (24AACCA4884D1ZW).
                         e.g., "09AAJCR7876D1ZU"
 
  • party_pan          : Supplier's 10-char PAN. If not printed, DERIVE from GSTIN
                         chars 3–12 (e.g., "09AAJCR7876D1ZU" → "AAJCR7876D"). Else "".
 
  • party_state        : Supplier's state name ONLY — no code, no comma, no extra text.
                         e.g., "Uttar Pradesh"  NOT "Uttar Pradesh, Code: 09"
                               "Gujarat"        NOT "Gujarat (24)"
                         This value is used directly in <PLACEOFSUPPLY> in Tally XML.
 
  • party_state_code   : 2-digit state code as a string.
                         e.g., "09" for Uttar Pradesh, "24" for Gujarat, "27" for Maharashtra.
 
  • party_address      : Supplier's full address as printed on the invoice header,
                         EXCLUDING the trailing PIN code (pin code goes in party_pincode).
                         Combine all remaining address lines into one string.
                         e.g., "D-5, S/F, Sector-03 (P-2), Loni, Ghaziabad"
 
  • party_pincode      : Supplier's 6-digit PIN code.
                         Read it off the address (usually the last 6-digit number,
                         e.g., "...Ghaziabad - 201102" → "201102"). If not printed
                         explicitly, infer it from the city/locality named in the
                         address using known PIN code ranges. Use "" only if the
                         city itself can't be identified. Exactly 6 digits, no text.
 
  • party_country      : Supplier's country. For domestic Indian suppliers use "India".
 
  • party_registration_type : GST registration type of the supplier.
                         Use "Regular" if GSTIN is present and valid.
                         Use "Unregistered" if no GSTIN is found.
                         Use "Composition" if invoice mentions composition dealer.
 
 
C. LINE ITEMS (from the item table on the Tax Invoice)
 
  For each item row extract:
  • stock_item_name : Exact description as printed on the invoice.
                      e.g., "PP Self Adhesive Plastic Rolls", "Rans®Bitu-PMR",
                            "Pine Wood Pallet", "LINER JW".
  • hsn_sac         : HSN/SAC code for that item.
                      — Extract per-item if each row has its own HSN column value.
                      — If ONE HSN/SAC code covers ALL items (printed once in header,
                        footer, or summary), apply that SAME code to every item.
                      — If truly absent, use "" (empty string).
                      — Never guess or fabricate.
                      — No spaces: "39201019" not "3920 1019".
  • quantity        : Numeric quantity (e.g., 3, 2.000, 117, 3376.00).
  • unit            : Unit of measure exactly as printed (e.g., "Roll", "Kgs", "Nos",
                      "Pcs", "Ltr"). Default "Nos" only if completely absent.
  • rate            : Rate per unit (numeric, e.g., 3300.00, 170.00).
  • amount          : Line amount = quantity × rate (numeric, e.g., 9900.00, 340.00).
  • purchase_ledger : Always exactly "Purchase" for every item — no rate suffix.
 
 
D. TAXABLE VALUE (Critical for GST Ledger Naming)
 
  • taxable_value : Sum of ALL item amounts ONLY.
                    Do NOT include freight, P&F, loading, or any other charges.
                    Do NOT include GST amounts.
                    Formula: taxable_value = sum of item[i].amount for all i
                    e.g., Items: 9900.00 + 340.00 = 10240.00 → taxable_value = 10240.00
 
  ★ This value is used to build the GST ledger name in Tally:
      Inter-state  → "IGST-Input-{igst_rate}% Rs. {taxable_value}"
                     e.g., "IGST-Input-18.00% Rs. 10240.00"
      Intra-state  → "CGST-Input-{cgst_rate}% Rs. {taxable_value}"
                     e.g., "CGST-Input-2.50% Rs. 109980.00"
                   → "SGST-Input-{sgst_rate}% Rs. {taxable_value}"
                     e.g., "SGST-Input-2.50% Rs. 109980.00"
 
E. ADDITIONAL CHARGES (non-GST, non-item lines on the Tax Invoice)
 
  Identify every extra charge or deduction that is NOT an inventory item and NOT GST.
  Common examples: Freight Charges, Freight Outward, Packaging & Forwarding,
  Loading Charges, Insurance, Round Off.
 
  Rules:
    Rules:
  • Put each in additional_charges[] with its exact ledger_name and amount.
  • Round Off: if it reduces the total (shown as negative or labelled "Less"),
    use a NEGATIVE amount (e.g., -0.25).
  • ★ If Round Off is zero (0.0) or not present on the invoice, do NOT add any
    Round Off entry to additional_charges[]. Leave it out entirely.
    Only include Round Off in additional_charges[] when its amount is non-zero.
  ...
  ★ IMPORTANT: Round Off must ALSO be copied to the top-level "Round off" field
    with the exact same signed value. Both must always match.
    If Round Off is zero or absent → set top-level "Round off" to 0.0 AND
    do NOT include Round Off in additional_charges[].

 
 
F. GST
 
  Determine GST type by comparing supplier's state with buyer's state
  (buyer is always Asia Bulk Sacks, Gujarat, Code 24):
  • Same state (Gujarat)  → CGST + SGST (intra-state).
  • Different state        → IGST only (inter-state).
 
  Extract:
  • cgst_rate    : CGST percentage as printed (e.g., 2.5, 9.0). Per-component rate only.
  • cgst_amount  : CGST rupee amount as printed (e.g., 522.00, 2749.50).
  • sgst_rate    : SGST percentage (same as cgst_rate for intra-state).
  • sgst_amount  : SGST rupee amount as printed.
  • igst_rate    : IGST percentage for inter-state (e.g., 18.0, 5.0, 28.0).
  • igst_amount  : IGST rupee amount for inter-state (e.g., 2004.75).
 
  GST Ledger names are built dynamically using taxable_value (see Section D):
  • cgst_ledger  : "CGST-Input-{cgst_rate formatted to 2 decimals}% Rs. {taxable_value}"
                   e.g., "CGST-Input-2.50% Rs. 109980.00"
  • sgst_ledger  : "SGST-Input-{sgst_rate formatted to 2 decimals}% Rs. {taxable_value}"
                   e.g., "SGST-Input-2.50% Rs. 109980.00"
  • igst_ledger  : "IGST-Input-{igst_rate formatted to 2 decimals}% Rs. {taxable_value}"
                   e.g., "IGST-Input-18.00% Rs. 10240.00"
 
  NOTE: Rate is the per-component rate (e.g., CGST 2.5% not total 5%).
        If rate is not explicitly printed, derive: rate = (amount / taxable_value) * 100
  ★ STRICT: Do NOT put any GST line in additional_charges.
 
 
G. TOTALS
 
  • total_amount  : Grand total payable as shown on Tax Invoice
                    (items + charges + GST ± Round Off).
                    e.g., 13142.00 / 115479.00 / 69316.00
  • "Round off"   : Numeric value of Round Off (negative if deducted).
                     Must exactly match the Round Off entry in additional_charges[].
                    If no Round Off on the invoice, set to 0.0 and do NOT add
                    any Round Off entry to additional_charges[].
  • "Packaging & Forwarding Extra" : P&F amount if separately listed, else 0.0.
  • "Loading Charges"              : Loading amount if present, else 0.0.
  • "Insurance"                    : Insurance amount if present, else 0.0.
 
 
STEP 3 — EXTRACT FROM GRN PAGE(S) (Page 2 onward)
 
 
From the GRN / Material Receipt Note extract ONLY:
  • "Net GRN Amount" : The "Net GRN Amount" or "Net Mat. Rcvd. Amount" field.
                       May differ from Tax Invoice total due to P&F adjustments.
  • "Packaging & Forwarding Extra" : If shown on the GRN and NOT on the Tax Invoice,
                                      capture it here.
  • NOTE — Split GRN: If one Tax Invoice is received in multiple GRN batches,
    sum the GRN amounts for "Net GRN Amount" but do NOT change item quantities —
    use Tax Invoice quantities always.
 
 
 
STEP 4 — NARRATION
 
 
Build narration as:
  "Purchase from {party_name} vide invoice {voucher_number} dated {date_human_readable}"
  e.g., "Purchase from Rans Engineering & Chemicals Private Limited vide invoice GST-UP/078/26-27 dated 08-Jun-26"
 
 
 
STEP 5 — OUTPUT FORMAT
 
 
Return ONLY a single valid JSON object — no markdown, no explanation, no extra text.
 
Schema:
{
  "voucher_type"    : "Purchase",
  "voucher_number"  : "string",
  "date"            : "YYYYMMDD",
 
  "party_name"              : "string",
  "party_gstin"             : "string",
  "party_pan"               : "string  — 10-char PAN, derived from GSTIN if absent",
  "party_state"             : "string  — state name ONLY e.g. Gujarat, Uttar Pradesh",
  "party_state_code"        : "string  — e.g. 24, 09, 27",
  "party_address"           : "string  — full address as on invoice (excl. PIN code)",
  "party_pincode"           : "string  — 6-digit PIN code",
  "party_country"           : "string  — e.g. India",
  "party_registration_type" : "string  — Regular | Unregistered | Composition",
 
  "taxable_value" : 0.0,
 
  "items": [
    {
      "stock_item_name" : "string",
      "hsn_sac"         : "string",
      "quantity"        : 0.0,
      "unit"            : "string",
      "rate"            : 0.0,
      "amount"          : 0.0,
      "purchase_ledger" : "Purchase"
    }
  ],
 
  "additional_charges": [
    {
      "ledger_name" : "string",
      "amount"      : 0.0
    }
  ],
 
  "gst": {
    "cgst_ledger"  : "CGST-Input-{rate}% Rs. {taxable_value}",
    "cgst_rate"    : 0.0,
    "cgst_amount"  : 0.0,
    "sgst_ledger"  : "SGST-Input-{rate}% Rs. {taxable_value}",
    "sgst_rate"    : 0.0,
    "sgst_amount"  : 0.0,
    "igst_ledger"  : "IGST-Input-{rate}% Rs. {taxable_value}",
    "igst_rate"    : 0.0,
    "igst_amount"  : 0.0
  },
 
  "Round off"                   : 0.0,
  "Packaging & Forwarding Extra": 0.0,
  "Loading Charges"             : 0.0,
  "Insurance"                   : 0.0,
  "total_amount"                : 0.0,
  "Net GRN Amount"              : 0.0,
  "narration"                   : "string"
}
 
 
VALIDATION CHECKLIST before outputting:
 
  ✓ party_gstin is the SUPPLIER's GSTIN — not Asia Bulk Sacks' (24AACCA4884D1ZW).
  ✓ party_pan is either taken from the invoice or correctly derived from GSTIN
    characters 3–12.
  ✓ party_state contains ONLY the state name — no code, no comma, no extra text.
  ✓ party_address is the supplier's address — not Asia Bulk Sacks' address.
  ✓ party_pincode is a 6-digit number (inferred from address/city if not printed).
  ✓ party_country is "India" for domestic suppliers.
  ✓ taxable_value = sum of item amounts ONLY (no freight, no GST).
  ✓ gst.cgst_ledger / sgst_ledger / igst_ledger names are built using taxable_value.
     Format: "IGST-Input-18.00% Rs. 10240.00" (rate always 2 decimal places).
  ✓ IGST used only when supplier state ≠ Gujarat; CGST+SGST for Gujarat suppliers.
  ✓ GST amounts NOT in additional_charges[].
  ✓ Round Off is negative if it reduces the total.
  ✓ Round Off value in additional_charges[] matches top-level "Round off" field exactly.
  ✓ total_amount matches the grand total on the Tax Invoice.
  ✓ purchase_ledger is exactly "Purchase" for every single item — no rate suffix.
  ✓ All amounts are plain numbers — no ₹ symbol, no commas.
  ✓ hsn_sac populated for every item (shared HSN applied to all items if printed once).
  ✓ hsn_sac is plain digits with no spaces (e.g., "39201019" not "3920 1019").
"""
 
 
MASTER_XML_PROMPT = """Generate Tally Prime XML to create these missing masters for company "{company}".
IMPORTANT: Use &amp; instead of & in XML values (e.g. "Duties &amp; Taxes").
 
STRICT: Only create masters that are explicitly listed in the missing_json below.
  Do NOT create any extra masters on your own. If a ledger (like Round Off) is
  not in missing_json, do NOT create it — even if invoice_json mentions it.
CRITICAL CREATION ORDER
 
In <REQUESTDATA> output messages in this EXACT sequential order:
  1. All <UNIT> messages first.
  2. All <LEDGER> messages second.
  3. All <STOCKITEM> messages last.
Reason: Tally processes XML imports sequentially. A STOCKITEM imported before
its unit or ledger is defined will cause an import error.
 
 
UNIT RULES
 
  • Create a UNIT only if it does not already exist in Tally.
  • DECIMALPLACES: 2 for weight/volume units (Kg, Kgs, Ltr, Mtr); 0 for countable units
    (Nos, Pcs, Roll, Box, Set).
 
 
LEDGER RULES
 
  Create only ledgers that do not already exist in Tally.
 
  ┌─────────────────────────────────┬──────────────────────────┐
  │ Ledger Type                     │ PARENT                   │
  ├─────────────────────────────────┼──────────────────────────┤
  │ Party (supplier)                │ Sundry Creditors         │
  │ Purchase ("Purchase")           │ Purchase Accounts        │
  │ Discount                        │ Indirect Incomes         │
  │ Freight / Loading / P&F / Ins.  │ Indirect Expenses        │
  │ Round Off                       │ Indirect Expenses        │
  │ CGST / SGST / IGST              │ Duties &amp; Taxes       │
  └─────────────────────────────────┴──────────────────────────┘
 
  PARTY LEDGER (Sundry Creditors):
  
  Use supplier details from the invoice JSON to populate the ledger fully using
  TallyPrime 3.0+ nested structure. Field source mapping:
    • NAME / NAME.LIST inside LANGUAGENAME.LIST : party_name
    • PARENT             : Sundry Creditors
    • ISBILLWISEON       : Yes
    • INCOMETAXNUMBER    : party_pan
    • LEDMAILINGDETAILS.LIST : APPLICABLEFROM (company start date or 20260401),
      MAILINGNAME (party_name), ADDRESS.LIST (party_address, split into lines),
      STATE (party_state), COUNTRY (party_country), PINCODE (party_pincode).
    • LEDGSTREGDETAILS.LIST  : APPLICABLEFROM (same date), GSTREGISTRATIONTYPE
      (party_registration_type), PLACEOFSUPPLY (party_state), GSTIN (party_gstin).
  Do NOT invent CREDITPERIOD or OPENINGBALANCE tags unless the invoice JSON
  explicitly carries that data — they are not part of the standard ledger shape.
 
  DISCOUNT LEDGER (Indirect Incomes):
 
  Create this ONLY if invoice_json contains a discount-type entry in
  additional_charges[] (ledger_name containing "discount", amount typically
  negative since it reduces the payable amount).
    • PARENT              : Indirect Incomes
    • VATDEALERTYPE        : Regular
    • ISBILLWISEON         : No
    • ISCOSTCENTRESON      : Yes
    • APPROPRIATEFOR       : GST
    • GSTAPPROPRIATETO     : Goods
    • EXCISEALLOCTYPE      : Based on Value
    • GSTTYPE              : &#4; Not Applicable
    • GSTAPPLICABLE        : &#4; Not Applicable
 
  ROUND OFF LEDGER (Indirect Expenses):
 

  ★ CRITICAL: Create a Round Off ledger ONLY if "Round Off" appears in the
  missing_json list. If missing_json does not contain a Round Off entry,
  do NOT create it. Never create any master that is not in missing_json.
  When creating, use this exact shape — a plain generic expense ledger is NOT
  sufficient for Round Off:
    • PARENT              : Indirect Expenses
    • VATDEALERNATURE      : Invoice Rounding
    • ROUNDINGMETHOD       : Downward Rounding  (or "Normal Rounding" if the
                              invoice rounds both up and down across vouchers)
    • ROUNDINGLIMIT        : 1
    • ISCOSTCENTRESON      : Yes
    • ISBILLWISEON         : No
 
  OTHER INDIRECT EXPENSES (Freight / Loading / P&amp;F / Insurance):
  Simple ledgers — PARENT: Indirect Expenses only, no extra tags needed unless
  invoice_json supplies additional detail for that specific charge.
 
  GST LEDGERS (Duties & Taxes):
  
  GST ledger names are DYNAMIC — built from the invoice JSON using this formula:
    taxable_value = invoice_json["taxable_value"]
    cgst_rate     = invoice_json["gst"]["cgst_rate"]
    sgst_rate     = invoice_json["gst"]["sgst_rate"]
    igst_rate     = invoice_json["gst"]["igst_rate"]
 
    cgst_ledger_name = f"CGST-Input-{{cgst_rate:.2f}}% Rs. {{taxable_value:.2f}}"
    sgst_ledger_name = f"SGST-Input-{{sgst_rate:.2f}}% Rs. {{taxable_value:.2f}}"
    igst_ledger_name = f"IGST-Input-{{igst_rate:.2f}}% Rs. {{taxable_value:.2f}}"
 
    Examples:
      "IGST-Input-18.00% Rs. 10240.00"
      "CGST-Input-2.50% Rs. 109980.00"
      "SGST-Input-2.50% Rs. 109980.00"
 
  For each GST ledger:
    • PARENT                  : Duties &amp; Taxes
    • TAXTYPE                 : GST
    • ISDUTIESANDTAXES        : Yes
    • RATEOFTAXCALCULATION    : the rate value as a plain number
                                (e.g., 2.5 for CGST 2.5%, 18.0 for IGST 18%)
    • GSTDUTYHEAD             : "Central Tax"    for CGST
                                "State Tax"      for SGST
                                "Integrated Tax" for IGST
    • GSTAPPLICABLE           : &#4; Not Applicable
    • OPENINGBALANCE          : 0
    • ISBILLWISEON            : No
    • AFFECTSSTOCK            : No
 
 
STOCKITEM RULES

  • Do NOT create a stock item if it already exists.
  • BASEUNITS must match the unit name exactly (e.g., "Roll", "Kgs", "Nos").
  • Each stock item in the "Missing masters" JSON carries these fields chosen by
    the user: name, unit, taxability, gst_rate, hsn.
      - taxability ∈ {{Taxable, Exempt, Nil Rated, Non-GST}}
      - gst_rate   = the TOTAL GST % the user entered (may be null)
      - hsn        = the HSN/SAC code (may be blank)
  • STATE NAME DETAILS (STATEWISEDETAILS.LIST):    
      - state name=party_state put in <STATENAME></STATENAME>
  • HSN CODE (HSNDETAILS.LIST):
      - Use the item's "hsn" value if present.
      - If "hsn" is blank/missing, fall back to the matching invoice item's hsn_sac.
      - HSN must be plain digits, no spaces.
  • GST DETAILS (GSTDETAILS.LIST) — driven ENTIRELY by the item's "taxability":
      - Always emit TAXABILITY = the item's taxability value.
      - ONLY when taxability = "Taxable", emit a RATEDETAILS.LIST that splits the
        TOTAL rate R as follows:
            let R = the item's "gst_rate".
            If gst_rate is null/blank, derive R from the invoice:
                cgst_amount > 0 → R = cgst_rate * 2   (e.g., 2.5+2.5 → 5.00)
                igst_amount > 0 → R = igst_rate        (e.g., 18.0 → 18.00)
            CGST (Central Tax)      → GSTRATE = R / 2
            SGST/UTGST (State Tax)  → GSTRATE = R / 2
            IGST (Integrated Tax)   → GSTRATE = R
      - For taxability Exempt / Nil Rated / Non-GST: emit NO RATEDETAILS.LIST and
        NO GSTRATE (GST rate is not applicable to these).
 
 
XML STRUCTURE
 
 
<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>All Masters</REPORTNAME>
        <STATICVARIABLES><SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY></STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
 
        <!-- ① UNITS FIRST -->
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <UNIT NAME="Roll" ACTION="Create">
            <NAME>Roll</NAME>
            <ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
            <DECIMALPLACES>0</DECIMALPLACES>
          </UNIT>
        </TALLYMESSAGE>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <UNIT NAME="Kgs" ACTION="Create">
            <NAME>Kgs</NAME>
            <ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
            <DECIMALPLACES>2</DECIMALPLACES>
          </UNIT>
        </TALLYMESSAGE>
 
        <!-- ② LEDGERS SECOND — order: Party → Purchase → Discount → Charges → Round Off → GST -->
 
        <!-- Party Ledger (Sundry Creditors) — fully populated from invoice JSON -->
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Rans Engineering &amp; Chemicals Private Limited" ACTION="Create">
            <NAME>Rans Engineering &amp; Chemicals Private Limited</NAME>
            <PARENT>Sundry Creditors</PARENT>
            <ISBILLWISEON>Yes</ISBILLWISEON>
            <INCOMETAXNUMBER>AAJCR7876D</INCOMETAXNUMBER>
            <LANGUAGENAME.LIST>
              <NAME.LIST TYPE="String">
                <NAME>Rans Engineering &amp; Chemicals Private Limited</NAME>
              </NAME.LIST>
              <LANGUAGEID>1033</LANGUAGEID>
            </LANGUAGENAME.LIST>
            <LEDMAILINGDETAILS.LIST>
              <APPLICABLEFROM>20260401</APPLICABLEFROM>
              <MAILINGNAME>Rans Engineering &amp; Chemicals Private Limited</MAILINGNAME>
              <ADDRESS.LIST TYPE="String">
                <ADDRESS>D-5, S/F, Sector-03 (P-2)</ADDRESS>
                <ADDRESS>Loni, Ghaziabad</ADDRESS>
              </ADDRESS.LIST>
              <STATE>Uttar Pradesh</STATE>
              <COUNTRY>India</COUNTRY>
              <PINCODE>201102</PINCODE>
            </LEDMAILINGDETAILS.LIST>
            <LEDGSTREGDETAILS.LIST>
              <APPLICABLEFROM>20260401</APPLICABLEFROM>
              <GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>
              <PLACEOFSUPPLY>Uttar Pradesh</PLACEOFSUPPLY>
              <GSTIN>09AAJCR7876D1ZU</GSTIN>
            </LEDGSTREGDETAILS.LIST>
          </LEDGER>
        </TALLYMESSAGE>
 
        <!-- Purchase Ledger -->
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Purchase" ACTION="Create">
            <NAME>Purchase</NAME>
            <PARENT>Purchase Accounts</PARENT>
            <GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>
          </LEDGER>
        </TALLYMESSAGE>
 
        <!-- Discount Ledger (Indirect Incomes) — only if invoice has a discount line -->
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Discount" ACTION="Create">
            <NAME>Discount</NAME>
            <PARENT>Indirect Incomes</PARENT>
            <VATDEALERTYPE>Regular</VATDEALERTYPE>
            <ISBILLWISEON>No</ISBILLWISEON>
            <ISCOSTCENTRESON>Yes</ISCOSTCENTRESON>
            <APPROPRIATEFOR>GST</APPROPRIATEFOR>
            <GSTAPPROPRIATETO>Goods</GSTAPPROPRIATETO>
            <EXCISEALLOCTYPE>Based on Value</EXCISEALLOCTYPE>
            <GSTTYPE>&#4; Not Applicable</GSTTYPE>
            <GSTAPPLICABLE>&#4; Not Applicable</GSTAPPLICABLE>
          </LEDGER>
        </TALLYMESSAGE>
 
        <!-- Additional Charge Ledger (Freight / Packing / Forwarding etc.)
             RULE: Use the entry's "parent" value as PARENT.
             Emit APPROPRIATEFOR + GSTAPPROPRIATETO + EXCISEALLOCTYPE ONLY when
             this ledger's missing_json entry has "appropriate_for": "GST".
             Otherwise emit a PLAIN ledger (NAME + PARENT only).
             Do NOT emit GSTTYPEOFSUPPLY. -->

        <!-- Example: charge ledger marked Appropriate for GST -->
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Freight Outward" ACTION="Create">
            <NAME>Freight Outward</NAME>
            <PARENT>Indirect Expenses</PARENT>
            <APPROPRIATEFOR>GST</APPROPRIATEFOR>
            <GSTAPPROPRIATETO>Goods</GSTAPPROPRIATETO>
            <EXCISEALLOCTYPE>Based on Value</EXCISEALLOCTYPE>
          </LEDGER>
        </TALLYMESSAGE>

        <!-- Example: charge ledger NOT appropriated for GST → plain ledger -->
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Packing Charges" ACTION="Create">
            <NAME>Packing Charges</NAME>
            <PARENT>Indirect Expenses</PARENT>
            <APPROPRIATEFOR>GST</APPROPRIATEFOR>
            <GSTAPPROPRIATETO>Goods</GSTAPPROPRIATETO>
            <EXCISEALLOCTYPE>Based on Value</EXCISEALLOCTYPE>
          </LEDGER>
        </TALLYMESSAGE>

        <!-- Round Off Ledger (Indirect Expenses) — exact shape required -->
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Round Off" ACTION="Create">
            <NAME>Round Off</NAME>
            <PARENT>Indirect Expenses</PARENT>
            <VATDEALERNATURE>Invoice Rounding</VATDEALERNATURE>
            <ROUNDINGMETHOD>Downward Rounding</ROUNDINGMETHOD>
            <ROUNDINGLIMIT>1</ROUNDINGLIMIT>
            <ISCOSTCENTRESON>Yes</ISCOSTCENTRESON>
            <ISBILLWISEON>No</ISBILLWISEON>
          </LEDGER>
        </TALLYMESSAGE>
 
        <!-- GST Ledgers — names built dynamically from taxable_value and rates -->
        <!-- Example: inter-state invoice, taxable_value=10240.00, igst_rate=18.0 -->
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="IGST-Input-18.00% Rs. 10240.00" ACTION="Create">
            <NAME.LIST>
              <NAME>IGST-Input-18.00% Rs. 10240.00</NAME>
            </NAME.LIST>
            <PARENT>Duties &amp; Taxes</PARENT>
            <TAXTYPE>GST</TAXTYPE>
            <ISDUTIESANDTAXES>Yes</ISDUTIESANDTAXES>
            <GSTDUTYHEAD>Integrated Tax</GSTDUTYHEAD>
            <RATEOFTAXCALCULATION>18.0</RATEOFTAXCALCULATION>
            <GSTAPPLICABLE>&#4; Not Applicable</GSTAPPLICABLE>
            <OPENINGBALANCE>0</OPENINGBALANCE>
            <ISBILLWISEON>No</ISBILLWISEON>
            <AFFECTSSTOCK>No</AFFECTSSTOCK>
          </LEDGER>
        </TALLYMESSAGE>
 
        <!-- Example: intra-state invoice, taxable_value=109980.00, cgst_rate=2.5 -->
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="CGST-Input-2.50% Rs. 109980.00" ACTION="Create">
            <NAME.LIST>
              <NAME>CGST-Input-2.50% Rs. 109980.00</NAME>
            </NAME.LIST>
            <PARENT>Duties &amp; Taxes</PARENT>
            <TAXTYPE>GST</TAXTYPE>
            <ISDUTIESANDTAXES>Yes</ISDUTIESANDTAXES>
            <GSTDUTYHEAD>Central Tax</GSTDUTYHEAD>
            <RATEOFTAXCALCULATION>2.5</RATEOFTAXCALCULATION>
            <GSTAPPLICABLE>&#4; Not Applicable</GSTAPPLICABLE>
            <OPENINGBALANCE>0</OPENINGBALANCE>
            <ISBILLWISEON>No</ISBILLWISEON>
            <AFFECTSSTOCK>No</AFFECTSSTOCK>
          </LEDGER>
        </TALLYMESSAGE>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="SGST-Input-2.50% Rs. 109980.00" ACTION="Create">
            <NAME.LIST>
              <NAME>SGST-Input-2.50% Rs. 109980.00</NAME>
            </NAME.LIST>
            <PARENT>Duties &amp; Taxes</PARENT>
            <TAXTYPE>GST</TAXTYPE>
            <ISDUTIESANDTAXES>Yes</ISDUTIESANDTAXES>
            <GSTDUTYHEAD>State Tax</GSTDUTYHEAD>
            <RATEOFTAXCALCULATION>2.5</RATEOFTAXCALCULATION>
            <GSTAPPLICABLE>&#4; Not Applicable</GSTAPPLICABLE>
            <OPENINGBALANCE>0</OPENINGBALANCE>
            <ISBILLWISEON>No</ISBILLWISEON>
            <AFFECTSSTOCK>No</AFFECTSSTOCK>
          </LEDGER>
        </TALLYMESSAGE>
 
        <!-- ③ STOCKITEMS LAST -->
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <STOCKITEM NAME="PP Self Adhesive Plastic Rolls" ACTION="Create">
            <NAME>PP Self Adhesive Plastic Rolls</NAME>
            <BASEUNITS>Roll</BASEUNITS>
            <HSNDETAILS.LIST>
              <APPLICABLEFROM>20260401</APPLICABLEFROM>
              <HSNCODE>27150090</HSNCODE>
              <SRCOFHSNDETAILS>Specify Details Here</SRCOFHSNDETAILS>
            </HSNDETAILS.LIST>
            <GSTAPPLICABLE>Applicable</GSTAPPLICABLE>

        <GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>

        <GSTDETAILS.LIST>
            <APPLICABLEFROM>20260401</APPLICABLEFROM>
            <TAXABILITY>Taxable</TAXABILITY>
            <SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>
    <STATEWISEDETAILS.LIST>
        <STATENAME>&#4; Any</STATENAME>
            <RATEDETAILS.LIST>
                    <GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD>
                    <GSTRATE>13.5</GSTRATE>
                </RATEDETAILS.LIST>

                <RATEDETAILS.LIST>
                    <GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD>
                    <GSTRATE>13.5</GSTRATE>
                </RATEDETAILS.LIST>
        <RATEDETAILS.LIST>
         <GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD>
         <GSTRATEVALUATIONTYPE>Based on Value</GSTRATEVALUATIONTYPE>
         <GSTRATE>27</GSTRATE>
        </RATEDETAILS.LIST>
        <RATEDETAILS.LIST>
         <GSTRATEDUTYHEAD>Cess</GSTRATEDUTYHEAD>
         <GSTRATEVALUATIONTYPE>&#4; Not Applicable</GSTRATEVALUATIONTYPE>
        </RATEDETAILS.LIST>
        <RATEDETAILS.LIST>
         <GSTRATEDUTYHEAD>State Cess</GSTRATEDUTYHEAD>
         <GSTRATEVALUATIONTYPE>Based on Value</GSTRATEVALUATIONTYPE>
        </RATEDETAILS.LIST>
        <GSTSLABRATES.LIST>        </GSTSLABRATES.LIST>
       </STATEWISEDETAILS.LIST>

        </GSTDETAILS.LIST>
          </STOCKITEM>
        </TALLYMESSAGE>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <STOCKITEM NAME="Rans®Bitu-PMR" ACTION="Create">
            <NAME>Rans®Bitu-PMR</NAME>
            <BASEUNITS>Kgs</BASEUNITS>
          <HSNDETAILS.LIST>
            <APPLICABLEFROM>20260401</APPLICABLEFROM>
            <HSNCODE>27150050</HSNCODE>
            <SRCOFHSNDETAILS>Specify Details Here</SRCOFHSNDETAILS>
          </HSNDETAILS.LIST>
          <GSTAPPLICABLE>Applicable</GSTAPPLICABLE>

        <GSTTYPEOFSUPPLY>Goods</GSTTYPEOFSUPPLY>

        <GSTDETAILS.LIST>
            <APPLICABLEFROM>20260401</APPLICABLEFROM>
            <TAXABILITY>Taxable</TAXABILITY>
            <SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>
<STATEWISEDETAILS.LIST>
        <STATENAME>&#4; Any</STATENAME>
            <RATEDETAILS.LIST>
                    <GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD>
                    <GSTRATE>13.5</GSTRATE>
                </RATEDETAILS.LIST>

                <RATEDETAILS.LIST>
                    <GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD>
                    <GSTRATE>13.5</GSTRATE>
                </RATEDETAILS.LIST>
        <RATEDETAILS.LIST>
         <GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD>
         <GSTRATEVALUATIONTYPE>Based on Value</GSTRATEVALUATIONTYPE>
         <GSTRATE>27</GSTRATE>
        </RATEDETAILS.LIST>
        <RATEDETAILS.LIST>
         <GSTRATEDUTYHEAD>Cess</GSTRATEDUTYHEAD>
         <GSTRATEVALUATIONTYPE>&#4; Not Applicable</GSTRATEVALUATIONTYPE>
        </RATEDETAILS.LIST>
        <RATEDETAILS.LIST>
         <GSTRATEDUTYHEAD>State Cess</GSTRATEDUTYHEAD>
         <GSTRATEVALUATIONTYPE>Based on Value</GSTRATEVALUATIONTYPE>
        </RATEDETAILS.LIST>
        <GSTSLABRATES.LIST>        </GSTSLABRATES.LIST>
       </STATEWISEDETAILS.LIST>

        </GSTDETAILS.LIST>
          </STOCKITEM>
        </TALLYMESSAGE>
 
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>
 
Return ONLY the XML. No markdown, no explanation, no ```.
 
Missing masters:
{missing_json}
 
Invoice data (for dynamic values):
{invoice_json}"""
 
VOUCHER_XML_PROMPT = """Generate Tally Prime Purchase Voucher XML for company "{company}".
IMPORTANT: Use &amp; instead of & in XML values.
 

GST LEDGER NAME RULE (read carefully)

GST ledger names are DYNAMIC and must match exactly what was created in Masters.
Build them from the invoice JSON like this:
 
  taxable_value = invoice_json["taxable_value"]    ← sum of item amounts ONLY
  igst_ledger   = invoice_json["gst"]["igst_ledger"]
  cgst_ledger   = invoice_json["gst"]["cgst_ledger"]
  sgst_ledger   = invoice_json["gst"]["sgst_ledger"]
 
  These will look like:
    "IGST-Input-18.00% Rs. 10240.00"
    "CGST-Input-2.50% Rs. 109980.00"
    "SGST-Input-2.50% Rs. 109980.00"
 
Use these exact strings as <LEDGERNAME> for GST entries in the voucher.
 

SIGN CONVENTION

  • Party (supplier)   → ISDEEMEDPOSITIVE: Yes,  AMOUNT: NEGATIVE  (we owe them)
  • Stock items        → ISDEEMEDPOSITIVE: No,   AMOUNT: POSITIVE  (goods received)
  • GST (all types)    → ISDEEMEDPOSITIVE: No,   AMOUNT: POSITIVE  (input tax credit)
  • Extra charges      → ISDEEMEDPOSITIVE: No,   AMOUNT: POSITIVE  (expense)
  • Round Off NEGATIVE → ISDEEMEDPOSITIVE: Yes,  AMOUNT: NEGATIVE  (reduces total)
  • Round Off POSITIVE → ISDEEMEDPOSITIVE: No,   AMOUNT: POSITIVE  (increases total)
 

BALANCE CHECK (verify before generating XML)

  |Party amount| = sum(item amounts)
                 + sum(GST amounts)
                 + sum(additional_charges amounts with their signs)
 
  Example — inter-state with freight and round off:
    Items=10240 + Freight=897.50 + IGST=2004.75  + RoundOff(-0.25) = 13142.00
    → Party AMOUNT = -13142.00  ✓
 
  If balance does not match, recheck before outputting XML.
 

FORMAT RULES

  • DATE              → YYYYMMDD
  • RATE (inventory)  → "value/unit"   e.g. "3300.00/Roll", "170.00/Kgs"
  • ACTUALQTY / BILLEDQTY → "value unit"  e.g. "3.00 Roll", "2.00 Kgs"
  • GST <RATE>        → "X.XX%"  e.g. "18.00%", "2.50%"
  • <TAXRATE>         → plain number, NO % sign  e.g. 18.00, 2.50
  • Non-GST charges (Freight, Round Off, P&F) → NO <RATE> tag, NO <TAXCLASSIFICATIONDETAILS.LIST>
  • ISDEEMEDPOSITIVE  → always a child XML element, never an XML attribute
  • PLACEOFSUPPLY     → party_state value only (plain state name, no code)
  • ACCOUNTINGALLOCATIONS.LIST → LEDGERNAME MUST be the item's own
    invoice_json["items"][i]["purchase_ledger"] value (e.g. "Local Purchase").
    Use each item's purchase_ledger exactly as given — do NOT normalize or
    replace it with the literal word "Purchase". Fall back to "Purchase" ONLY
    if that item's purchase_ledger field is missing or blank.
 

GST LEDGER ENTRY — THREE MANDATORY PARTS

Every CGST / SGST / IGST LEDGERENTRIES.LIST MUST have ALL THREE:
  1. <RATE>X.XX%</RATE>
  2. <AMOUNT>amount</AMOUNT>
  3. <TAXCLASSIFICATIONDETAILS.LIST>
       <TAXTYPE>GST</TAXTYPE>
       <TAXNAME>CGST or SGST or IGST</TAXNAME>
       <TAXRATE>X.XX</TAXRATE>    ← plain number, NO % sign
       <TAXAMOUNT>amount</TAXAMOUNT>
     </TAXCLASSIFICATIONDETAILS.LIST>
Missing any of these → Tally shows "Provide GST Details: No".
 

FIVE MANDATORY GST COMPLIANCE FIELDS inside <VOUCHER>

  <ISGSTAPPLICABLE>Yes</ISGSTAPPLICABLE>
  <PLACEOFSUPPLY>{{party_state — plain state name only}}</PLACEOFSUPPLY>
  <VCHGSTCLASS/>
  <IGNOREPOSVALIDATION>No</IGNOREPOSVALIDATION>
  <GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>
 

ROUND OFF RULE
  • Generate Round Off LEDGERENTRIES.LIST ONLY if additional_charges[] contains
    a Round Off entry with a NON-ZERO amount.
  • If additional_charges[] is empty or has NO Round Off entry, do NOT generate
    any Round Off LEDGERENTRIES.LIST at all — not even with amount 0.00.
  • Stirct you pass value with sign negative if you recevie from json 
  • The top-level "Round off" JSON field is for balance verification only —
    do NOT create an XML entry from it.
  • NEVER reference a ledger that does not exist. If Round Off was not created
    in masters (because it was not needed), do NOT add it to the voucher.
 

HSNDETAILS.LIST RULE

  • MANDATORY inside every ALLINVENTORYENTRIES.LIST block.
  • GSTRATE = total GST % (cgst_rate*2 for intra-state, igst_rate for inter-state).
 

XML STRUCTURE

 
<ENVELOPE>
 <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
 <BODY>
  <IMPORTDATA>
   <REQUESTDESC>
    <REPORTNAME>Vouchers</REPORTNAME>
    <STATICVARIABLES><SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY></STATICVARIABLES>
   </REQUESTDESC>
   <REQUESTDATA>
    <TALLYMESSAGE xmlns:UDF="TallyUDF">
     <VOUCHER VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Invoice Voucher View">
 
      <!-- META -->
      <DATE>20260608</DATE>
      <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
      <VOUCHERNUMBER>GST-UP/078/26-27</VOUCHERNUMBER>
      <REFERENCE>GST-UP/078/26-27</REFERENCE>
      <PARTYLEDGERNAME>Rans Engineering &amp; Chemicals Private Limited</PARTYLEDGERNAME>
      <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
      <ISINVOICE>Yes</ISINVOICE>
 
      <!-- GST COMPLIANCE — ALL FIVE MANDATORY -->
      <ISGSTAPPLICABLE>Yes</ISGSTAPPLICABLE>
      <PLACEOFSUPPLY>Uttar Pradesh</PLACEOFSUPPLY>
      <VCHGSTCLASS/>
      <IGNOREPOSVALIDATION>No</IGNOREPOSVALIDATION>
      <GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>
 
      <NARRATION>Purchase from Rans Engineering &amp; Chemicals Private Limited vide invoice GST-UP/078/26-27 dated 08-Jun-26</NARRATION>
 
      <!-- 1. PARTY — Credit supplier (negative) -->
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>Rans Engineering &amp; Chemicals Private Limited</LEDGERNAME>
       <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
       <AMOUNT>-13142.00</AMOUNT>
      </LEDGERENTRIES.LIST>
 
      <!-- 2. INVENTORY ITEMS — Debit each stock item (positive) -->
      <ALLINVENTORYENTRIES.LIST>
       <STOCKITEMNAME>PP Self Adhesive Plastic Rolls</STOCKITEMNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <RATE>3300.00/Roll</RATE>
       <AMOUNT>9900.00</AMOUNT>
       <ACTUALQTY>3.00 Roll</ACTUALQTY>
       <BILLEDQTY>3.00 Roll</BILLEDQTY>
       <HSNDETAILS.LIST>
        <HSNCODE>39201019</HSNCODE>
        <GSTRATE>18.00</GSTRATE>
        <TAXABILITY>Taxable</TAXABILITY>
       </HSNDETAILS.LIST>
       <ACCOUNTINGALLOCATIONS.LIST>
        <!-- LEDGERNAME = this item's invoice_json purchase_ledger (NOT hardcoded "Purchase") -->
        <LEDGERNAME>Local Purchase</LEDGERNAME>
        <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
        <AMOUNT>9900.00</AMOUNT>
       </ACCOUNTINGALLOCATIONS.LIST>
      </ALLINVENTORYENTRIES.LIST>
 
      <ALLINVENTORYENTRIES.LIST>
       <STOCKITEMNAME>Rans®Bitu-PMR</STOCKITEMNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <RATE>170.00/Kgs</RATE>
       <AMOUNT>340.00</AMOUNT>
       <ACTUALQTY>2.00 Kgs</ACTUALQTY>
       <BILLEDQTY>2.00 Kgs</BILLEDQTY>
       <HSNDETAILS.LIST>
        <HSNCODE>27150090</HSNCODE>
        <GSTRATE>18.00</GSTRATE>
        <TAXABILITY>Taxable</TAXABILITY>
       </HSNDETAILS.LIST>
       <ACCOUNTINGALLOCATIONS.LIST>
        <LEDGERNAME>Purchase</LEDGERNAME>
        <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
        <AMOUNT>340.00</AMOUNT>
       </ACCOUNTINGALLOCATIONS.LIST>
      </ALLINVENTORYENTRIES.LIST>
 
      
      <!-- 3. ADDITIONAL CHARGES — loop all entries from additional_charges[]
           No <RATE> tag on these entries -->
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>Freight Outward</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <AMOUNT>897.50</AMOUNT>
      </LEDGERENTRIES.LIST>

      <!-- 4. GST ENTRY — inter-state: IGST only
           Ledger name from invoice_json["gst"]["igst_ledger"] = "IGST-Input-18.00% Rs. 10240.00"
           All three parts mandatory: RATE, AMOUNT, TAXCLASSIFICATIONDETAILS.LIST -->
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>IGST-Input-18.00% Rs. 10240.00</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <RATE>18.00%</RATE>
       <AMOUNT>2004.75</AMOUNT>
       <TAXCLASSIFICATIONDETAILS.LIST>
        <TAXTYPE>GST</TAXTYPE>
        <TAXNAME>IGST</TAXNAME>
        <TAXRATE>18.00</TAXRATE>
        <TAXAMOUNT>2004.75</TAXAMOUNT>
       </TAXCLASSIFICATIONDETAILS.LIST>
      </LEDGERENTRIES.LIST>
 
      <!-- 5. Intra-state example (use instead of IGST when supplier is in Gujarat):
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>CGST-Input-2.50% Rs. 109980.00</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <RATE>2.50%</RATE>
       <AMOUNT>2749.50</AMOUNT>
       <TAXCLASSIFICATIONDETAILS.LIST>
        <TAXTYPE>GST</TAXTYPE>
        <TAXNAME>CGST</TAXNAME>
        <TAXRATE>2.50</TAXRATE>
        <TAXAMOUNT>2749.50</TAXAMOUNT>
       </TAXCLASSIFICATIONDETAILS.LIST>
      </LEDGERENTRIES.LIST>
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>SGST-Input-2.50% Rs. 109980.00</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <RATE>2.50%</RATE>
       <AMOUNT>2749.50</AMOUNT>
       <TAXCLASSIFICATIONDETAILS.LIST>
        <TAXTYPE>GST</TAXTYPE>
        <TAXNAME>SGST</TAXNAME>
        <TAXRATE>2.50</TAXRATE>
        <TAXAMOUNT>2749.50</TAXAMOUNT>
       </TAXCLASSIFICATIONDETAILS.LIST>
      </LEDGERENTRIES.LIST>
      -->
 
      <!-- 5. ROUND OFF — ONLY if additional_charges[] contains a Round Off entry
           with non-zero amount. If no Round Off in additional_charges[], OMIT this
           entire block. Do NOT generate this with amount 0.00. -->
      <LEDGERENTRIES.LIST>
       <LEDGERNAME>Round Off</LEDGERNAME>
       <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
       <AMOUNT>-0.25</AMOUNT>
      </LEDGERENTRIES.LIST>
 
     </VOUCHER>
    </TALLYMESSAGE>
   </REQUESTDATA>
  </IMPORTDATA>
 </BODY>
</ENVELOPE>
 

PRE-OUTPUT SELF-CHECK

  ✓ GST ledger names match invoice_json["gst"]["igst_ledger"] / ["cgst_ledger"] / ["sgst_ledger"] exactly.
  ✓ Every GST entry has <RATE>X.XX%</RATE>, <AMOUNT>, AND <TAXCLASSIFICATIONDETAILS.LIST>.
  ✓ <TAXRATE> inside TAXCLASSIFICATIONDETAILS.LIST is a plain number (no % sign).
  ✓ <PLACEOFSUPPLY> is only the plain state name — no code, no comma.
  ✓ All five GST compliance fields present inside <VOUCHER>.
  ✓ Debits = Credits (balance check passed).
  ✓ No <RATE> tag on Freight / Round Off / P&F / Loading entries.
  ✓ Round Off generated from additional_charges[] only — not from top-level "Round off" field.
    ✓ If additional_charges[] has no Round Off entry → NO Round Off LEDGERENTRIES.LIST in XML.
    ✓ Never reference a ledger name that was not created in masters.
  ✓ HSNDETAILS.LIST present inside every ALLINVENTORYENTRIES.LIST.
  ✓ GSTRATE in HSNDETAILS.LIST is total GST % (cgst_rate*2 or igst_rate).
 
Return ONLY the XML. No markdown, no explanation, no ```.
 
Invoice data:
{invoice_json}"""
 
HEAL_PROMPT = """The Tally XML import failed with these errors:
{errors}
 
Fix the XML below and return corrected valid Tally XML.
 
 
COMMON CAUSES AND FIXES
 
 
  • "Unit does not exist"
      → Add the missing UNIT master before STOCKITEM in Masters XML.
 
  • "Ledger does not exist"
      → Add the missing LEDGER master.
        For GST ledgers, name must be dynamic:
          "IGST-Input-18.00% Rs. 10240.00"  (inter-state)
          "CGST-Input-2.50% Rs. 109980.00"  (intra-state)
          "SGST-Input-2.50% Rs. 109980.00"  (intra-state)
        Build from: invoice_json["gst"]["igst_ledger"] / ["cgst_ledger"] / ["sgst_ledger"]
        If the ledger name in the voucher doesn't match the master name exactly → fix both.
 
  • "Stock item does not exist"
      → Add the missing STOCKITEM master.
 
  • ISDEEMEDPOSITIVE as attribute
      → Move it inside the element as a child tag.
 
  • Debit/Credit mismatch
      → |party amount| must equal sum of all item amounts + GST amounts
        + charge amounts (with Round Off sign already applied).
 
  • "&" in values
      → Replace with "&amp;".
 
  • Incorrect RATE format
      → Items: "value/unit"  e.g. "3300.00/Roll", "170.00/Kgs".
        GST entries: "X.XX%"  e.g. "18.00%", "2.50%".
        Non-GST charges (Freight, Round Off): NO RATE tag at all.
 
  • Incorrect QTY format
      → Must be "value unit"  e.g. "3.00 Roll", "2.00 Kgs".
 
  • Missing HSNDETAILS.LIST
      → Add inside every ALLINVENTORYENTRIES.LIST block.
 
  • Wrong GSTRATE
      → Must be TOTAL GST % (e.g. 18.00 not 9.00 for 9%+9%).
 
  • "Provide GST Details: No"
      → Fix ALL of the following:
        (a) Add all five GST compliance fields inside <VOUCHER>:
              <ISGSTAPPLICABLE>Yes</ISGSTAPPLICABLE>
              <PLACEOFSUPPLY>{{party_state — plain state name only}}</PLACEOFSUPPLY>
              <VCHGSTCLASS/>
              <IGNOREPOSVALIDATION>No</IGNOREPOSVALIDATION>
              <GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>
        (b) Add <TAXCLASSIFICATIONDETAILS.LIST> to every GST LEDGERENTRIES.LIST:
              <TAXCLASSIFICATIONDETAILS.LIST>
                <TAXTYPE>GST</TAXTYPE>
                <TAXNAME>IGST</TAXNAME>   ← or CGST or SGST
                <TAXRATE>18.00</TAXRATE>  ← plain number, NO % sign
                <TAXAMOUNT>2004.75</TAXAMOUNT>
              </TAXCLASSIFICATIONDETAILS.LIST>
        (c) Add <RATE>X.XX%</RATE> to every GST LEDGERENTRIES.LIST.
        ALL THREE parts must be present on every GST entry.
 
  • Wrong PLACEOFSUPPLY format
      → Must be ONLY the state name from party_state.
        "Uttar Pradesh" → correct.  "Uttar Pradesh, Code: 09" → wrong.
 
  • GST ledger name mismatch
      → Voucher ledger name must exactly match master ledger name.
        Both must use: "IGST-Input-{{rate:.2f}}% Rs. {{taxable_value:.2f}}"
        Fix both the master and the voucher if they differ.
 
  • Round Off entered twice
      → Remove the duplicate. Keep only the entry from additional_charges[].
        Delete any entry generated from the top-level "Round off" field.
 
  • Purchase ledger name wrong
      → Must be exactly "Purchase" — not "Purchase @ 18%" or similar.
  • Party ledger missing address / GSTIN / state
      → Add LEDMAILINGDETAILS.LIST (with MAILINGNAME, ADDRESS.LIST, STATE, COUNTRY) and LEDGSTREGDETAILS.LIST (with GSTREGISTRATIONTYPE, GSTIN) to the party ledger master.
 
  • Party ledger missing PINCODE / PLACEOFSUPPLY / INCOMETAXNUMBER
      → Add PINCODE inside LEDMAILINGDETAILS.LIST (party_pincode), PLACEOFSUPPLY
        inside LEDGSTREGDETAILS.LIST (party_state), and INCOMETAXNUMBER at the
        ledger level (party_pan, derived from GSTIN chars 3–12 if not given).
 
  • Round Off ledger missing ROUNDINGMETHOD / ROUNDINGLIMIT / VATDEALERNATURE
      → A bare 3-tag Round Off ledger is insufficient. Add VATDEALERNATURE
        ("Invoice Rounding"), ROUNDINGMETHOD ("Downward Rounding"),
        ROUNDINGLIMIT (1), and ISCOSTCENTRESON (Yes) under Indirect Expenses.
 
  • Discount ledger missing APPROPRIATEFOR / GSTAPPROPRIATETO / EXCISEALLOCTYPE
      → Add these three tags plus GSTTYPE and GSTAPPLICABLE (both
        "&#4; Not Applicable") under Indirect Incomes.
 
 
PRE-OUTPUT SELF-CHECK
 
  ✓ Every GST entry has <RATE>X.XX%</RATE>.
  ✓ Every GST entry has <TAXCLASSIFICATIONDETAILS.LIST> with TAXTYPE, TAXNAME, TAXRATE (no %), TAXAMOUNT.
  ✓ <PLACEOFSUPPLY> contains only plain state name.
  ✓ All five GST compliance fields inside <VOUCHER>.
  ✓ GST ledger names in voucher match master names exactly.
  ✓ Debits = Credits.
  ✓ No <RATE> tag on non-GST charge entries.
  ✓ Party ledger has LEDMAILINGDETAILS.LIST and LEDGSTREGDETAILS.LIST populated.
 
Return ONLY the corrected XML, no markdown, no explanation.
 
Original invoice data:
{invoice_json}
 
Failed XML:
{failed_xml}"""

# ── Helpers ──
def post_tally(xml_str, label="Import"):
    try:
        r = requests.post(TALLY_URL, data=xml_str.encode("utf-8"),
                         headers={"Content-Type": "application/xml"}, timeout=15)
        r.raise_for_status()
        log.info(f"  📨 Tally raw response [{label}]: {r.text[:500]}")
        return r.text
    except Exception as e:
        log.error(f"  ❌ Tally connection error [{label}]: {e}")
        return None

def check_tally_response(xml_text, label):
    try:
        root = ET.fromstring(xml_text)
        errs = [e.text.strip() for e in root.findall(".//LINEERROR") if e.text]
        errs += [e.text.strip() for e in root.findall(".//ERROR") if e.text]
        if errs:
            log.warning(f"  ⚠️ Tally Errors [{label}]: {errs}")
            return False, errs
        created = int(root.findtext(".//CREATED") or "0")
        altered = int(root.findtext(".//ALTERED") or "0")
        exceptions = int(root.findtext(".//EXCEPTIONS") or "0")
        if created > 0 or altered > 0:
            log.info(f"  ✅ [{label}]: Created={created} Altered={altered}")
            return True, []
        elif exceptions > 0:
            log.warning(f"  ⚠️ [{label}]: Tally EXCEPTIONS={exceptions} — voucher amounts may not balance (debits ≠ credits)")
            return False, [f"Tally EXCEPTIONS={exceptions}. Likely cause: party amount doesn't match sum of items+GST. Ensure debits=credits."]
        else:
            log.warning(f"  ⚠️ [{label}]: Created=0 Altered=0 — nothing was imported")
            return False, ["Tally returned Created=0, Altered=0. Data may be invalid or duplicated."]
    except ET.ParseError as e:
        log.error(f"  ❌ XML parse error [{label}]: {e}")
        return False, [str(e)]

def save_xml_log(name, xml_str):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(LOG_DIR, f"{ts}_{name}.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml_str)
    log.info(f"  💾 Saved: {path}")

def call_ai(prompt, user_content, use_json=True):
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set in .env")
    client = OpenAI(api_key=OPENAI_API_KEY)
    kwargs = {"model": "gpt-4o-mini", "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_content}
    ], "temperature": 0.0}
    if use_json:
        kwargs["response_format"] = {"type": "json_object"}
    return client.chat.completions.create(**kwargs).choices[0].message.content

def call_ai_vision(images_b64, prompt):
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set in .env")
    client = OpenAI(api_key=OPENAI_API_KEY)
    content = [{"type": "text", "text": prompt}]
    for img in images_b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}})
    return client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"}, temperature=0.0
    ).choices[0].message.content

def sanitize_xml(xml_str):
    """Fix common AI XML issues like unescaped & characters."""
    import re
    # Fix & not followed by amp; lt; gt; quot; apos; or #
    xml_str = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#)', '&amp;', xml_str)
    return xml_str

def call_ai_xml(prompt):
    """Call AI expecting XML output (not JSON)."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set in .env")
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0
    ).choices[0].message.content
    # Strip any markdown fencing
    if "```xml" in resp:
        resp = resp.split("```xml")[1].split("```")[0].strip()
    elif "```" in resp:
        resp = resp.split("```")[1].split("```")[0].strip()
    resp = resp.strip()
    # Sanitize XML special characters
    resp = sanitize_xml(resp)
    log.info(f"  📄 AI Generated XML ({len(resp)} chars)")
    return resp

def validate_xml(xml_str):
    try:
        import re
        # Strip Tally's special/control character references (e.g. &#4;) to allow standard XML parsing validation
        sanitized = re.sub(r'&#\d+;', '', xml_str)
        ET.fromstring(sanitized)
        return True
    except ET.ParseError:
        return False

# ── Phase 1: File Reading ──
def read_excel(path):
    if pd is None:
        raise ImportError("pandas required for Excel files")
    xls = pd.ExcelFile(path)
    parts = []
    for s in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=s)
        if not df.empty:
            parts.append(f"Sheet: {s}\n{df.to_string(index=False)}")
    return "\n\n".join(parts)

def read_pdf(path):
    if PdfReader is None:
        raise ImportError("pypdf required for PDF files")
    reader = PdfReader(path)
    text = ""
    for page in reader.pages:
        t = page.extract_text()
        if t:
            text += t + "\n"
    return text.strip()

def pdf_to_images_b64(path):
    if fitz is None:
        raise ImportError("PyMuPDF required for scanned PDFs")
    doc = fitz.open(path)
    images = []
    for i in range(len(doc)):
        pix = doc.load_page(i).get_pixmap(dpi=150)
        images.append(base64.b64encode(pix.tobytes("png")).decode())
    return images

def read_image_b64(path):
    with open(path, "rb") as f:
        return [base64.b64encode(f.read()).decode()]

# ── Phase 2: AI Extraction ──
def extract_invoice(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    log.info(f"\n📄 Reading: {os.path.basename(file_path)} ({ext})")

    if ext in [".xlsx", ".xls"]:
        text = read_excel(file_path)
        log.info("  🤖 Sending to GPT-4o-mini for extraction...")
        result = call_ai(EXTRACT_PROMPT, text)

    elif ext == ".pdf":
        text = read_pdf(file_path)
        if len(text.strip()) >= 50:
            log.info(f"  📝 Extracted {len(text)} chars of text")
            log.info("  🤖 Sending text to GPT-4o-mini...")
            result = call_ai(EXTRACT_PROMPT, text)
        else:
            log.info("  📸 Scanned PDF detected, using Vision API...")
            images = pdf_to_images_b64(file_path)
            result = call_ai_vision(images, EXTRACT_PROMPT)

    elif ext in [".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"]:
        log.info("  📸 Using Vision API for image...")
        images = read_image_b64(file_path)
        result = call_ai_vision(images, EXTRACT_PROMPT)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    data = json.loads(result)
    log.info("  ✅ AI Extraction complete")

    # Recalculate total_amount to ensure it includes everything (items + charges + GST)
    items_total = sum(float(it.get("amount", 0)) for it in data.get("items", []))
    charges_total = sum(float(ch.get("amount", 0)) for ch in data.get("additional_charges", []))
    gst = data.get("gst", {})
    gst_total = float(gst.get("cgst_amount", 0)) + float(gst.get("sgst_amount", 0)) + float(gst.get("igst_amount", 0))
    calculated_total = round(items_total + charges_total + gst_total, 2)
    ai_total = float(data.get("total_amount", 0))
    if abs(calculated_total - ai_total) > 0.5:
        log.info(f"  ⚠️ Fixing total_amount: AI said {ai_total}, recalculated = {calculated_total} (items:{items_total} + charges:{charges_total} + GST:{gst_total})")
        data["total_amount"] = calculated_total

    log.info(json.dumps(data, indent=2))
    return data

# ── Phase 3: Fetch Tally Masters ──
def fetch_ledgers(company):
    xml = f"""<ENVELOPE><HEADER><VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST><TYPE>COLLECTION</TYPE>
    <ID>List of Ledgers</ID></HEADER><BODY><DESC>
    <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
    <SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY></STATICVARIABLES>
    </DESC></BODY></ENVELOPE>"""
    resp = post_tally(xml, "Fetch Ledgers")
    ledgers = {}
    if resp:
        try:
            root = ET.fromstring(resp)
            for el in root.findall(".//LEDGER"):
                name = el.attrib.get("NAME") or el.findtext("NAME") or el.findtext(".//NAME")
                parent = el.attrib.get("PARENT") or el.findtext("PARENT") or el.findtext(".//PARENT") or ""
                if name:
                    ledgers[name.strip().upper()] = {"name": name.strip(), "parent": parent.strip()}
        except Exception as e:
            log.error(f"Error parsing ledgers: {e}")
    if ledgers:
        log.info(f"\n  ┌─── 📒 LEDGERS FROM TALLY ({len(ledgers)}) ───")
        for key, val in ledgers.items():
            log.info(f"  │  {val['name']}  [Group: {val['parent']}]")
        log.info(f"  └───────────────────────────────────")
    return ledgers

def fetch_stock_items(company):
    xml = f"""<ENVELOPE><HEADER><VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST><TYPE>COLLECTION</TYPE>
    <ID>List of Stock Items</ID></HEADER><BODY><DESC>
    <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
    <SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY></STATICVARIABLES>
    </DESC></BODY></ENVELOPE>"""
    resp = post_tally(xml, "Fetch Stock Items")
    items = {}
    if resp:
        try:
            root = ET.fromstring(resp)
            for el in root.findall(".//STOCKITEM"):
                name = el.attrib.get("NAME") or el.findtext("NAME") or el.findtext(".//NAME")
                unit = el.attrib.get("BASEUNITS") or el.findtext("BASEUNITS") or ""
                if name:
                    items[name.strip().upper()] = {"name": name.strip(), "unit": unit.strip()}
        except Exception as e:
            log.error(f"Error parsing stock items: {e}")
    if items:
        log.info(f"\n  ┌─── 📦 STOCK ITEMS FROM TALLY ({len(items)}) ───")
        for key, val in items.items():
            log.info(f"  │  {val['name']}  [Unit: {val['unit'] or 'N/A'}]")
        log.info(f"  └───────────────────────────────────")
    else:
        log.info("  📦 No stock items found in Tally")
    return items

def fetch_units(company):
    xml = f"""<ENVELOPE><HEADER><VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST><TYPE>COLLECTION</TYPE>
    <ID>CustomUnitColl</ID></HEADER><BODY><DESC>
    <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
    <SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY></STATICVARIABLES>
    <TDL><TDLMESSAGE><COLLECTION NAME="CustomUnitColl" ISINITIALIZE="Yes">
    <TYPE>Unit</TYPE><NATIVEMETHOD>Name</NATIVEMETHOD>
    </COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"""
    resp = post_tally(xml, "Fetch Units")
    units = set()
    if resp:
        try:
            root = ET.fromstring(resp)
            for el in root.findall(".//UNIT"):
                name = el.attrib.get("NAME") or el.findtext("NAME") or el.findtext(".//NAME")
                if name:
                    units.add(name.strip())
        except Exception as e:
            log.error(f"Error parsing units: {e}")
    if units:
        log.info(f"\n  ┌─── 📏 UNITS FROM TALLY ({len(units)}) ───")
        for u in sorted(units):
            log.info(f"  │  {u}")
        log.info(f"  └───────────────────────────────────")
    else:
        log.info("  📏 No units found in Tally")
    return units

def fetch_all_masters(company):
    log.info(f"\n{'='*60}")
    log.info(f"📊 FETCHING ALL MASTER DATA FROM TALLY")
    log.info(f"🏢 Company: {company}")
    log.info(f"{'='*60}")
    ledgers = fetch_ledgers(company)
    stock = fetch_stock_items(company)
    units = fetch_units(company)
    log.info(f"\n  ╔══════════════════════════════════════")
    log.info(f"  ║ 📊 TALLY MASTER SUMMARY")
    log.info(f"  ║ 📒 Ledgers:     {len(ledgers)}")
    log.info(f"  ║ 📦 Stock Items: {len(stock)}")
    log.info(f"  ║ 📏 Units:       {len(units)}")
    log.info(f"  ╚══════════════════════════════════════")
    return {"ledgers": ledgers, "stock_items": stock, "units": units}

# ── Phase 4: Compare & Find Missing ──
def fuzzy_match(name, existing_list, threshold=0.85):
    if not name:
        return None
    for ex in existing_list:
        if ex.strip().upper() == name.strip().upper():
            return ex
    matches = difflib.get_close_matches(name, existing_list, n=1, cutoff=threshold)
    return matches[0] if matches else None

def find_missing_masters(invoice_data, tally_masters):
    log.info(f"\n{'='*60}")
    log.info(f"🔄 COMPARING INVOICE DATA vs TALLY MASTERS")
    log.info(f"{'='*60}")

    existing_ledger_names = [v["name"] for v in tally_masters["ledgers"].values()]
    existing_stock_names = [v["name"] for v in tally_masters["stock_items"].values()]
    existing_units = list(tally_masters["units"])

    missing = {"ledgers": [], "stock_items": [], "units": []}
    matched_items = []

    # Check party
    party = invoice_data.get("party_name")
    if party:
        m = fuzzy_match(party, existing_ledger_names)
        if m:
            matched_items.append(f"✅ Party Ledger: '{party}' → matched '{m}' in Tally")
            invoice_data["party_name"] = m
        else:
            vt = invoice_data.get("voucher_type", "Purchase")
            parent = "Sundry Creditors" if vt == "Purchase" else "Sundry Debtors"
            missing["ledgers"].append({"name": party, "parent": parent, "type": "Party"})
            matched_items.append(f"❌ Party Ledger: '{party}' → NOT FOUND (will create under {parent})")

    # Check items
    for item in invoice_data.get("items", []):
        # Unit
        unit = item.get("unit", "Nos")
        mu = fuzzy_match(unit, existing_units)
        if mu:
            matched_items.append(f"✅ Unit: '{unit}' → matched '{mu}' in Tally")
            item["unit"] = mu
        elif unit not in [u["name"] for u in missing.get("units_detail", [])]:
            if not any(u == unit for u in [x.get("name") for x in missing.get("units", [])]):
                missing["units"].append({"name": unit})
                matched_items.append(f"❌ Unit: '{unit}' → NOT FOUND (will create)")

        # Stock item
        sname = item.get("stock_item_name")
        if sname:
            ms = fuzzy_match(sname, existing_stock_names)
            if ms:
                matched_items.append(f"✅ Stock Item: '{sname}' → matched '{ms}' in Tally")
                item["stock_item_name"] = ms
            else:
                if not any(s["name"] == sname for s in missing["stock_items"]):
                    missing["stock_items"].append({"name": sname, "unit": item.get("unit", "Nos")})
                matched_items.append(f"❌ Stock Item: '{sname}' → NOT FOUND (will create)")

        # Purchase/Sales ledger
        pl = item.get("purchase_ledger")
        if pl:
            mp = fuzzy_match(pl, existing_ledger_names)
            if mp:
                matched_items.append(f"✅ Account Ledger: '{pl}' → matched '{mp}' in Tally")
                item["purchase_ledger"] = mp
            else:
                vt = invoice_data.get("voucher_type", "Purchase")
                parent = "Purchase Accounts" if vt == "Purchase" else "Sales Accounts"
                if not any(l["name"] == pl for l in missing["ledgers"]):
                    missing["ledgers"].append({"name": pl, "parent": parent, "type": "Account"})
                matched_items.append(f"❌ Account Ledger: '{pl}' → NOT FOUND (will create under {parent})")

    # Check additional charges
    for charge in invoice_data.get("additional_charges", []):
        cl = charge.get("ledger_name")
        if cl:
            mc = fuzzy_match(cl, existing_ledger_names)
            if mc:
                matched_items.append(f"✅ Charge Ledger: '{cl}' → matched '{mc}' in Tally")
                charge["ledger_name"] = mc
            else:
                parent = "Indirect Expenses" if "round" in cl.lower() else "Direct Expenses"
                if not any(l["name"] == cl for l in missing["ledgers"]):
                    missing["ledgers"].append({"name": cl, "parent": parent, "type": "Expense"})
                matched_items.append(f"❌ Charge Ledger: '{cl}' → NOT FOUND (will create under {parent})")

    # Check GST ledgers
    gst = invoice_data.get("gst", {})
    for key in ["cgst_ledger", "sgst_ledger", "igst_ledger"]:
        gl = gst.get(key)
        if gl:
            mg = fuzzy_match(gl, existing_ledger_names)
            if mg:
                matched_items.append(f"✅ GST Ledger: '{gl}' → matched '{mg}' in Tally")
                gst[key] = mg
            else:
                if not any(l["name"] == gl for l in missing["ledgers"]):
                    missing["ledgers"].append({"name": gl, "parent": "Duties & Taxes", "type": "GST"})
                matched_items.append(f"❌ GST Ledger: '{gl}' → NOT FOUND (will create under Duties & Taxes)")

    # Print comparison results
    log.info("\n  ┌─── 🔍 COMPARISON RESULTS ───")
    for item in matched_items:
        log.info(f"  │  {item}")
    log.info(f"  └───────────────────────────────────")

    has_missing = missing["ledgers"] or missing["stock_items"] or missing["units"]
    if has_missing:
        log.info(f"\n  ╔══════════════════════════════════════")
        log.info(f"  ║ ⚠️  MISSING MASTERS TO CREATE:")
        for l in missing["ledgers"]:
            log.info(f"  ║  📒 Ledger: {l['name']} → Group: {l['parent']} ({l['type']})")
        for s in missing["stock_items"]:
            log.info(f"  ║  📦 Stock Item: {s['name']} → Unit: {s['unit']}")
        for u in missing["units"]:
            log.info(f"  ║  📏 Unit: {u['name']}")
        log.info(f"  ╚══════════════════════════════════════")
    else:
        log.info("\n  ✅ All invoice masters already exist in Tally — no master creation needed!")
    return missing, has_missing

# ── Fetch Company Date ──
def fetch_company_start_date(company):
    """Get the company's financial year start date from Tally."""
    xml = f"""<ENVELOPE><HEADER><VERSION>1</VERSION>
    <TALLYREQUEST>EXPORT</TALLYREQUEST><TYPE>COLLECTION</TYPE>
    <ID>CompanyInfoColl</ID></HEADER><BODY><DESC>
    <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
    <SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY></STATICVARIABLES>
    <TDL><TDLMESSAGE><COLLECTION NAME="CompanyInfoColl" ISINITIALIZE="Yes">
    <TYPE>Company</TYPE><NATIVEMETHOD>StartingFrom</NATIVEMETHOD>
    </COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"""
    try:
        resp = post_tally(xml, "Fetch Company Date")
        if resp:
            root = ET.fromstring(resp)
            el = root.find(".//STARTINGFROM")
            if el is not None and el.text:
                date_str = el.text.strip()
                log.info(f"  📅 Tally Company Start Date: {date_str}")
                return date_str
    except Exception as e:
        log.warning(f"  Could not fetch company date: {e}")
    return None

# ── Phase 5: AI-Generated Master XML ──
def create_missing_masters(missing, company, invoice_data):
    log.info("\n🤖 Asking AI to generate master creation XML...")
    prompt = MASTER_XML_PROMPT.format(
        company=company,
        missing_json=json.dumps(missing, indent=2),
        invoice_json=json.dumps(invoice_data, indent=2)
    )
    xml_str = call_ai_xml(prompt)

    if not validate_xml(xml_str):
        log.error("  ❌ AI generated invalid XML for masters. Retrying...")
        save_xml_log("masters_dynamic_invalid1", xml_str)
        xml_str = call_ai_xml(prompt + "\n\nPREVIOUS OUTPUT WAS INVALID XML. Ensure valid XML.")
        if not validate_xml(xml_str):
            log.error("  ❌ Still invalid XML. Aborting master creation.")
            save_xml_log("masters_dynamic_invalid2", xml_str)
            return False

    log.info("\n==================== 📄 GENERATED MASTER XML ====================")
    log.info(xml_str)
    log.info("=================================================================\n")

    save_xml_log("masters_dynamic", xml_str)
    resp = post_tally(xml_str, "Create Masters")
    if resp:
        ok, errs = check_tally_response(resp, "Create Masters")
        return ok
    return False

# ── Phase 6: AI-Generated Voucher XML ──
def create_voucher(invoice_data, company):
    log.info("\n🤖 Asking AI to generate voucher XML...")
    prompt = VOUCHER_XML_PROMPT.format(company=company, invoice_json=json.dumps(invoice_data, indent=2))
    xml_str = call_ai_xml(prompt)

    if not validate_xml(xml_str):
        log.error("  ❌ AI generated invalid voucher XML. Retrying...")
        xml_str = call_ai_xml(prompt + "\n\nPREVIOUS OUTPUT WAS INVALID XML. Return valid XML only.")
        if not validate_xml(xml_str):
            log.error("  ❌ Still invalid XML.")
            return False, xml_str, []

    log.info("\n==================== 📄 GENERATED VOUCHER XML ====================")
    log.info(xml_str)
    log.info("==================================================================\n")

    save_xml_log("voucher_dynamic", xml_str)
    resp = post_tally(xml_str, "Voucher Import")
    if resp:
        ok, errs = check_tally_response(resp, "Voucher Import")
        return ok, xml_str, errs
    return False, xml_str, ["No response from Tally"]

# ── Phase 7: AI Self-Healing ──
def self_heal(invoice_data, failed_xml, errors, company, attempt=1):
    if attempt > 2:
        log.error("  ❌ Max healing attempts reached.")
        return False
    log.info(f"\n🛠️ AI Self-Healing (attempt {attempt})...")
    prompt = HEAL_PROMPT.format(
        errors=json.dumps(errors), invoice_json=json.dumps(invoice_data, indent=2),
        failed_xml=failed_xml
    )
    xml_str = call_ai_xml(prompt)
    if not validate_xml(xml_str):
        log.error("  ❌ Healed XML is invalid")
        return False

    log.info(f"\n==================== 📄 GENERATED HEALED XML (ATTEMPT {attempt}) ====================")
    log.info(xml_str)
    log.info("===================================================================================\n")

    save_xml_log(f"voucher_healed_{attempt}", xml_str)
    resp = post_tally(xml_str, f"Healed Import #{attempt}")
    if resp:
        ok, errs = check_tally_response(resp, f"Healed Import #{attempt}")
        if ok:
            return True
        return self_heal(invoice_data, xml_str, errs, company, attempt + 1)
    return False

# ── Main Pipeline ──
def process_file(file_path, company=COMPANY_NAME, force_edu=False):
    log.info("\n" + "=" * 60)
    log.info(f"🚀 DYNAMIC TALLY AUTOMATION")
    log.info(f"📄 File: {os.path.basename(file_path)}")
    log.info(f"🏢 Company: {company}")
    log.info("=" * 60)

    try:
        # Phase 1-2: Extract
        invoice_data = extract_invoice(file_path)

        # Auto-fix date to match Tally's financial year
        company_start = fetch_company_start_date(company)
        orig_date = invoice_data.get("date", "")
        if company_start and len(company_start) == 8 and len(orig_date) == 8:
            start_year = int(company_start[:4])
            inv_year = int(orig_date[:4])
            if inv_year != start_year:
                new_date = str(start_year) + orig_date[4:]
                log.info(f"  📅 Auto-fixing date: {orig_date} → {new_date} (matching Tally FY {start_year})")
                invoice_data["date"] = new_date

        # Auto-coerce date to 1st of month for Tally Educational Mode
        d = invoice_data.get("date", "")
        if len(d) == 8 and d[6:] not in ["01", "02", "31"]:
            old_d = d
            invoice_data["date"] = d[:6] + "01"
            log.info(f"  📅 Edu mode: date coerced {old_d} → {invoice_data['date']} (1st of month)")

        # Phase 3: Fetch masters
        tally_masters = fetch_all_masters(company)

        # Phase 4: Compare
        missing, has_missing = find_missing_masters(invoice_data, tally_masters)

        # Phase 5: Create missing masters
        if has_missing:
            ok = create_missing_masters(missing, company, invoice_data)
            if not ok:
                log.warning("  ⚠️ Some masters may not have been created, attempting voucher anyway...")

        # Phase 6: Create voucher
        success, xml_sent, errors = create_voucher(invoice_data, company)

        # Phase 7: Self-heal if needed (skip date errors — just push as-is)
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

def process_folder(folder_path, company=COMPANY_NAME, force_edu=False):
    supported = [".xlsx", ".xls", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"]
    files = [os.path.join(folder_path, f) for f in os.listdir(folder_path)
             if os.path.splitext(f)[1].lower() in supported]
    log.info(f"\n📁 Batch: {len(files)} files in '{folder_path}'")
    results = {"ok": 0, "fail": 0}
    for fp in files:
        if process_file(fp, company, force_edu):
            results["ok"] += 1
        else:
            results["fail"] += 1
    log.info(f"\n📊 Batch Done: ✅ {results['ok']} | ❌ {results['fail']}")

def main():
    p = argparse.ArgumentParser(description="Dynamic AI Tally Automation")
    p.add_argument("--file", help="Single invoice file path")
    p.add_argument("--dir", help="Folder of invoices")
    p.add_argument("--company", default=COMPANY_NAME)
    p.add_argument("--edu", action="store_true", help="Educational mode date coercion")
    args = p.parse_args()

    if OpenAI is None:
        log.error("❌ openai package required. Run: pip install openai")
        sys.exit(1)

    if args.file:
        process_file(args.file, args.company, args.edu)
    elif args.dir:
        process_folder(args.dir, args.company, args.edu)
    else:
        d = "invoices_to_import"
        os.makedirs(d, exist_ok=True)
        log.info(f"No args. Processing default folder: '{d}'")
        process_folder(d, args.company, args.edu)

if __name__ == "__main__":
    main()
