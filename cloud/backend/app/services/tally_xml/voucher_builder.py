"""Generate purchase voucher XML with VOUCHER_XML_PROMPT (prompt kept as-is)."""
import json
import logging

from app.services.extraction.extractor import call_ai_text
from app.services.extraction.prompts import VOUCHER_XML_PROMPT
from app.services.amounts import display_amount
from app.services.tally_xml.validate import (
    clean_ai_xml,
    fix_party_amount,
    fix_roundoff,
    roundoff_amount,
    validate_xml,
)

log = logging.getLogger(__name__)


def build_voucher_xml(invoice_data: dict, company: str) -> str:
    """Return validated voucher XML, or raise ValueError after one retry."""
    prompt = VOUCHER_XML_PROMPT.format(
        company=company, invoice_json=json.dumps(invoice_data, indent=2)
    )
    xml_str = clean_ai_xml(call_ai_text(prompt))
    if not validate_xml(xml_str):
        log.warning("AI produced invalid voucher XML; retrying once")
        xml_str = clean_ai_xml(
            call_ai_text(prompt + "\n\nPREVIOUS OUTPUT WAS INVALID XML. Return valid XML only.")
        )
        if not validate_xml(xml_str):
            raise ValueError("AI could not produce valid voucher XML.")
    # The AI flips the Round Off sign / duplicates / misplaces it, unbalancing
    # the voucher. Rebuild that entry deterministically from the invoice amount.
    # The AI computes the party total itself and gets it wrong - it dropped a
    # P&F line and Tally rejected the voucher for debits != credits. Stamp the
    # grid's amount over whatever it produced.
    return fix_party_amount(
        fix_roundoff(xml_str, roundoff_amount(invoice_data)),
        display_amount(invoice_data),
    )
