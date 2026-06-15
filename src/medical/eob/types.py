"""
Public contract types for the EOB extraction engine.

NOTE: This module intentionally uses Python 3.12+ syntax that is new to this
codebase — frozen dataclasses, ``Enum``, ``Protocol``, and the PEP 695
``type`` statement for tagged-union aliases (``type EOBResult = ...``). This is
the required style for this package; do not down-level it to pre-3.12 forms.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol


class PdfKind(Enum):
    TEXT = "text"        # USABLE embedded text layer -> native word boxes
    IMAGE = "image"      # image-only OR garbage text layer -> OCR
    MIXED = "mixed"      # some pages each
    NOT_PDF = "not_pdf"


@dataclass(frozen=True)
class Word:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    page: int  # 0-based page index


@dataclass(frozen=True)
class Document:
    """OCR/normalized INPUT artifact (not the parsed EOB)."""

    text: str
    words: list[Word]
    page_images: list[bytes]  # PNG bytes, one per page
    source: PdfKind


@dataclass(frozen=True)
class LineItem:
    service_date: str
    service: str
    reason_code: str
    doctor_charges: str
    discounts: str
    allowed: str
    anthem_paid: str
    copay: str
    deductible: str
    coinsurance: str
    not_covered: str
    your_total: str


@dataclass(frozen=True)
class Claim:
    patient: str
    claim_number: str
    received_date: str | None
    provider: str
    in_network: bool
    patient_owes: str
    line_items: list[LineItem]


EOBSubtype = Literal["summary", "denial", "payment_notice", "duplicate_notice"]


@dataclass(frozen=True)
class EOBDocument:
    """The parsed EOB — the unit extraction returns."""

    issuer: str
    subtype: EOBSubtype
    subscriber: str
    claims: list[Claim]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    confidence: float
    issues: list[str]


@dataclass(frozen=True)
class GroundedField:
    field: str          # dotted path e.g. "issuer", "claims[0].anthem_paid"
    value: str | None   # None when found=False
    page: int | None    # 0-based, matches Word.page; None when found=False
    span: str | None    # verbatim token(s) from the page image; None when found=False
    found: bool


@dataclass(frozen=True)
class GroundingReport:
    fields: list[GroundedField]   # all extracted fields with provenance
    ungrounded: list[str]         # field paths that failed the post-check


@dataclass(frozen=True)
class Extracted:
    eob: EOBDocument
    validation: ValidationResult
    extractor: str


@dataclass(frozen=True)
class UnknownType:
    doc: Document


@dataclass(frozen=True)
class Unreadable:
    reason: str


type EOBResult = Extracted | UnknownType | Unreadable


class Extractor(Protocol):
    """AnthemExtractor + LLM both satisfy this."""

    def extract(self, doc: Document) -> EOBDocument: ...


class GroundedExtractor(Protocol):
    """LLMVisionExtractor — returns EOBDocument plus provenance report."""

    def extract(self, doc: Document) -> tuple[EOBDocument, GroundingReport]: ...
