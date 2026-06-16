"""
Per-field diff of an extracted ``EOBDocument`` against a labeled expectation.

``diff_eob`` is a pure function — no DB, no I/O. It enumerates every comparable
field (top-level, per-claim, per-line-item), classifies each into one of five
outcomes (``match | miss | mismatch | ungrounded | spurious``), and returns one
dict per field. The harness tags each dict with ``run_id``/``fixture`` and
persists it.

Claims and line items are matched by a key-gated greedy alignment (``_align``)
rather than by list position, so duplicate line items (the same procedure billed
several times) pair 1:1 and over-extraction surfaces as ``spurious`` rows instead
of corrupting positional comparisons. The key that *qualifies* a pairing is kept
separate from the fields that *score* it: coincidental equality of constantly-zero
monetary fields must never qualify a match.

Field paths use the same dotted/indexed convention as the grounding report so
the ``ungrounded`` list (parsed from ``validation.issues``) lines up:
``issuer``, ``subtype``, ``subscriber``, ``claims[i].patient``,
``claims[i].line_items[j].service``, etc. Matched and miss rows use the expected
index; spurious (actual-only) rows carry a ``spurious:{k}`` marker on the
unmatched segment so every emitted row has a globally unique field path.

Normalization never mutates the inputs; both sides are normalized to a comparison
copy before comparing (``None`` -> ``""``, strip ``$``/``,``, lowercased).
"""

import logging
from typing import Callable, Optional

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

# Identity fields used to *qualify* a pairing (the match key). Kept separate from
# the monetary fields used to *score* it: copay/deductible/discounts/coinsurance are
# 0.00 constantly, so monetary-only overlap must never qualify a match.
_LINE_IDENTITY_FIELDS: tuple[str, ...] = ("service_date", "service", "reason_code")
_CLAIM_IDENTITY_FIELDS: tuple[str, ...] = ("claim_number", "patient")
# A line-item pair qualifies only when at least this many identity fields match.
_LINE_IDENTITY_THRESHOLD = 2
# Identity agreement must dominate the claim score's line-item-count tie-break.
_IDENTITY_WEIGHT = 1000


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


def _line_item_candidate(exp: LineItem, act: LineItem) -> bool:
    """A line-item pair qualifies only when >=2 of the 3 identity fields match.

    Monetary fields never qualify a pair (they only break ties in ``_line_item_score``)
    because constantly-zero monetary values would otherwise pair unrelated rows.
    """
    matches = sum(
        1
        for f in _LINE_IDENTITY_FIELDS
        if _normalize(getattr(exp, f, None)) == _normalize(getattr(act, f, None))
    )
    return matches >= _LINE_IDENTITY_THRESHOLD


def _line_item_score(exp: LineItem, act: LineItem) -> int:
    """Rank qualified candidates by total field agreement (identity + monetary).

    Used only to choose, among already-qualified pairs, the closest counterpart for
    near-duplicate line items; it never qualifies a pairing on its own.
    """
    return sum(
        1
        for f in _LINE_ITEM_FIELDS
        if _normalize(getattr(exp, f, None)) == _normalize(getattr(act, f, None))
    )


def _claim_candidate(exp: Claim, act: Claim) -> bool:
    """A claim pair qualifies only when ``claim_number`` is non-empty and matches.

    ``patient`` is constant within an EOB and must not qualify pairs — a garbled
    ``claim_number`` therefore yields miss + spurious for the whole claim.
    """
    exp_num = _normalize(getattr(exp, "claim_number", None))
    return bool(exp_num) and exp_num == _normalize(getattr(act, "claim_number", None))


def _claim_score(exp: Claim, act: Claim) -> int:
    """Rank qualified claim candidates: identity agreement, then line-item-count.

    ``line-item-count agreement`` is +1 when the counts are equal else 0; identity
    agreement is weighted to dominate so it never loses to the count tie-break.
    """
    identity = sum(
        1
        for f in _CLAIM_IDENTITY_FIELDS
        if _normalize(getattr(exp, f, None)) == _normalize(getattr(act, f, None))
    )
    count_agreement = 1 if len(exp.line_items) == len(act.line_items) else 0
    return identity * _IDENTITY_WEIGHT + count_agreement


def _align(
    expected: list,
    actual: list,
    candidate_fn: Callable[[object, object], bool],
    score_fn: Callable[[object, object], int],
) -> list[tuple[Optional[object], Optional[object], Optional[int], Optional[int]]]:
    """Greedy, key-gated 1:1 alignment of expected to actual items.

    Returns ``(exp, act, exp_idx, act_idx)`` tuples: matched pairs first in expected
    order (followed by expected-only ``miss`` tuples with ``act=None``), then
    actual-only ``spurious`` tuples (``exp=None``) in actual order.

    A pairing is considered only when ``candidate_fn`` qualifies it; ``score_fn``
    merely ranks qualified candidates. The walk is greedy (descending score), not an
    optimal assignment — acceptable because candidacy is key-gated, so suboptimality
    can only reshuffle monetary tie-breaks among items that already share the key.
    Each accept consumes one expected and one actual index, so K items sharing the
    full key still pair 1:1 and duplicate counts are preserved.
    """
    candidates: list[tuple[int, int, int]] = []
    for ei, exp_item in enumerate(expected):
        for ai, act_item in enumerate(actual):
            if candidate_fn(exp_item, act_item):
                candidates.append((score_fn(exp_item, act_item), ei, ai))
    # Highest score first; (exp_idx, act_idx) ties break deterministically.
    candidates.sort(key=lambda t: (-t[0], t[1], t[2]))

    matched: dict[int, int] = {}
    used_exp: set[int] = set()
    used_act: set[int] = set()
    for _, ei, ai in candidates:
        if ei in used_exp or ai in used_act:
            continue
        matched[ei] = ai
        used_exp.add(ei)
        used_act.add(ai)

    result: list[tuple[Optional[object], Optional[object], Optional[int], Optional[int]]] = []
    for ei, exp_item in enumerate(expected):
        if ei in matched:
            ai = matched[ei]
            result.append((exp_item, actual[ai], ei, ai))
        else:
            result.append((exp_item, None, ei, None))
    for ai, act_item in enumerate(actual):
        if ai not in used_act:
            result.append((None, act_item, None, ai))
    return result


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


def _spurious_line_item_rows(
    claim_index: int,
    act_item_index: int,
    act_item: LineItem,
    insurer: str,
    kind: str,
    subtype: str,
    extractor: str,
    confidence: float,
    parsing_method: str = "",
) -> list[dict]:
    """Emit one "spurious" row per field for an unmatched actual line item.

    Uses the ``spurious:{act_item_index}`` path marker so these rows never share a
    field path with any matched or missed row (which use the expected index ``j``).
    """
    rows: list[dict] = []
    for field_name in _LINE_ITEM_FIELDS:
        path = f"claims[{claim_index}].line_items[spurious:{act_item_index}].{field_name}"
        act_val = getattr(act_item, field_name, None)
        rows.append(
            _row(
                insurer, kind, subtype, _CLAIM_TABLE, path,
                extractor, None, act_val, confidence, "spurious",
                parsing_method,
            )
        )
    return rows


def _spurious_claim_rows(
    act_index: int,
    act_claim: Claim,
    insurer: str,
    kind: str,
    subtype: str,
    extractor: str,
    confidence: float,
) -> list[dict]:
    """Emit "spurious" rows for every field of an unmatched actual claim.

    Uses the ``spurious:{act_index}`` path marker at the claim level so these rows
    never collide with matched or missed expected-claim rows.
    """
    rows: list[dict] = []
    parsing_method = act_claim.parsing_method
    # Claim-level banner fields.
    for field_name in _CLAIM_FIELDS:
        path = f"claims[spurious:{act_index}].{field_name}"
        act_val = getattr(act_claim, field_name, None)
        rows.append(
            _row(
                insurer, kind, subtype, _CLAIM_BANNER, path,
                insurer, None, act_val, 0.0, "spurious",
                parsing_method,
            )
        )
    # Line items of the spurious claim.
    for j, act_item in enumerate(act_claim.line_items):
        for field_name in _LINE_ITEM_FIELDS:
            path = f"claims[spurious:{act_index}].line_items[{j}].{field_name}"
            act_val = getattr(act_item, field_name, None)
            rows.append(
                _row(
                    insurer, kind, subtype, _CLAIM_TABLE, path,
                    insurer, None, act_val, 0.0, "spurious",
                    parsing_method,
                )
            )
    return rows


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

        # Per-claim fields. Align claims by claim_number so an extra actual claim
        # surfaces as "spurious" rather than shifting positional comparisons; a
        # missing expected claim still yields "miss" rows.
        for exp_claim, act_claim, exp_idx, act_idx in _align(
            expected.claims, actual.claims, _claim_candidate, _claim_score
        ):
            if exp_claim is not None:
                claim_parsing_method = act_claim.parsing_method if act_claim else ""
                rows.extend(
                    _diff_claim(
                        exp_idx, exp_claim, act_claim, insurer, kind, subtype,
                        extractor, confidence, ungrounded, claim_parsing_method,
                    )
                )
            else:
                rows.extend(
                    _spurious_claim_rows(
                        act_idx, act_claim, insurer, kind, subtype,
                        extractor, confidence,
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

    # Align line items by identity key so duplicate rows pair 1:1 and extra
    # actual items surface as "spurious" rather than shifting positional indexes.
    exp_items = exp_claim.line_items
    act_items = act_claim.line_items if act_claim else []
    for exp_item, act_item, exp_j, act_j in _align(
        exp_items, act_items, _line_item_candidate, _line_item_score
    ):
        if exp_item is not None:
            # Matched or miss row — use the expected index so paths are stable.
            for field_name in _LINE_ITEM_FIELDS:
                path = f"claims[{index}].line_items[{exp_j}].{field_name}"
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
        else:
            # Spurious actual line item — use the distinct "spurious:{k}" marker.
            rows.extend(
                _spurious_line_item_rows(
                    index, act_j, act_item,
                    insurer, kind, subtype, extractor, confidence, parsing_method,
                )
            )

    return rows
