"""
PDF classification: TEXT / IMAGE / MIXED / NOT_PDF.

Uses a per-page text-*quality* gate rather than a raw character count, so an
insurer's garbage embedded text layer (high punctuation/digit noise, low real
alpha content) is correctly routed to OCR (IMAGE) instead of being trusted as
TEXT. An expected-anchor phrase can rescue a borderline page toward USABLE but
never demotes a page toward IMAGE.

Never raises: any parse failure (including a non-PDF byte stream) returns
``PdfKind.NOT_PDF`` and is logged with ``exc_info=True``.
"""

import logging

from src.medical.eob.anchors import ANCHOR_PHRASES
from src.medical.eob.types import PdfKind

logger = logging.getLogger(__name__)


MIN_USABLE_CHARS = 50
MIN_ALPHA_RATIO = 0.55

# Characters treated as legible/decodable when scoring text quality.
_LEGIBLE_EXTRA = set(".,/$%()-:# ")


def _alpha_ratio(text: str) -> float:
    """
    Ratio of legible chars (alphanumerics + common EOB punctuation) to all
    non-whitespace chars. Returns 0.0 when there are no non-whitespace chars.
    """
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return 0.0
    legible = sum(1 for c in non_ws if c.isalnum() or c in _LEGIBLE_EXTRA)
    return legible / len(non_ws)


def _has_expected_anchor(text: str) -> bool:
    """True if any expected EOB anchor phrase appears in the text."""
    lowered = text.lower()
    return any(phrase in lowered for phrase in ANCHOR_PHRASES)


def _page_is_usable(text: str) -> bool:
    """
    A page is usable when it has enough characters AND a healthy alpha ratio,
    OR enough characters AND an expected anchor phrase. The anchor is a POSITIVE
    override only: it can rescue a borderline page toward USABLE, never demote
    a page toward IMAGE.
    """
    long_enough = len(text.strip()) >= MIN_USABLE_CHARS
    if not long_enough:
        return False
    return _alpha_ratio(text) >= MIN_ALPHA_RATIO or _has_expected_anchor(text)


def classify_pdf(stream: bytes) -> PdfKind:
    """
    Classify a PDF byte stream as TEXT / IMAGE / MIXED / NOT_PDF.

    Aggregation:
        - 0 pages OR open/parse error -> NOT_PDF
        - usable_count == 0           -> IMAGE
        - usable_count == total_pages -> TEXT
        - 0 < usable_count < total    -> MIXED
    """
    try:
        import fitz

        with fitz.open(stream=stream, filetype="pdf") as pdf:
            total_pages = pdf.page_count
            if total_pages == 0:
                return PdfKind.NOT_PDF
            usable_count = 0
            for page in pdf:
                if _page_is_usable(page.get_text("text")):
                    usable_count += 1
    except Exception:
        logger.error("classify_pdf: failed to open/parse stream", exc_info=True)
        return PdfKind.NOT_PDF

    if usable_count == 0:
        return PdfKind.IMAGE
    if usable_count == total_pages:
        return PdfKind.TEXT
    return PdfKind.MIXED
