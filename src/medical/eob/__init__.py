"""EOB extraction package: classify -> document -> artifacts -> pipeline -> persist."""

from src.medical.eob.bridge import bridge_eob_to_claims
from src.medical.eob.ingestion import (
    _format_consent_prompt,
    _format_eob_confirm,
    commit_eob_ingestion,
)
from src.medical.eob.persist import (
    get_eob_claim_history,
    get_latest_eob_claim,
    persist_eob,
)
from src.medical.eob.pipeline import REGISTRY, process_eob
from src.medical.eob.validate import validate

__all__ = [
    "REGISTRY",
    "process_eob",
    "validate",
    "persist_eob",
    "bridge_eob_to_claims",
    "get_latest_eob_claim",
    "get_eob_claim_history",
    "commit_eob_ingestion",
    "_format_eob_confirm",
    "_format_consent_prompt",
]
