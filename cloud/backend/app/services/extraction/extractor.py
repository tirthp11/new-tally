"""AI extraction on in-memory file bytes.

Refactored from the reference script. Two deliberate differences:
  1. Everything operates on bytes - no file paths, nothing written to disk.
  2. The AI result is returned EXACTLY as parsed. The eager normalization the
     old script applied (total recalculation, date coercion) is NOT done here;
     any adjustment happens at push time and is shown to the user first.
"""
import json
import logging

from openai import OpenAI

from app.core.config import get_settings
from app.services.extraction import file_reader
from app.services.extraction.prompts import EXTRACT_PROMPT

log = logging.getLogger(__name__)


def _client() -> OpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured on the server.")
    return OpenAI(api_key=settings.openai_api_key)


def call_ai_vision(images_b64: list[str], prompt: str) -> str:
    settings = get_settings()
    content = [{"type": "text", "text": prompt}]
    for img in images_b64:
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}
        )
    return (
        _client()
        .chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        .choices[0]
        .message.content
    )


def call_ai_text(prompt: str) -> str:
    """Plain-text completion (used for XML generation by the tally_xml services)."""
    settings = get_settings()
    return (
        _client()
        .chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        .choices[0]
        .message.content
    )


def _is_roundoff(name: str) -> bool:
    return "roundoff" in "".join(c for c in (name or "").lower() if c.isalpha())


def normalize_roundoff(invoice: dict) -> dict:
    """Keep Round Off consistent between the top-level "Round off" field and
    additional_charges. The AI often writes a non-zero round off to the top-level
    field only, which leaves it out of additional_charges - so the voucher/UI
    (which read additional_charges) miss it and the Round Off ledger dropdown
    never renders. Sync them: any non-zero value belongs in additional_charges
    with its sign; an exactly-zero value belongs in neither."""
    charges = invoice.get("additional_charges") or []
    existing = next((c for c in charges if _is_roundoff((c or {}).get("ledger_name"))), None)

    def as_float(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    amount = as_float(existing.get("amount")) if existing else as_float(invoice.get("Round off"))
    amount = round(amount, 2)

    # Drop every existing Round Off charge; re-add a single correct one if non-zero.
    charges = [c for c in charges if not _is_roundoff((c or {}).get("ledger_name"))]
    if amount != 0.0:
        charges.append({"ledger_name": "Round Off", "amount": amount})
        invoice["Round off"] = amount
    else:
        invoice["Round off"] = 0.0
    invoice["additional_charges"] = charges
    return invoice


def extract_invoice(data: bytes, filename: str) -> dict:
    """Run AI extraction on in-memory bytes and return the parsed JSON as-is."""
    kind = file_reader.detect_kind(filename)

    if kind == "pdf":
        # Every PDF goes through vision. pypdf's text layer linearizes a 2-D page
        # into 1-D text and scrambles spatially-separated columns differently for
        # each vendor template (glued GST numbers, reordered item names). Rendering
        # each page to an image lets the model read columns by their real position,
        # which is layout-independent - the same path screenshots already use.
        log.info("Extracting from PDF via vision: %s", filename)
        images = file_reader.pdf_to_images_b64(data)
        if not images:
            raise ValueError("Could not read any pages from this PDF.")
        result = call_ai_vision(images, EXTRACT_PROMPT)
    else:  # image
        log.info("Extracting from image via vision: %s", filename)
        images = file_reader.image_to_b64(data)
        result = call_ai_vision(images, EXTRACT_PROMPT)

    # Stored exactly as returned, except Round Off is synced between the
    # top-level field and additional_charges (the AI often fills only one).
    invoice = normalize_roundoff(json.loads(result))
    log.info(
        "AI extraction result for %s:\n%s",
        filename, json.dumps(invoice, indent=2, ensure_ascii=False),
    )
    return invoice
