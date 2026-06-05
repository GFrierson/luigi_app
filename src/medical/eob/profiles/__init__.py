"""
Generic, issuer-agnostic EOB extraction engine.

This module defines the data shapes an issuer profile is built from
(``Signature``, ``ColumnSpec``, ``IssuerProfile``) and the ``ProfileExtractor``
that drives them. ``ProfileExtractor`` satisfies the ``Extractor`` Protocol and
carries ZERO issuer-specific logic: every issuer specific (anchor phrases,
column geometry, field parsers) is supplied by the ``IssuerProfile`` it wraps.

Concrete issuer profiles live in sibling modules (e.g. ``anthem.py``).

Never raises across the ``extract`` boundary: failures degrade to an empty-but-
valid ``EOBDocument`` and are logged with ``exc_info=True``.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable

from src.medical.eob.blocks import Block, segment
from src.medical.eob.types import (
    Claim,
    Document,
    EOBDocument,
    EOBSubtype,
    LineItem,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signature:
    kind: str
    anchor_phrases: list[str]
    terminator_phrases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ColumnSpec:
    columns: dict[str, int]      # column_name -> x_center in normalized pixels
    row_terminator: list[str]    # phrases that end the table body


@dataclass
class IssuerProfile:
    issuer: str
    signatures: list[Signature]
    column_specs: dict[str, ColumnSpec]      # kind -> ColumnSpec
    extractors: dict[str, Callable]          # kind -> callable(block, profile)


# LineItem field names, in declaration order, used to build a LineItem from a
# parsed table row dict. Missing keys default to "".
_LINE_ITEM_FIELDS: tuple[str, ...] = (
    "service_date",
    "service",
    "reason_code",
    "doctor_charges",
    "discounts",
    "allowed",
    "anthem_paid",
    "copay",
    "deductible",
    "coinsurance",
    "not_covered",
    "your_total",
)


def _group_blocks_by_kind(blocks: list[Block]) -> dict[str, list[Block]]:
    """Bucket blocks by their ``kind``, preserving reading order within a kind."""
    grouped: dict[str, list[Block]] = {}
    for block in blocks:
        grouped.setdefault(block.kind, []).append(block)
    return grouped


def _pair_claims(
    banner_blocks: list[Block], table_blocks: list[Block]
) -> list[tuple[Block | None, Block | None]]:
    """
    Greedily pair claim banner blocks with claim table blocks by page order.

    A banner's last page should be <= its table's first page, and a banner is
    paired with the closest unused table at or after it. Unpaired banners or
    tables are emitted with the missing side as ``None``.
    """
    banners = sorted(banner_blocks, key=lambda b: b.page_span)
    tables = sorted(table_blocks, key=lambda b: b.page_span)
    used_tables: set[int] = set()
    pairs: list[tuple[Block | None, Block | None]] = []

    for banner in banners:
        best_idx: int | None = None
        best_first_page: int | None = None
        for idx, table in enumerate(tables):
            if idx in used_tables:
                continue
            if table.page_span[0] >= banner.page_span[1]:
                if best_first_page is None or table.page_span[0] < best_first_page:
                    best_first_page = table.page_span[0]
                    best_idx = idx
        if best_idx is not None:
            used_tables.add(best_idx)
            pairs.append((banner, tables[best_idx]))
        else:
            pairs.append((banner, None))

    for idx, table in enumerate(tables):
        if idx not in used_tables:
            pairs.append((None, table))

    return pairs


def _assemble_claim(banner_data: dict, table_rows: list[dict]) -> Claim:
    """Construct a ``Claim`` from parsed banner data and parsed table rows."""
    line_items: list[LineItem] = []
    for row in table_rows:
        line_items.append(
            LineItem(**{f: str(row.get(f, "")) for f in _LINE_ITEM_FIELDS})
        )
    return Claim(
        patient=str(banner_data.get("patient", "")),
        claim_number=str(banner_data.get("claim_number", "")),
        received_date=banner_data.get("received_date"),
        provider=str(banner_data.get("provider", "")),
        in_network=bool(banner_data.get("in_network", False)),
        patient_owes=str(banner_data.get("patient_owes", "")),
        line_items=line_items,
    )


class ProfileExtractor:
    """Satisfies the Extractor Protocol. Carries zero issuer-specific logic."""

    def __init__(self, profile: IssuerProfile) -> None:
        self._profile = profile

    def _run_extractor(self, kind: str, *args):
        """Invoke a profile extractor by kind, returning a safe default on miss."""
        fn = self._profile.extractors.get(kind)
        if fn is None:
            return None
        return fn(*args, self._profile)

    def extract(self, doc: Document) -> EOBDocument:
        try:
            blocks = segment(doc, self._profile.signatures)
            grouped = _group_blocks_by_kind(blocks)

            # Header -> subscriber.
            subscriber = ""
            for header_block in grouped.get("header", []):
                header_data = self._run_extractor("header", header_block) or {}
                if header_data.get("subscriber"):
                    subscriber = str(header_data["subscriber"])
                    break

            # Doc banner -> subtype.
            subtype: EOBSubtype = "summary"
            for banner_block in grouped.get("doc_banner", []):
                banner_data = self._run_extractor("doc_banner", banner_block) or {}
                detected = banner_data.get("subtype")
                if detected:
                    subtype = detected  # type: ignore[assignment]
                    break

            # Claims: pair claim_banner blocks with claim_table blocks.
            pairs = _pair_claims(
                grouped.get("claim_banner", []), grouped.get("claim_table", [])
            )
            claims: list[Claim] = []
            for banner_block, table_block in pairs:
                banner_parsed: dict = {}
                if banner_block is not None:
                    banner_parsed = (
                        self._run_extractor("claim_banner", banner_block) or {}
                    )
                table_rows: list[dict] = []
                if table_block is not None:
                    table_rows = (
                        self._run_extractor("claim_table", table_block) or []
                    )
                if banner_parsed or table_rows:
                    claims.append(_assemble_claim(banner_parsed, table_rows))

            return EOBDocument(
                issuer=self._profile.issuer,
                subtype=subtype,
                subscriber=subscriber,
                claims=claims,
            )
        except Exception:
            logger.error(
                "ProfileExtractor.extract: unexpected failure for issuer '%s'",
                self._profile.issuer,
                exc_info=True,
            )
            return EOBDocument(
                issuer=self._profile.issuer,
                subtype="summary",
                subscriber="",
                claims=[],
            )
