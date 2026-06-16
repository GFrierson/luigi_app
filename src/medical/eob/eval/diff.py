"""
Per-field diff of an extracted ``EOBDocument`` against a labeled expectation.

``diff_eob`` is a pure function — no DB, no I/O. It enumerates every comparable
field (top-level, per-claim, per-line-item), classifies each into one of four
outcomes (``match | miss | mismatch | ungrounded``), and returns one dict per
field. The harness tags each dict with ``run_id``/``fixture`` and persists it.

Field paths use the same dotted/indexed convention as the grounding report so
the ``ungrounded`` list (parsed from ``validation.issues``) lines up:
``issuer``, ``subtype``, ``subscriber``, ``claims[i].patient``,
``claims[i].line_items[j].service``, etc.

Normalization never mutates the inputs; both sides are normalized to a comparison
copy before comparing (``None`` -> ``""``, strip ``$``/``,``, lowercased).
"""

import logging
from typing import Optional

from src.medical.eob.types import Claim, EOBDocument, LineItem

logger = logging.getLogger(__name__)


# block_type tags per the roadmap schema.
_DOC_BANNER = "doc_banner"
_HEADER = "header"
_CLAIM_BANNER = "claim_banner"
_CLAIM_TABLE = "claim_table"

# Top-level EOBDocument fields -> their block_type.
_TOP_LEVEL_FIELDS: tuple[tuple[str, str], ...] = (
    ("issuer", _DOC_BANNER),
    ("subtype", _DOC_BANNER),
    ("subscriber", _HEADER),
)

# Per-claim scalar fields (everything but line_items).
_CLAIM_FIELDS: tuple[str, ...] = (
    "patient",
    "claim_number",
    "received_date",
    "provider",
    "in_network",
    "patient_owes",
)

# Per-line-item fields, derived from the LineItem dataclass field order.
_LINE_ITEM_FIELDS: tuple[str, ...] = tuple(LineItem.__annotations__.keys())


def _normalize(value: object) -> str:
    """Coerce a field value to its comparison form (never mutates the source)."""
    if value is None:
        return ""
    return str(value).strip().lower().replace("$", "").replace(",", "")


def _classify(
    field_path: str,
    expected_value: object,
    actual_value: object,
    ungrounded: list[str],
) -> str:
    """Return the outcome for a single field, in priority order."""
    if field_path in ungrounded:
        return "ungrounded"
    exp = _normalize(expected_value)
    act = _normalize(actual_value)
    if exp and not act:
        return "miss"
    if exp and act and exp != act:
        return "mismatch"
    return "match"


def _row(
    insurer: str,
    kind: str,
    subtype: str,
    block_type: str,
    field_path: str,
    extractor: str,
    expected_value: object,
    actual_value: object,
    confidence: float,
    outcome: str,
    parsing_method: str = "",
) -> dict:
    """Build one eval_results row dict (sans run_id/fixture/ts)."""
    return {
        "insurer": insurer,
        "kind": kind,
        "subtype": subtype,
        "block_type": block_type,
        "field": field_path,
        "extractor": extractor,
        "expected": "" if expected_value is None else str(expected_value),
        "actual": "" if actual_value is None else str(actual_value),
        "outcome": outcome,
        "confidence": confidence,
        "parsing_method": parsing_method,
    }


def diff_eob(
    fixture: str,
    insurer: str,
    kind: str,
    expected: EOBDocument,
    actual: EOBDocument,
    extractor: str,
    confidence: float,
    ungrounded: list[str],
) -> list[dict]:
    """
    Diff ``actual`` against ``expected`` field-by-field.

    Returns one row dict per field. Claim- and line-item-count mismatches are
    handled without IndexError: fields present only in ``expected`` emit
    ``"miss"`` rows (their actual value is empty).

    Never raises — on unexpected failure returns whatever rows were built so far
    plus a logged error.
    """
    rows: list[dict] = []
    subtype = expected.subtype
    try:
        # Top-level fields.
        for field_name, block_type in _TOP_LEVEL_FIELDS:
            exp_val = getattr(expected, field_name, None)
            act_val = getattr(actual, field_name, None)
            outcome = _classify(field_name, exp_val, act_val, ungrounded)
            rows.append(
                _row(
                    insurer, kind, subtype, block_type, field_name,
                    extractor, exp_val, act_val, confidence, outcome,
                )
            )

        # Per-claim fields. Iterate over the expected claims so a shorter actual
        # list yields "miss" rows rather than dropping fields.
        for i, exp_claim in enumerate(expected.claims):
            act_claim: Optional[Claim] = (
                actual.claims[i] if i < len(actual.claims) else None
            )
            claim_parsing_method = act_claim.parsing_method if act_claim else ""
            rows.extend(
                _diff_claim(
                    i, exp_claim, act_claim, insurer, kind, subtype,
                    extractor, confidence, ungrounded, claim_parsing_method,
                )
            )
    except Exception:
        logger.error(
            f"diff_eob: unexpected failure on fixture={fixture!r}",
            exc_info=True,
        )
    return rows


def _diff_claim(
    index: int,
    exp_claim: Claim,
    act_claim: Optional[Claim],
    insurer: str,
    kind: str,
    subtype: str,
    extractor: str,
    confidence: float,
    ungrounded: list[str],
    parsing_method: str = "",
) -> list[dict]:
    """Diff one expected claim (and its line items) against an actual claim."""
    rows: list[dict] = []

    for field_name in _CLAIM_FIELDS:
        path = f"claims[{index}].{field_name}"
        exp_val = getattr(exp_claim, field_name, None)
        act_val = getattr(act_claim, field_name, None) if act_claim else None
        outcome = _classify(path, exp_val, act_val, ungrounded)
        rows.append(
            _row(
                insurer, kind, subtype, _CLAIM_BANNER, path,
                extractor, exp_val, act_val, confidence, outcome,
                parsing_method,
            )
        )

    exp_items = exp_claim.line_items
    act_items = act_claim.line_items if act_claim else []
    for j, exp_item in enumerate(exp_items):
        act_item: Optional[LineItem] = (
            act_items[j] if j < len(act_items) else None
        )
        for field_name in _LINE_ITEM_FIELDS:
            path = f"claims[{index}].line_items[{j}].{field_name}"
            exp_val = getattr(exp_item, field_name, None)
            act_val = getattr(act_item, field_name, None) if act_item else None
            outcome = _classify(path, exp_val, act_val, ungrounded)
            rows.append(
                _row(
                    insurer, kind, subtype, _CLAIM_TABLE, path,
                    extractor, exp_val, act_val, confidence, outcome,
                    parsing_method,
                )
            )

    return rows
