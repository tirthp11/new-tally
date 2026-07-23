"""XML validation and sanitization, ported from the reference script."""
import re
import xml.etree.ElementTree as ET


def sanitize_xml(xml_str: str) -> str:
    """Fix common AI XML issues like unescaped & characters."""
    return re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", xml_str)


def strip_markdown_fences(text: str) -> str:
    if "```xml" in text:
        text = text.split("```xml")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return text.strip()


def validate_xml(xml_str: str) -> bool:
    """True if the string parses as XML (Tally control refs like &#4; allowed)."""
    try:
        sanitized = re.sub(r"&#\d+;", "", xml_str)
        ET.fromstring(sanitized)
        return True
    except ET.ParseError:
        return False


# The AI sometimes copies typographic characters (em dashes, smart quotes)
# from the prompt into the XML, mostly inside comments. Tally's parser cannot
# handle them and answers "Unknown Request, cannot be processed".
_TYPOGRAPHIC = {
    "—": "-",   # em dash
    "–": "-",   # en dash
    "‘": "'",   # left single quote
    "’": "'",   # right single quote
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    " ": " ",   # non-breaking space
    "•": "-",   # bullet
    "→": "->",  # right arrow
}


def strip_comments(xml_str: str) -> str:
    """Remove XML comments entirely - Tally does not need them and the AI
    tends to put prompt characters in them that break Tally's parser."""
    return re.sub(r"<!--.*?-->", "", xml_str, flags=re.S)


def normalize_typographic(xml_str: str) -> str:
    for bad, good in _TYPOGRAPHIC.items():
        xml_str = xml_str.replace(bad, good)
    return xml_str


def clean_ai_xml(raw: str) -> str:
    """Strip fences and comments, normalize characters, sanitize an AI XML response."""
    return sanitize_xml(normalize_typographic(strip_comments(strip_markdown_fences(raw))))


# The AI sometimes emits a <TALLYMESSAGE> for every ledger/stock/unit the invoice
# references, not just the ones the user chose to create - even though the prompt
# forbids it. Those extras arrive as ACTION="Create" on masters that already
# exist, so Tally ALTERs them and the push is rejected. This filter keeps only the
# blocks whose master name is in the caller's allowlist (the `missing` dict), so
# the XML can never create anything the user did not pick. Block-based (not
# ElementTree) because masters XML legitimately carries the XML-illegal control
# ref &#4; that ET.fromstring rejects.
_TALLYMESSAGE_RE = re.compile(r"<TALLYMESSAGE\b.*?</TALLYMESSAGE>", re.S | re.I)
_NAME_ATTR_RE = re.compile(r'<(?:LEDGER|STOCKITEM|UNIT)\b[^>]*\bNAME="([^"]*)"', re.I)
_NAME_EL_RE = re.compile(r"<NAME>(.*?)</NAME>", re.S | re.I)


def _block_master_name(block: str) -> str | None:
    """Master name of a <TALLYMESSAGE> block: the LEDGER/STOCKITEM/UNIT NAME
    attribute, falling back to the first <NAME> element. None if neither found."""
    m = _NAME_ATTR_RE.search(block)
    if not m:
        m = _NAME_EL_RE.search(block)
    if not m:
        return None
    return m.group(1).replace("&amp;", "&").strip()


def filter_masters_to_allowed(xml_str: str, missing: dict) -> str:
    """Drop every <TALLYMESSAGE> whose master name is not in `missing`. A block
    with no extractable name is kept (never silently drop a requested master)."""
    allowed = set()
    for key in ("ledgers", "stock_items", "units"):
        for entry in (missing or {}).get(key, []):
            name = (entry or {}).get("name")
            if name:
                allowed.add(name.strip().upper())
    if not allowed:
        return xml_str

    def keep(match: "re.Match[str]") -> str:
        name = _block_master_name(match.group(0))
        if name is None or name.upper() in allowed:
            return match.group(0)
        return ""

    return _TALLYMESSAGE_RE.sub(keep, xml_str)


# Round Off is AI-driven and unreliable: the model flips its sign (a -0.04
# deduction comes out as +0.04, unbalancing the voucher so Tally rejects it),
# sometimes duplicates the block, and sometimes places it before GST. We already
# know the exact amount and sign from the invoice, so rebuild the entry
# deterministically instead of trusting the AI. Block-based like the masters
# filter; voucher XML carries no &#4; control refs.
def _is_roundoff(name: str) -> bool:
    return "roundoff" in re.sub(r"[^a-z]", "", (name or "").lower())


def roundoff_amount(invoice_data: dict) -> float:
    """The signed Round Off value for this invoice: the additional_charges Round
    Off entry if present, else the top-level "Round off" field. 0.0 if absent."""
    for charge in (invoice_data or {}).get("additional_charges", []) or []:
        if _is_roundoff((charge or {}).get("ledger_name")):
            try:
                return float(charge.get("amount", 0) or 0)
            except (TypeError, ValueError):
                return 0.0
    try:
        return float((invoice_data or {}).get("Round off", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


_LEDGERENTRY_RE = re.compile(r"[ \t]*<LEDGERENTRIES\.LIST>.*?</LEDGERENTRIES\.LIST>\n?", re.S | re.I)
_LEDGERNAME_RE = re.compile(r"<LEDGERNAME>(.*?)</LEDGERNAME>", re.S | re.I)


def fix_roundoff(xml_str: str, amount: float) -> str:
    """Strip every Round Off LEDGERENTRIES.LIST the AI emitted and, if `amount`
    is non-zero, append exactly one correct block (right sign, after GST) just
    before </VOUCHER>. No-op if there is no </VOUCHER> to anchor to."""
    def drop_roundoff(match: "re.Match[str]") -> str:
        nm = _LEDGERNAME_RE.search(match.group(0))
        if nm and _is_roundoff(nm.group(1)):
            return ""
        return match.group(0)

    cleaned = _LEDGERENTRY_RE.sub(drop_roundoff, xml_str)

    if not amount or abs(amount) < 0.005 or "</VOUCHER>" not in cleaned:
        return cleaned

    # Negative reduces the payable (ISDEEMEDPOSITIVE Yes); positive increases it.
    deemed = "No"
    block = (
        "      <LEDGERENTRIES.LIST>\n"
        "       <LEDGERNAME>Round Off</LEDGERNAME>\n"
        f"       <ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>\n"
        f"       <AMOUNT>{amount:.2f}</AMOUNT>\n"
        "      </LEDGERENTRIES.LIST>\n"
    )
    return cleaned.replace("</VOUCHER>", block + "     </VOUCHER>", 1)


_PARTYLEDGERNAME_RE = re.compile(r"<PARTYLEDGERNAME>(.*?)</PARTYLEDGERNAME>", re.S | re.I)
_AMOUNT_RE = re.compile(r"<AMOUNT>.*?</AMOUNT>", re.S | re.I)


def fix_party_amount(xml_str: str, amount) -> str:
    """Overwrite the party ledger's AMOUNT with the voucher's own amount - the
    value shown in the grid. Same reason as fix_roundoff above: the AI computes
    this total itself and gets it wrong (it dropped a P&F line, so debits did
    not equal credits and Tally rejected the voucher). No-op if the party entry
    or the amount is missing."""
    if amount is None:
        return xml_str
    m = _PARTYLEDGERNAME_RE.search(xml_str)
    if not m:
        return xml_str
    party = m.group(1).strip()

    def stamp(match: "re.Match[str]") -> str:
        nm = _LEDGERNAME_RE.search(match.group(0))
        if not nm or nm.group(1).strip() != party:
            return match.group(0)
        return _AMOUNT_RE.sub(
            f"<AMOUNT>{-abs(float(amount)):.2f}</AMOUNT>", match.group(0), count=1)

    return _LEDGERENTRY_RE.sub(stamp, xml_str)


def balance_difference(xml_str: str) -> float | None:
    """Signed rupee gap between the voucher's debits and credits, read from the
    finished XML. 0.00 means it balances and Tally will accept it; anything else
    means Tally would reject it with EXCEPTIONS - and still log the broken copy
    in its exception list, forcing a cleanup in Tally on top of the web app. The
    caller uses this to refuse the push BEFORE sending, so Tally is never touched
    by a voucher that cannot post.

    Sums every LEDGERENTRIES.LIST amount (the party is negative, charges/GST/
    Round Off positive) plus every inventory ACCOUNTINGALLOCATIONS.LIST amount
    (the purchase postings). The stock-item-level AMOUNT is the same figure as
    its accounting allocation, so it is deliberately not counted. Returns None if
    the XML cannot be parsed (let the normal Tally response handle that case)."""
    try:
        root = ET.fromstring(re.sub(r"&#\d+;", "", xml_str))
    except ET.ParseError:
        return None

    total = 0.0
    for tag in ("LEDGERENTRIES.LIST", "ACCOUNTINGALLOCATIONS.LIST"):
        for entry in root.iter(tag):
            amt = entry.findtext("AMOUNT")
            if amt is None:
                continue
            try:
                total += float(amt)
            except ValueError:
                pass
    return round(total, 2)
