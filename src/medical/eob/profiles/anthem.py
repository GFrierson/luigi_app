"""
Anthem (Blue Cross Blue Shield of Georgia) EOB issuer profile.

Defines ``ANTHEM_PROFILE`` — the only export — wiring Anthem-specific anchor
signatures, claim-table column geometry, and block field parsers into the
generic ``IssuerProfile`` engine. All Anthem specifics live here; the engine in
``profiles/__init__.py`` stays issuer-agnostic.

Column x-centers are measured from real Anthem EOBs at 300 DPI.  Geometric
predicates replace phrase-based segmentation anchors/terminators that were
unreliable against real multi-line OCR headers.

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
from src.medical.eob.types import Word

logger = logging.getLogger(__name__)


# Claim-table column x-centers in normalized pixels at 300 DPI. Measured from
# real Anthem EOBs (check + denial variants) — identical across subtypes.
_CLAIM_TABLE_COLUMNS: dict[str, int] = {
    "service_date":    129,
    "service":         340,
    "reason_code":     678,
    "doctor_charges": 1045,
    "discounts":      1390,
    "allowed":        1660,
    "anthem_paid":    1875,
    "copay":          2179,
    "deductible":     2413,
    "coinsurance":    2627,
    "not_covered":    2832,
    "your_total":     3140,  # visually separated magenta column, far right
}

_CLAIM_NUMBER_RE = re.compile(r"claim\s*(?:number|#)?\s*[:#]?\s*([A-Za-z0-9-]+)", re.I)
_RECEIVED_RE = re.compile(
    r"received\s*[:#]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})", re.I
)
_PROVIDER_RE = re.compile(r"(?:provider|doctor|rendered by)\s*[:#]?\s*([^$\n]+?)(?:\s{2,}|$)", re.I)

# Real Anthem text for patient cost-share amounts.
_YOU_PAY_RE = re.compile(
    r"you\s+pay[:\s]*\$?\s*([0-9][0-9,]*\.?[0-9]{0,2})",
    re.I,
)
# Check / ACH EOBs say "Amount deposited to your account: $157.50"
_DEPOSITED_RE = re.compile(
    r"amount\s+deposited.*?\$?\s*([0-9][0-9,]*\.?[0-9]{0,2})",
    re.I,
)


# --- Geometric predicates for claim-table segmentation -----------------------

_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}")
# Data-row service dates appear at x0 ~65; header/label dates are at x0 ~1650+.
_ROW_DATE_MAX_X0 = 260


def _is_data_row_start(words: list[Word]) -> bool:
    """True if ``words`` begins with a date token at low x0.

    Accepts data-row service dates (x0 < _ROW_DATE_MAX_X0) and rejects
    header pseudo-dates like "Received: 11/11/25" (x0 ~1650) and "Issue
    date:" labels. ``words`` should be sorted left-to-right so ``words[0]``
    is the leftmost token.
    """
    return (
        bool(words)
        and bool(_DATE_RE.match(words[0].text))
        and words[0].x0 < _ROW_DATE_MAX_X0
    )


def _is_totals(words: list[Word]) -> bool:
    """True if the leftmost word in ``words`` begins with 'totals'.

    Checks only ``words[0]`` (consistent with the start-anchored semantics of
    ``_match_phrase``) so the terminator fires precisely when a 'Totals' token
    leads the sliding window, not when it appears up to 4 positions ahead.
    """
    return bool(words) and words[0].text.lower().startswith("totals")


def _block_text(block: Block) -> str:
    """Concatenate a block's words in reading order into a single string."""
    ordered = sorted(block.words, key=lambda w: (w.page, w.y0, w.x0))
    return " ".join(w.text for w in ordered)


# Constants for positional patient extraction.
_PATIENT_ABOVE_CLAIM_X_MAX = 700   # px; patient name is left of this
_PATIENT_ABOVE_CLAIM_Y_BAND = 20   # px; y-band directly above "Claim Number:"
_PATIENT_ABOVE_MAX_TOKENS = 3      # maximum name tokens to collect


def _extract_patient_above_claim(words: list[Word]) -> str:
    """
    Return patient name from the y-band directly above the 'Claim Number:' token.

    Finds the first word containing 'claim' immediately followed by a word
    containing 'number', then collects alpha-only tokens above that y0 with
    x0 < _PATIENT_ABOVE_CLAIM_X_MAX.  Returns '' on any failure.
    """
    try:
        ordered = sorted(words, key=lambda w: (w.page, w.y0, w.x0))
        claim_y0 = None
        claim_page = None
        for i, w in enumerate(ordered):
            if "claim" in w.text.lower() and i + 1 < len(ordered):
                if "number" in ordered[i + 1].text.lower():
                    claim_y0 = w.y0
                    claim_page = w.page
                    break
        if claim_y0 is None:
            return ""
        above = [
            w for w in ordered
            if w.page == claim_page
            and 0 < claim_y0 - w.y0 <= _PATIENT_ABOVE_CLAIM_Y_BAND
            and w.x0 < _PATIENT_ABOVE_CLAIM_X_MAX
            and w.text.isalpha()
        ]
        if not above:
            return ""
        return " ".join(w.text for w in above[:_PATIENT_ABOVE_MAX_TOKENS])
    except Exception:
        logger.error("_extract_patient_above_claim: unexpected failure", exc_info=True)
        return ""


def _extract_claim_banner(block: Block, profile: IssuerProfile) -> dict:
    """
    Parse claim-level banner fields from a claim_banner block.

    Returns a dict with claim_number, received_date, provider, patient,
    patient_owes, in_network (and amount_deposited for check EOBs).
    Returns ``{}`` on failure.
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

        # Positional patient extraction: alpha tokens directly above "Claim Number:".
        patient = _extract_patient_above_claim(block.words)
        if patient:
            data["patient"] = patient

        # Patient cost-share: "You pay $67.50." (summary) or deposited (check/ACH).
        m = _DEPOSITED_RE.search(text)
        if m:
            data["patient_owes"] = "0.00"
            data["amount_deposited"] = m.group(1).strip()
        else:
            m = _YOU_PAY_RE.search(text)
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


def _extract_claim_table(
    block: Block, profile: IssuerProfile
) -> tuple[list[dict], str]:
    """Parse a claim_table block; return (rows, parsing_method)."""
    try:
        spec = profile.column_specs.get("claim_table")
        if spec is None:
            return [], "none"
        result = parse_table(block, spec)
        return result.rows, result.parsing_method
    except Exception:
        logger.error("_extract_claim_table: unexpected failure", exc_info=True)
        return [], "none"


# Label tokens excluded from subscriber name extraction.
_HEADER_LABEL_TOKENS = {
    "member", "id", "group", "plan", "type", "name", "holder",
    "account", "number", ":", "#",
}
# Maximum x0 for tokens in the account-holder name row.
_HOLDER_X_MAX = 1000
# Row-height tolerance for grouping header words into y-rows.
_HEADER_ROW_BAND = 14


def _extract_header(block: Block, profile: IssuerProfile) -> dict:
    """
    Extract the subscriber name from a header block.

    Strategy 1 — account-holder table: collects name tokens on the row
    directly below the first word containing 'holder', with x0 < _HOLDER_X_MAX,
    excluding common label words and digit-containing tokens.

    Strategy 2 — USPS mailing block fallback: collects alpha tokens bracketed
    by lone 'S' sentinel characters (USPS barcode markers, e.g.
    'S JAMES G FRIERSON S').

    Returns ``{"subscriber": name_or_empty}``.
    """
    try:
        ordered = sorted(block.words, key=lambda w: (w.page, w.y0, w.x0))

        # Strategy 1: row below the "holder" anchor word.
        for word in ordered:
            if "holder" in word.text.lower():
                holder_y0 = word.y0
                holder_page = word.page
                # Find words strictly below on the same page.
                below = [
                    w for w in ordered
                    if w.page == holder_page and w.y0 > holder_y0
                ]
                if not below:
                    continue
                # First y-row immediately below the holder word.
                next_y0 = min(w.y0 for w in below)
                row_words = [
                    w for w in below
                    if abs(w.y0 - next_y0) <= _HEADER_ROW_BAND
                    and w.x0 < _HOLDER_X_MAX
                    and w.text.lower().strip(":#") not in _HEADER_LABEL_TOKENS
                    and not any(ch.isdigit() for ch in w.text)
                ]
                if row_words:
                    name = " ".join(
                        w.text for w in sorted(row_words, key=lambda w: w.x0)
                    )
                    return {"subscriber": name}

        # Strategy 2: USPS mailing block — alpha tokens between lone 'S' sentinels.
        sentinel_indices = [i for i, w in enumerate(ordered) if w.text == "S"]
        if len(sentinel_indices) >= 2:
            start_idx = sentinel_indices[0]
            end_idx = sentinel_indices[-1]
            name_words = [
                ordered[i]
                for i in range(start_idx + 1, end_idx)
                if ordered[i].text.isalpha()
                and ordered[i].text.lower() not in _HEADER_LABEL_TOKENS
            ]
            if name_words:
                return {"subscriber": " ".join(w.text for w in name_words)}

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
            # "received" removed: it fires on "Received: 11/11/25" date labels
            # (x0 ~1650) inside check EOBs, prematurely closing open claim_table
            # blocks via the kind-switch logic. "claim number" and "claim detail"
            # are the only reliable banner anchors.
            anchor_phrases=["claim number", "claim detail"],
            terminator_phrases=["services provided"],
            terminator_predicate=_is_data_row_start,
        ),
        Signature(
            kind="claim_table",
            anchor_phrases=[],
            anchor_predicate=_is_data_row_start,
            terminator_phrases=["totals"],
            terminator_predicate=_is_totals,
        ),
    ],
    column_specs={
        "claim_table": ColumnSpec(
            columns=_CLAIM_TABLE_COLUMNS,
            row_terminator=["totals"],
        ),
    },
    extractors={
        "claim_banner": _extract_claim_banner,
        "claim_table": _extract_claim_table,
        "header": _extract_header,
        "doc_banner": _extract_doc_banner,
    },
)
