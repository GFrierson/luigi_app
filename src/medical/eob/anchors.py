"""
Shared insurer-anchor phrases for EOB classification and extraction.

``_INSURER_PHRASE_MAP`` is the single source of truth for the coarse insurer
brand phrases; it is imported by both ``src/medical/extraction.py`` (Phase 13
dispatch) and ``src/medical/eob/classify.py`` (anchor-rescue gate).
"""

import logging

logger = logging.getLogger(__name__)


# (phrase_in_lowercase, insurer_key) — coarse insurer detection.
# The right-hand insurer keys are the single source of truth for insurer
# identity; downstream consumers (extraction.py dispatch, the EOB pipeline
# REGISTRY, and extractors/allowlist.py entries) must reference these exact
# key strings.
_INSURER_PHRASE_MAP: list[tuple[str, str]] = [
    ("blue cross blue shield of georgia", "anthem"),
    ("anthem", "anthem"),
    ("bcbs", "anthem"),
]


ANCHOR_PHRASES: list[str] = [p for p, _ in _INSURER_PHRASE_MAP] + [
    "explanation of benefits",
    "claim number",
    "subscriber",
]


def identify(text: str) -> str | None:
    """
    Return the insurer key for the first matching phrase in ``text``, or None.

    Never raises — returns None on any error.
    """
    try:
        lowered = text.lower()
        for phrase, key in _INSURER_PHRASE_MAP:
            if phrase in lowered:
                return key
        return None
    except Exception:
        logger.error("identify: unexpected failure", exc_info=True)
        return None
