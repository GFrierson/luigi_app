"""
Reading-order segmentation of a ``Document`` into typed ``Block`` regions.

``segment`` slides a small window over the document's words (in reading order)
to detect issuer ``Signature`` anchor phrases, opening a new ``Block`` of that
kind and closing the current one when a competing anchor or a terminator phrase
is seen. The engine is issuer-agnostic: all issuer specifics arrive via the
``signatures`` argument (see ``src/medical/eob/profiles``).

Coordinates are assumed already normalized to OCR-DPI pixel space by the
``Document`` builder (see ``from_text_layer`` in ``document.py``), so no scaling
happens here.

Never raises: any failure returns ``[]`` and is logged with ``exc_info=True``.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from src.medical.eob.types import Document, Word

if TYPE_CHECKING:
    from src.medical.eob.profiles import Signature

logger = logging.getLogger(__name__)


# Sliding-window size (in words) used to detect multi-word anchor/terminator
# phrases that span several tokens.
_WINDOW_SIZE = 5


@dataclass(frozen=True)
class Block:
    kind: str
    words: list[Word]
    page_span: tuple[int, int]  # (first_page, last_page) inclusive


def _reading_order(words: list[Word]) -> list[Word]:
    """Return words sorted into top-to-bottom, left-to-right reading order."""
    return sorted(words, key=lambda w: (w.page, w.y0, w.x0))


def _window_text(words: list[Word], start: int, size: int) -> str:
    """Lowercased space-joined text of up to ``size`` words starting at ``start``."""
    return " ".join(w.text for w in words[start : start + size]).lower()


def _match_phrase(window: str, phrases: list[str]) -> bool:
    """
    True if any phrase (already lowercase) begins at the start of the window.

    Anchoring at the window start — rather than testing membership anywhere in
    the lookahead — ensures an anchor/terminator fires precisely at the word
    where its phrase begins, not when the phrase merely appears a few tokens
    ahead. This keeps adjacent blocks (e.g. consecutive claim banners) from
    collapsing into one.
    """
    return any(window.startswith(phrase) for phrase in phrases)


def _match_anchor(
    sig: "Signature", window: str, window_words: list[Word]
) -> bool:
    """True if the window matches the signature's anchor phrase OR its predicate."""
    if _match_phrase(window, [p.lower() for p in sig.anchor_phrases]):
        return True
    return bool(sig.anchor_predicate is not None and sig.anchor_predicate(window_words))


def _match_terminator(
    window: str,
    phrases: list[str],
    predicate: "Callable[..., bool] | None",
    window_words: list[Word],
) -> bool:
    """True if the window matches a terminator phrase OR the terminator predicate."""
    if _match_phrase(window, phrases):
        return True
    return bool(predicate is not None and predicate(window_words))


def _make_block(kind: str, words: list[Word]) -> Block:
    """Build a Block, computing its inclusive page_span from its words."""
    pages = [w.page for w in words]
    span = (min(pages), max(pages)) if pages else (0, 0)
    return Block(kind=kind, words=words, page_span=span)


def segment(doc: Document, signatures: "list[Signature]") -> list[Block]:
    """
    Segment ``doc`` into a list of typed ``Block`` regions in reading order.

    A new block opens when a signature's anchor phrase is detected in the
    sliding window. The current block closes when (a) a different signature's
    anchor is detected, or (b) one of the current signature's terminator
    phrases is detected. Multiple blocks of the same kind are expected (e.g.
    one ``claim_banner`` per claim).

    Signatures may also supply an ``anchor_predicate`` and/or
    ``terminator_predicate`` (callables receiving the window's Word objects)
    that are OR-ed with the phrase match, enabling geometry-based detection
    when phrase matching alone is insufficient.

    Never raises — returns ``[]`` on any error.
    """
    try:
        ordered = _reading_order(list(doc.words))
        if not ordered:
            return []

        blocks: list[Block] = []
        current_kind: str | None = None
        current_words: list[Word] = []
        current_terminators: list[str] = []
        current_terminator_predicate: Callable[..., bool] | None = None

        i = 0
        n = len(ordered)
        while i < n:
            window = _window_text(ordered, i, _WINDOW_SIZE)
            window_words = ordered[i : i + _WINDOW_SIZE]

            # Does the window start a new signature region?
            matched_sig: "Signature | None" = None
            for sig in signatures:
                if _match_anchor(sig, window, window_words):
                    matched_sig = sig
                    break

            # Terminate the current block before opening a different one.
            if current_kind is not None:
                hit_terminator = _match_terminator(
                    window,
                    current_terminators,
                    current_terminator_predicate,
                    window_words,
                )
                switching = matched_sig is not None and matched_sig.kind != current_kind
                if hit_terminator or switching:
                    if current_words:
                        blocks.append(_make_block(current_kind, current_words))
                    current_kind = None
                    current_words = []
                    current_terminators = []
                    current_terminator_predicate = None

            if matched_sig is not None and current_kind is None:
                current_kind = matched_sig.kind
                current_terminators = [p.lower() for p in matched_sig.terminator_phrases]
                current_terminator_predicate = matched_sig.terminator_predicate

            if current_kind is not None:
                current_words.append(ordered[i])

            i += 1

        if current_kind is not None and current_words:
            blocks.append(_make_block(current_kind, current_words))

        return blocks
    except Exception:
        logger.error("segment: unexpected failure", exc_info=True)
        return []
