"""
Artifact detection on a normalized ``Document``.

Phase 1 detects only — no segmentation. Flags surfaced: ``check``, ``eop``,
``ach``, ``out_of_order``, ``multi_doc``. ``detect_artifacts`` never raises;
any unexpected failure returns ``[]`` (logged with ``exc_info=True``).
"""

import logging
import re
from collections import defaultdict

from src.medical.eob.types import Document, Word

logger = logging.getLogger(__name__)


_PAGE_X_OF_Y = re.compile(r"page\s+(\d+)\s+of\s+(\d+)")


def _flag_check(text: str) -> bool:
    return any(
        phrase in text
        for phrase in ("check number", "pay to the order of", "void after", "check date")
    )


def _flag_eop(text: str) -> bool:
    return any(
        phrase in text
        for phrase in ("explanation of payment", "remittance advice", "provider remittance")
    )


def _flag_ach(text: str) -> bool:
    return any(
        phrase in text
        for phrase in ("ach", "eft", "electronic funds transfer", "trace number", "direct deposit")
    )


def _flag_out_of_order(words: list[Word]) -> bool:
    """
    True when 'page X of Y' markers across pages are not monotonically
    non-decreasing in X (e.g. a page-2 sheet stacked before a page-1 sheet).
    """
    by_page: dict[int, list[Word]] = defaultdict(list)
    for w in words:
        by_page[w.page].append(w)

    page_nums: list[int] = []
    for page in sorted(by_page):
        page_text = " ".join(w.text for w in by_page[page]).lower()
        match = _PAGE_X_OF_Y.search(page_text)
        if match:
            page_nums.append(int(match.group(1)))

    if len(page_nums) < 2:
        return False
    return any(page_nums[i] > page_nums[i + 1] for i in range(len(page_nums) - 1))


def _flag_multi_doc(text: str) -> bool:
    """More than one 'page 1 of' marker implies multiple stacked documents."""
    return text.count("page 1 of") > 1


def detect_artifacts(doc: Document) -> list[str]:
    """Return a sorted, deduplicated list of artifact flag keys for the document."""
    try:
        text = doc.text.lower()
        flags: set[str] = set()
        if _flag_check(text):
            flags.add("check")
        if _flag_eop(text):
            flags.add("eop")
        if _flag_ach(text):
            flags.add("ach")
        if _flag_out_of_order(doc.words):
            flags.add("out_of_order")
        if _flag_multi_doc(text):
            flags.add("multi_doc")
        return sorted(flags)
    except Exception:
        logger.error("detect_artifacts: unexpected failure", exc_info=True)
        return []
