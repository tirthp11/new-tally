"""Parse Tally's raw import response, ported from check_tally_response."""
import logging
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)


def check_tally_response(xml_text: str, label: str = "Import") -> tuple[bool, list[str], dict]:
    """Return (ok, errors, info). info may carry created/altered counts and ids."""
    info: dict = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.error("XML parse error [%s]: %s", label, e)
        return False, [str(e)], info

    errs = [e.text.strip() for e in root.findall(".//LINEERROR") if e.text]
    errs += [e.text.strip() for e in root.findall(".//ERROR") if e.text]
    if errs:
        log.warning("Tally errors [%s]: %s", label, errs)
        return False, errs, info

    created = int(root.findtext(".//CREATED") or "0")
    altered = int(root.findtext(".//ALTERED") or "0")
    exceptions = int(root.findtext(".//EXCEPTIONS") or "0")
    info["created"] = created
    info["altered"] = altered
    last_vch_id = root.findtext(".//LASTVCHID")
    if last_vch_id:
        info["last_vch_id"] = last_vch_id.strip()

    if created > 0 or altered > 0:
        log.info("[%s]: Created=%d Altered=%d", label, created, altered)
        return True, [], info
    if exceptions > 0:
        return False, [
            f"Tally EXCEPTIONS={exceptions}. Likely cause: party amount doesn't match "
            "sum of items+GST. Ensure debits=credits."
        ], info
    return False, ["Tally returned Created=0, Altered=0. Data may be invalid or duplicated."], info
