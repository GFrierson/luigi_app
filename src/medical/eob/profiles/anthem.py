"""
Anthem (Blue Cross Blue Shield of Georgia) EOB issuer profile.

Defines ``ANTHEM_PROFILE`` — the only export — wiring Anthem-specific anchor
signatures, claim-table column geometry, and block field parsers into the
generic ``IssuerProfile`` engine. All Anthem specifics live here; the engine in
``profiles/__init__.py`` stays issuer-agnostic.

The claim-table column x-centers are STARTING ESTIMATES at 300 DPI (an 8.5"
page is ~2550 px wide) and must be tuned against real EOBs via the eval loop in
``experiments/medical/anthm_eob/``.

All block extractors never raise — they return ``{}`` / ``[]`` on failure and
log with ``exc_info=True``.
"""

import logging
import re

from src.medical.eob.blocks import Block
from src.medical.eob.profiles import (
    ColumnSpec,
    IssuerProfile,
    Signature,
)
from src.medical.eob.tables import parse_table

logger = logging.getLogger(__name__)


# Claim-table column x-centers in normalized pixels at 300 DPI. Tune via eval.
_CLAIM_TABLE_COLUMNS: dict[str, int] = {
    "service_date": 130,
    "service": 350,
    "reason_code": 580,
    "doctor_charges": 780,
    "discounts": 960,
    "allowed": 1120,
    "anthem_paid": 1290,
    "copay": 1450,
    "deductible": 1600,
    "coinsurance": 1740,
    "not_covered": 1900,
    "your_total": 2380,  # visually separated magenta column, far right
}

_CLAIM_NUMBER_RE = re.compile(r"claim\s*(?:number|#)?\s*[:#]?\s*([A-Za-z0-9-]+)", re.I)
_RECEIVED_RE = re.compile(
    r"received\s*[:#]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})", re.I
)
_PROVIDER_RE = re.compile(r"(?:provider|doctor|rendered by)\s*[:#]?\s*([^$\n]+?)(?:\s{2,}|$)", re.I)
_PATIENT_RE = re.compile(r"(?:patient|for)\s*[:#]?\s*([^$\n]+?)(?:\s{2,}|$)", re.I)
_PATIENT_OWES_RE = re.compile(
    r"(?:patient\s+responsibility|you\s+owe|patient\s+owes)\s*[:#]?\s*(\$?[0-9][0-9,]*\.?[0-9]*)",
    re.I,
)


def _block_text(block: Block) -> str:
    """Concatenate a block's words in reading order into a single string."""
    ordered = sorted(block.words, key=lambda w: (w.page, w.y0, w.x0))
    return " ".join(w.text for w in ordered)


def _extract_claim_banner(block: Block, profile: IssuerProfile) -> dict:
    """
    Parse claim-level banner fields from a claim_banner block.

    Returns a dict with claim_number, received_date, provider, patient,
    patient_owes, in_network. Returns ``{}`` on failure.
    """
    try:
        text = _block_text(block)
        lowered = text.lower()

        data: dict = {}

        m = _CLAIM_NUMBER_RE.search(text)
        if m:
            data["claim_number"] = m.group(1).strip()

        m = _RECEIVED_RE.search(text)
        data["received_date"] = m.group(1).strip() if m else None

        m = _PROVIDER_RE.search(text)
        if m:
            data["provider"] = m.group(1).strip()

        m = _PATIENT_RE.search(text)
        if m:
            data["patient"] = m.group(1).strip()

        m = _PATIENT_OWES_RE.search(text)
        if m:
            data["patient_owes"] = m.group(1).strip()

        if "out-of-network" in lowered or "out of network" in lowered:
            data["in_network"] = False
        elif "in-network" in lowered or "in network" in lowered:
            data["in_network"] = True
        else:
            data["in_network"] = False

        return data
    except Exception:
        logger.error("_extract_claim_banner: unexpected failure", exc_info=True)
        return {}


def _extract_claim_table(block: Block, profile: IssuerProfile) -> list[dict]:
    """Parse a claim_table block into a list of line-item row dicts."""
    try:
        spec = profile.column_specs.get("claim_table")
        if spec is None:
            return []
        return parse_table(block, spec)
    except Exception:
        logger.error("_extract_claim_table: unexpected failure", exc_info=True)
        return []


def _extract_header(block: Block, profile: IssuerProfile) -> dict:
    """
    Extract the subscriber name from a header block.

    Takes the first non-label word(s) following the "subscriber" anchor.
    Returns ``{"subscriber": name_or_empty}``.
    """
    try:
        ordered = sorted(block.words, key=lambda w: (w.page, w.y0, w.x0))
        label_tokens = {"subscriber", "id", "member", "group", "number", ":", "#"}
        for idx, word in enumerate(ordered):
            if "subscriber" in word.text.lower():
                name_parts: list[str] = []
                for follow in ordered[idx + 1 :]:
                    token = follow.text.strip()
                    cleaned = token.lower().strip(":#")
                    if not cleaned or cleaned in label_tokens:
                        if name_parts:
                            break
                        continue
                    if any(ch.isdigit() for ch in token):
                        if name_parts:
                            break
                        continue
                    name_parts.append(token)
                    if len(name_parts) >= 3:
                        break
                if name_parts:
                    return {"subscriber": " ".join(name_parts)}
        return {"subscriber": ""}
    except Exception:
        logger.error("_extract_header: unexpected failure", exc_info=True)
        return {"subscriber": ""}


def _extract_doc_banner(block: Block, profile: IssuerProfile) -> dict:
    """
    Detect the EOB subtype from a doc_banner block's text.

    Returns ``{"subtype": subtype_value}``.
    """
    try:
        text = _block_text(block).lower()
        has_amount = bool(re.search(r"\$[0-9]", text))

        if "duplicate" in text:
            return {"subtype": "duplicate_notice"}
        if "payment" in text and (
            "check" in text or "deposited" in text or "direct deposit" in text
        ):
            return {"subtype": "payment_notice"}
        if (
            "we are unable" in text
            or "cannot pay" in text
            or ("not covered" in text and not has_amount)
        ):
            return {"subtype": "denial"}
        return {"subtype": "summary"}
    except Exception:
        logger.error("_extract_doc_banner: unexpected failure", exc_info=True)
        return {"subtype": "summary"}


ANTHEM_PROFILE = IssuerProfile(
    issuer="anthem",
    signatures=[
        Signature(
            kind="header",
            anchor_phrases=["subscriber id", "member id", "group number"],
            terminator_phrases=[],
        ),
        Signature(
            kind="doc_banner",
            anchor_phrases=[
                "explanation of benefits",
                "summary of benefits",
                "we are unable to pay",
            ],
            terminator_phrases=[],
        ),
        Signature(
            kind="claim_banner",
            anchor_phrases=["claim number", "received", "claim detail"],
            terminator_phrases=["service date", "services provided"],
        ),
        Signature(
            kind="claim_table",
            anchor_phrases=["service date", "services provided", "doctor charges"],
            terminator_phrases=["totals", "patient responsibility", "claim number"],
        ),
    ],
    column_specs={
        "claim_table": ColumnSpec(
            columns=_CLAIM_TABLE_COLUMNS,
            row_terminator=["totals", "patient responsibility"],
        ),
    },
    extractors={
        "claim_banner": _extract_claim_banner,
        "claim_table": _extract_claim_table,
        "header": _extract_header,
        "doc_banner": _extract_doc_banner,
    },
)
