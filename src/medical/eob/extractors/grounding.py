"""
Post-extraction grounding check for LLM-extracted EOB fields.

check_grounding verifies that each GroundedField where found=True actually
has its span tokens present in Document.words on the cited page. Values not
found on-page are added to GroundingReport.ungrounded.
"""

import logging

from src.medical.eob.types import Document, GroundedField, GroundingReport

logger = logging.getLogger(__name__)


def _normalize_token(s: str) -> str:
    """Strip $, commas, whitespace and lowercase for comparison."""
    return s.replace("$", "").replace(",", "").strip().lower()


def check_grounding(fields: list[GroundedField], doc: Document) -> GroundingReport:
    """
    Verify each GroundedField against Document.words on its cited page.

    Fields with found=False are skipped (they declared absence — no check
    needed). Never raises — returns GroundingReport(fields=fields,
    ungrounded=[]) on failure.
    """
    try:
        ungrounded: list[str] = []
        for gf in fields:
            if not gf.found or gf.span is None or gf.page is None:
                continue
            page_tokens = {
                _normalize_token(w.text)
                for w in doc.words
                if w.page == gf.page and w.text
            }
            span_tokens = [_normalize_token(t) for t in gf.span.split() if t]
            if not span_tokens:
                continue
            if not all(t in page_tokens for t in span_tokens):
                ungrounded.append(gf.field)
        return GroundingReport(fields=fields, ungrounded=ungrounded)
    except Exception:
        logger.error("check_grounding: unexpected failure", exc_info=True)
        return GroundingReport(fields=fields, ungrounded=[])
