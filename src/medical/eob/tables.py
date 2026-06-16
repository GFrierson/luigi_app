"""
Coordinate-driven table parsing for EOB claim line-item grids.

``parse_table`` groups a ``Block``'s words into rows by their vertical position
and assigns each word to the nearest column by horizontal center, using the
``ColumnSpec`` column geometry (x-centers in normalized OCR-DPI pixel space).
Words beyond a row-terminator phrase are excluded.

On low parse quality, ``parse_table`` escalates to a pluggable ``SecondEngine``
(if supplied) and keeps whichever result scores higher. A ``NoOpSecondEngine``
is provided as a stub until a real second engine is benchmarked.

Never raises: any failure returns an empty ``TableParseResult`` and is logged
with ``exc_info=True``.
"""

import logging
from typing import Protocol

from src.medical.eob.blocks import Block
from src.medical.eob.profiles import ColumnSpec
from src.medical.eob.types import TableDiagnostic, TableParseResult, Word

logger = logging.getLogger(__name__)


# Words whose y0 differs by no more than this many pixels share a table row.
_ROW_Y_TOLERANCE = 8

# Score threshold below which a second engine is attempted.
ESCALATION_THRESHOLD = 0.6

# Patient cost-share columns whose sum should equal your_total per row.
_PATIENT_COLS = ("copay", "deductible", "coinsurance", "not_covered")

# Diagnostic score penalties.
_PENALTY_NO_YOUR_TOTAL = 0.3
_PENALTY_MISSING_COL = 0.2
_PENALTY_ARITHMETIC = 0.2
_MAX_MISSING_COL_PENALTY = 0.4


class SecondEngine(Protocol):
    """Pluggable interface for a local second-engine table re-parser."""

    def parse(self, block: Block, spec: ColumnSpec) -> list[dict[str, str]]: ...


class NoOpSecondEngine:
    """
    Stub second engine that always returns no rows.

    Used as a placeholder until PP-Structure or RapidOCR is benchmarked against
    the CAX11 RAM budget. Wire a real engine by implementing ``SecondEngine``.
    """

    def parse(self, block: Block, spec: ColumnSpec) -> list[dict[str, str]]:
        return []


def _nearest_column(word: Word, columns: dict[str, int]) -> str:
    """Return the column name whose x-center is closest to the word's x-center."""
    x_center = (word.x0 + word.x1) // 2
    return min(columns, key=lambda name: abs(x_center - columns[name]))


def _is_terminator(word: Word, terminators: list[str]) -> bool:
    """True if the word text matches any row-terminator phrase (case-insensitive)."""
    lowered = word.text.lower()
    return any(term in lowered for term in terminators)


def _try_parse_amount(s: str) -> float | None:
    """Parse a money string like '$1,234.56' into 1234.56; return None if unparseable."""
    cleaned = (s or "").replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _compute_diagnostic(
    rows: list[dict[str, str]], spec: ColumnSpec
) -> TableDiagnostic:
    """
    Score a parsed table for quality. Pure, never raises.

    Empty rows → worst-case diagnostic (score=0.0, escalate=True).

    Aggregation rules:
    - ``narrow_columns_resolved``: True only if your_total is non-empty in EVERY row.
    - ``columns_missing``: columns that are empty (or absent) in ALL rows.
    - ``arithmetic_ok``: False if ANY parseable row has copay+deductible+
      coinsurance+not_covered != your_total (within $0.01 tolerance).
      Rows where your_total is not parseable are skipped.
    """
    try:
        if not rows:
            return TableDiagnostic(
                score=0.0,
                columns_missing=list(spec.columns.keys()),
                arithmetic_ok=False,
                narrow_columns_resolved=False,
                escalate=True,
            )

        col_names = list(spec.columns.keys())

        columns_missing = [
            col for col in col_names
            if not any(row.get(col, "") for row in rows)
        ]

        narrow_columns_resolved = all(bool(row.get("your_total", "")) for row in rows)

        arithmetic_ok = True
        for row in rows:
            your_total = _try_parse_amount(row.get("your_total", ""))
            if your_total is None:
                continue
            component_sum = 0.0
            any_component = False
            for col in _PATIENT_COLS:
                v = _try_parse_amount(row.get(col, ""))
                if v is not None:
                    component_sum += v
                    any_component = True
            if any_component and abs(component_sum - your_total) > 0.01:
                arithmetic_ok = False
                break

        score = 1.0
        if not narrow_columns_resolved:
            score -= _PENALTY_NO_YOUR_TOTAL
        score -= min(len(columns_missing) * _PENALTY_MISSING_COL, _MAX_MISSING_COL_PENALTY)
        if not arithmetic_ok:
            score -= _PENALTY_ARITHMETIC
        score = max(0.0, score)

        return TableDiagnostic(
            score=score,
            columns_missing=columns_missing,
            arithmetic_ok=arithmetic_ok,
            narrow_columns_resolved=narrow_columns_resolved,
            escalate=score < ESCALATION_THRESHOLD,
        )
    except Exception:
        logger.error("_compute_diagnostic: unexpected failure", exc_info=True)
        return TableDiagnostic(
            score=0.0,
            columns_missing=[],
            arithmetic_ok=False,
            narrow_columns_resolved=False,
            escalate=True,
        )


def parse_table(
    block: Block,
    spec: ColumnSpec,
    *,
    second_engine: SecondEngine | None = None,
    escalation_threshold: float = ESCALATION_THRESHOLD,
) -> TableParseResult:
    """
    Parse a table ``Block`` into a ``TableParseResult``.

    Runs a coordinate-bucket pass (L0). If the diagnostic score falls below
    ``escalation_threshold`` and a ``second_engine`` is supplied, re-parses with
    the second engine and keeps whichever result scores higher (L1 escalation).

    Rows are emitted in reading order across all pages. Each row dict contains
    every column name in ``spec.columns``; columns with no assigned words are
    the empty string. Collection stops at the first row-terminator phrase.

    Never raises — returns an empty ``TableParseResult`` on any error.
    """
    _degraded = TableDiagnostic(
        score=0.0, columns_missing=[], arithmetic_ok=False,
        narrow_columns_resolved=False, escalate=True,
    )
    try:
        columns = spec.columns
        if not columns:
            return TableParseResult(rows=[], parsing_method="coordinate_bucket", diagnostic=_degraded)

        terminators = [t.lower() for t in spec.row_terminator]
        ordered = sorted(block.words, key=lambda w: (w.page, w.y0, w.x0))

        rows: list[tuple[int, int, list[Word]]] = []  # (page, anchor_y0, words)
        for word in ordered:
            if _is_terminator(word, terminators):
                break
            placed = False
            for idx in range(len(rows) - 1, -1, -1):
                page, anchor_y0, row_words = rows[idx]
                if page != word.page:
                    continue
                if abs(word.y0 - anchor_y0) <= _ROW_Y_TOLERANCE:
                    row_words.append(word)
                    placed = True
                    break
                break
            if not placed:
                rows.append((word.page, word.y0, [word]))

        primary_rows: list[dict[str, str]] = []
        for _page, _anchor_y0, row_words in rows:
            buckets: dict[str, list[Word]] = {name: [] for name in columns}
            for word in row_words:
                buckets[_nearest_column(word, columns)].append(word)
            row_dict: dict[str, str] = {}
            for name in columns:
                cell_words = sorted(buckets[name], key=lambda w: w.x0)
                row_dict[name] = " ".join(w.text for w in cell_words)
            primary_rows.append(row_dict)

        primary_diag = _compute_diagnostic(primary_rows, spec)

        if primary_diag.escalate and second_engine is not None:
            logger.info(
                "parse_table: escalating to second engine "
                f"(primary score={primary_diag.score:.2f})"
            )
            alt_rows = second_engine.parse(block, spec)
            alt_diag = _compute_diagnostic(alt_rows, spec)
            if alt_diag.score > primary_diag.score:
                return TableParseResult(
                    rows=alt_rows,
                    parsing_method="second_engine",
                    diagnostic=alt_diag,
                )

        return TableParseResult(
            rows=primary_rows,
            parsing_method="coordinate_bucket",
            diagnostic=primary_diag,
        )
    except Exception:
        logger.error("parse_table: unexpected failure", exc_info=True)
        return TableParseResult(rows=[], parsing_method="coordinate_bucket", diagnostic=_degraded)
