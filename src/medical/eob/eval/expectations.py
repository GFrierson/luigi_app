"""
Loading labeled fixture expectations into typed ``EOBDocument`` objects.

Each fixture under ``tests/fixtures/expected/<fixture>.json`` carries its
failure-mode dimensions (``insurer``, ``kind``, ``subtype``) plus an ``eob``
object that deserializes into the public ``EOBDocument`` contract. The harness
diffs extraction output against this expectation.

Pure / never-raise: a malformed file returns ``None`` and is logged.
"""

import json
import logging
from dataclasses import dataclass

from src.medical.eob.types import Claim, EOBDocument, LineItem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Expectation:
    """A labeled fixture: dimensions + the expected parsed EOB."""

    fixture: str
    insurer: str
    kind: str
    subtype: str
    eob: EOBDocument


def _line_item_from_dict(data: dict) -> LineItem:
    """Build a LineItem, defaulting any absent column to an empty string."""
    fields = {name: str(data.get(name, "") or "") for name in LineItem.__annotations__}
    return LineItem(**fields)


def _claim_from_dict(data: dict) -> Claim:
    """Build a Claim from a dict, deserializing nested line items."""
    received = data.get("received_date")
    return Claim(
        patient=str(data.get("patient", "") or ""),
        claim_number=str(data.get("claim_number", "") or ""),
        received_date=received if received is None else str(received),
        provider=str(data.get("provider", "") or ""),
        in_network=bool(data.get("in_network", False)),
        patient_owes=str(data.get("patient_owes", "") or ""),
        line_items=[
            _line_item_from_dict(item) for item in data.get("line_items", [])
        ],
    )


def eob_from_dict(data: dict) -> EOBDocument:
    """Deserialize the ``eob`` object of an expectation file into an EOBDocument."""
    return EOBDocument(
        issuer=str(data.get("issuer", "") or ""),
        subtype=data.get("subtype", "summary"),
        subscriber=str(data.get("subscriber", "") or ""),
        claims=[_claim_from_dict(c) for c in data.get("claims", [])],
    )


def load_expectation(json_path: str) -> Expectation | None:
    """
    Load and deserialize one expectation JSON file.

    Never raises — returns ``None`` on missing file or malformed content.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return Expectation(
            fixture=str(data["fixture"]),
            insurer=str(data.get("insurer", "")),
            kind=str(data.get("kind", "")),
            subtype=str(data.get("subtype", "")),
            eob=eob_from_dict(data["eob"]),
        )
    except Exception:
        logger.error(
            f"load_expectation: failed to load {json_path}", exc_info=True
        )
        return None
