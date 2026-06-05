"""
Shared insurer-anchor phrases for EOB classification and extraction.

``_INSURER_PHRASE_MAP`` is the single source of truth for the coarse insurer
brand phrases; it is imported by both ``src/medical/extraction.py`` (Phase 13
dispatch) and ``src/medical/eob/classify.py`` (anchor-rescue gate).
"""

import logging

logger = logging.getLogger(__name__)


# (phrase_in_lowercase, insurer_key) — coarse insurer detection.
_INSURER_PHRASE_MAP: list[tuple[str, str]] = [
    ("blue cross blue shield of georgia", "anthm"),
    ("anthem", "anthm"),
    ("bcbs", "anthm"),
]


ANCHOR_PHRASES: list[str] = [p for p, _ in _INSURER_PHRASE_MAP] + [
    "explanation of benefits",
    "claim number",
    "subscriber",
]
