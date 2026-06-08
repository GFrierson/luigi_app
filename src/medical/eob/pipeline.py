"""
Top-level EOB extraction pipeline: identify -> dispatch -> extract -> validate.

``process_eob`` takes a normalized ``Document`` and returns a tagged
``EOBResult``: ``Extracted`` (issuer recognized and parsed), ``UnknownType``
(no recognized issuer; deferred to an LLM fallback in a later phase), or
``Unreadable`` (no legible text).

``REGISTRY`` maps insurer keys (as returned by ``identify`` from the shared
``_INSURER_PHRASE_MAP``) to the ``Extractor`` that handles them.
"""

import logging

from src.medical.eob.anchors import identify
from src.medical.eob.extractors.llm import LLMVisionExtractor
from src.medical.eob.profiles import ProfileExtractor
from src.medical.eob.profiles.anthem import ANTHEM_PROFILE
from src.medical.eob.types import (
    Document,
    EOBResult,
    Extracted,
    Extractor,
    Unreadable,
    UnknownType,
)
from src.medical.eob.validate import validate

logger = logging.getLogger(__name__)


# Insurer key -> Extractor. Keys MUST match what identify() returns (the
# right-hand values of _INSURER_PHRASE_MAP in anchors.py).
REGISTRY: dict[str, Extractor] = {
    "anthem": ProfileExtractor(ANTHEM_PROFILE),
}


# Fallback extractor for documents with no recognized issuer, used only when
# the caller opts in via process_eob(..., llm_override=True).
LLM_EXTRACTOR: Extractor = LLMVisionExtractor()


def process_eob(doc: Document, *, llm_override: bool = False) -> EOBResult:
    """
    Run the EOB extraction pipeline over a normalized ``Document``.

    Returns ``Unreadable`` for blank documents, ``Extracted`` for a recognized
    issuer, and ``UnknownType`` otherwise (unless ``llm_override`` is set, which
    is reserved for the Phase 3 LLM fallback).
    """
    if not doc.text.strip():
        return Unreadable("no legible text")
    issuer = identify(doc.text)
    if issuer is not None:
        eob = REGISTRY[issuer].extract(doc)
        return Extracted(eob, validate(eob, doc.source), extractor=issuer)
    if llm_override:
        eob = LLM_EXTRACTOR.extract(doc)
        return Extracted(eob, validate(eob, doc.source), extractor="llm")
    return UnknownType(doc)
