"""
Coordinate-driven table parsing for EOB claim line-item grids.

``parse_table`` groups a ``Block``'s words into rows by their vertical position
and assigns each word to the nearest column by horizontal center, using the
``ColumnSpec`` column geometry (x-centers in normalized OCR-DPI pixel space).
Words beyond a row-terminator phrase are excluded.

Never raises: any failure returns ``[]`` and is logged with ``exc_info=True``.
"""

import logging

from src.medical.eob.blocks import Block
from src.medical.eob.profiles import ColumnSpec
from src.medical.eob.types import Word

logger = logging.getLogger(__name__)


# Words whose y0 differs by no more than this many pixels share a table row.
_ROW_Y_TOLERANCE = 8


def _nearest_column(word: Word, columns: dict[str, int]) -> str:
    """Return the column name whose x-center is closest to the word's x-center."""
    x_center = (word.x0 + word.x1) // 2
    return min(columns, key=lambda name: abs(x_center - columns[name]))


def _is_terminator(word: Word, terminators: list[str]) -> bool:
    """True if the word text matches any row-terminator phrase (case-insensitive)."""
    lowered = word.text.lower()
    return any(term in lowered for term in terminators)


def parse_table(block: Block, spec: ColumnSpec) -> list[dict[str, str]]:
    """
    Parse a table ``Block`` into a list of row dicts keyed by column name.

    Rows are emitted in reading order across all pages. Each row dict contains
    every column name in ``spec.columns``; columns with no assigned words are
    the empty string. Collection stops at the first row-terminator phrase.

    Never raises — returns ``[]`` on any error.
    """
    try:
        columns = spec.columns
        if not columns:
            return []

        terminators = [t.lower() for t in spec.row_terminator]
        ordered = sorted(block.words, key=lambda w: (w.page, w.y0, w.x0))

        # Group words into rows keyed by (page, anchor_y0). A word joins an
        # existing row on the same page when its y0 is within tolerance of that
        # row's anchor; otherwise it starts a new row.
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
                # Past this page's last open row anchor — stop scanning back.
                break
            if not placed:
                rows.append((word.page, word.y0, [word]))

        result: list[dict[str, str]] = []
        for _page, _anchor_y0, row_words in rows:
            buckets: dict[str, list[Word]] = {name: [] for name in columns}
            for word in row_words:
                buckets[_nearest_column(word, columns)].append(word)
            row_dict: dict[str, str] = {}
            for name in columns:
                cell_words = sorted(buckets[name], key=lambda w: w.x0)
                row_dict[name] = " ".join(w.text for w in cell_words)
            result.append(row_dict)

        return result
    except Exception:
        logger.error("parse_table: unexpected failure", exc_info=True)
        return []
