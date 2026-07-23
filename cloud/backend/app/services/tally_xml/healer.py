"""Self-heal: regenerate corrected XML from Tally's errors (HEAL_PROMPT as-is)."""
import json
import logging

from app.services.extraction.extractor import call_ai_text
from app.services.extraction.prompts import HEAL_PROMPT
from app.services.tally_xml.validate import clean_ai_xml, validate_xml

log = logging.getLogger(__name__)

MAX_HEAL_ATTEMPTS = 2


def build_healed_xml(invoice_data: dict, failed_xml: str, errors: list[str]) -> str | None:
    """Return corrected XML, or None if the AI output is invalid."""
    prompt = HEAL_PROMPT.format(
        errors=json.dumps(errors),
        invoice_json=json.dumps(invoice_data, indent=2),
        failed_xml=failed_xml,
    )
    xml_str = clean_ai_xml(call_ai_text(prompt))
    if not validate_xml(xml_str):
        log.error("Healed XML is invalid")
        return None
    return xml_str


def split_date_errors(errors: list[str]) -> tuple[list[str], list[str]]:
    """Separate pure date/period warnings from real errors (same rule as scripts)."""
    date_errors = [e for e in errors if "date" in e.lower() or "period" in e.lower()]
    non_date_errors = [e for e in errors if e not in date_errors]
    return date_errors, non_date_errors
