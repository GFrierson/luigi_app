"""
Registry of deterministic extractors eligible for dispatch (Phase 13).

Each entry shape:
    {
        "insurer": str,            # must be a valid insurer key (see
                                   # _INSURER_PHRASE_MAP in extraction.py)
        "doc_type": str,           # 'statement' | 'eob' | 'receipt' | 'check'
        "extractor_version": str,  # human-readable version tag for logging
        "module": str,             # importable module name relative to
                                   # src.medical.extractors (e.g. "anthm_eob")
    }

This is a pure data file — it must NOT import from src.* to avoid import
cycles with the extraction dispatcher that reads it.

Empty until Phases 14/15 register concrete extractors. As the phrase list in
_INSURER_PHRASE_MAP is expanded (e.g. Anthem brand names — Empire BlueCross,
Amerigroup, etc.), document each registered extractor's coverage here.
"""

EXTRACTOR_ALLOWLIST: list[dict] = []
