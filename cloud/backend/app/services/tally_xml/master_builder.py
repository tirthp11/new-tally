"""Generate master-creation XML with MASTER_XML_PROMPT (prompt kept as-is)."""
import json
import logging

from app.services.extraction.extractor import call_ai_text
from app.services.extraction.prompts import MASTER_XML_PROMPT
from app.services.tally_xml.validate import clean_ai_xml, filter_masters_to_allowed, validate_xml

log = logging.getLogger(__name__)


def build_masters_xml(missing: dict, company: str, invoice_data: dict) -> str:
    """Return validated master XML, or raise ValueError after one retry."""
    prompt = MASTER_XML_PROMPT.format(
        company=company,
        missing_json=json.dumps(missing, indent=2),
        invoice_json=json.dumps(invoice_data, indent=2),
    )
    xml_str = clean_ai_xml(call_ai_text(prompt))
    if not validate_xml(xml_str):
        log.warning("AI produced invalid master XML; retrying once")
        xml_str = clean_ai_xml(
            call_ai_text(prompt + "\n\nPREVIOUS OUTPUT WAS INVALID XML. Ensure valid XML.")
        )
        if not validate_xml(xml_str):
            raise ValueError("AI could not produce valid master-creation XML.")
    # The AI sometimes adds ledgers the user did not pick (from invoice_json),
    # which Tally would ALTER on existing masters and reject. Keep only the
    # masters explicitly listed in `missing`.
    return filter_masters_to_allowed(xml_str, missing)
