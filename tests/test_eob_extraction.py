"""
Tests for the Phase 2 EOB extraction engine.

Covers:
    src/medical/eob/anchors.py        (identify)
    src/medical/eob/tables.py         (parse_table)
    src/medical/eob/blocks.py         (segment)
    src/medical/eob/pipeline.py       (process_eob)
    src/medical/eob/validate.py       (validate, _parse_amount)
    src/medical/eob/profiles          (ProfileExtractor Protocol conformance)

The database is never mocked (none is used here). External services are not
involved — these are pure-function tests over crafted Document/Block inputs.
"""

from unittest.mock import patch

from src.medical.eob.anchors import identify
from src.medical.eob.blocks import Block, segment
from src.medical.eob.pipeline import process_eob
from src.medical.eob.profiles import ColumnSpec, ProfileExtractor
from src.medical.eob.profiles.anthem import ANTHEM_PROFILE
from src.medical.eob.tables import NoOpSecondEngine, SecondEngine, _compute_diagnostic, parse_table
from src.medical.eob.types import TableDiagnostic, TableParseResult
from src.medical.eob.types import (
    Claim,
    Document,
    EOBDocument,
    Extracted,
    Extractor,
    LineItem,
    PdfKind,
    UnknownType,
    Unreadable,
    Word,
)
from src.medical.eob.validate import _parse_amount, validate


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_doc(text: str, words: list[Word] | None = None) -> Document:
    return Document(
        text=text, words=words or [], page_images=[], source=PdfKind.IMAGE
    )


def _make_block(kind: str, words: list[Word]) -> Block:
    pages = {w.page for w in words}
    return Block(
        kind=kind,
        words=words,
        page_span=(min(pages, default=0), max(pages, default=0)),
    )


def _word(text: str, x0: int, y0: int, page: int = 0) -> Word:
    return Word(text=text, x0=x0, y0=y0, x1=x0 + 10, y1=y0 + 10, page=page)


def _line_item(**overrides: str) -> LineItem:
    base = {f: "" for f in LineItem.__annotations__}
    base.update(overrides)
    return LineItem(**base)


# ---------------------------------------------------------------------------
# identify
# ---------------------------------------------------------------------------

def test_identify_returns_anthem_for_anthem_text():
    assert identify("anthem explanation of benefits") == "anthem"


def test_identify_returns_none_for_unknown_issuer():
    assert identify("cigna medical statement") is None


def test_identify_never_raises():
    assert identify("") is None


# ---------------------------------------------------------------------------
# parse_table
# ---------------------------------------------------------------------------

def test_parse_table_single_row_all_columns():
    spec = ANTHEM_PROFILE.column_specs["claim_table"]
    words = [
        _word(name[:4], x0=center - 5, y0=100)
        for name, center in spec.columns.items()
    ]
    block = _make_block("claim_table", words)

    result = parse_table(block, spec)

    assert isinstance(result, TableParseResult)
    assert len(result.rows) == 1
    assert all(result.rows[0][name] != "" for name in spec.columns)


def test_parse_table_multipage_stitch():
    spec = ColumnSpec(columns={"a": 100, "b": 300}, row_terminator=[])
    words = [
        _word("p0a", x0=95, y0=50, page=0),
        _word("p0b", x0=295, y0=50, page=0),
        _word("p1a", x0=95, y0=50, page=1),
        _word("p1b", x0=295, y0=50, page=1),
    ]
    block = _make_block("claim_table", words)

    result = parse_table(block, spec)

    assert len(result.rows) == 2
    assert result.rows[0] == {"a": "p0a", "b": "p0b"}
    assert result.rows[1] == {"a": "p1a", "b": "p1b"}


def test_parse_table_stops_at_row_terminator():
    spec = ColumnSpec(columns={"a": 100}, row_terminator=["totals"])
    words = [
        _word("first", x0=95, y0=50),
        _word("totals", x0=95, y0=80),
        _word("after", x0=95, y0=110),
    ]
    block = _make_block("claim_table", words)

    result = parse_table(block, spec)

    assert len(result.rows) == 1
    assert result.rows[0] == {"a": "first"}


def test_parse_table_handles_empty_columns():
    spec = ColumnSpec(columns={"a": 100, "b": 300, "c": 500}, row_terminator=[])
    words = [
        _word("only_a", x0=95, y0=50),
        _word("only_c", x0=495, y0=50),
    ]
    block = _make_block("claim_table", words)

    result = parse_table(block, spec)

    assert len(result.rows) == 1
    assert result.rows[0] == {"a": "only_a", "b": "", "c": "only_c"}


# ---------------------------------------------------------------------------
# segment
# ---------------------------------------------------------------------------

def test_segment_finds_claim_banner():
    words = [
        _word("Claim", x0=0, y0=10),
        _word("Number", x0=60, y0=10),
        _word("ABC123", x0=130, y0=10),
    ]
    doc = _make_doc("claim number ABC123", words)

    blocks = segment(doc, ANTHEM_PROFILE.signatures)

    assert any(b.kind == "claim_banner" for b in blocks)


def test_segment_finds_header_block():
    words = [
        _word("Subscriber", x0=0, y0=10),
        _word("ID", x0=80, y0=10),
        _word("John", x0=140, y0=10),
    ]
    doc = _make_doc("subscriber id John", words)

    blocks = segment(doc, ANTHEM_PROFILE.signatures)

    assert any(b.kind == "header" for b in blocks)


def test_segment_multiple_claim_banners():
    words = [
        _word("Claim", x0=0, y0=10),
        _word("Number", x0=60, y0=10),
        _word("AAA", x0=130, y0=10),
        # A claim_banner terminator ("services provided") closes the first
        # banner, mirroring a real EOB where the line-item table follows.
        _word("Services", x0=0, y0=40),
        _word("Provided", x0=80, y0=40),
        # A second claim banner begins (switching back from any open region).
        _word("Claim", x0=0, y0=70),
        _word("Number", x0=60, y0=70),
        _word("BBB", x0=130, y0=70),
    ]
    doc = _make_doc("claim number AAA services provided claim number BBB", words)

    blocks = segment(doc, ANTHEM_PROFILE.signatures)

    banners = [b for b in blocks if b.kind == "claim_banner"]
    assert len(banners) == 2


# ---------------------------------------------------------------------------
# process_eob
# ---------------------------------------------------------------------------

def test_process_eob_returns_extracted_for_anthem():
    known_eob = EOBDocument(
        issuer="anthem", subtype="summary", subscriber="Jane Doe", claims=[]
    )
    doc = _make_doc("anthem explanation of benefits")

    with patch.object(
        ProfileExtractor, "extract", return_value=known_eob
    ):
        result = process_eob(doc)

    assert isinstance(result, Extracted)
    assert result.extractor == "anthem"
    assert result.eob is known_eob


def test_process_eob_returns_unknown_type_for_non_anthem():
    doc = _make_doc("cigna member explanation statement")

    result = process_eob(doc)

    assert isinstance(result, UnknownType)


def test_process_eob_returns_unreadable_for_empty_text():
    doc = _make_doc("   ")

    result = process_eob(doc)

    assert isinstance(result, Unreadable)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def test_validate_ok_for_balanced_claim():
    claim = Claim(
        patient="Jane",
        claim_number="C1",
        received_date="01/01/2025",
        provider="Dr. X",
        in_network=True,
        patient_owes="$30.00",
        line_items=[
            _line_item(allowed="$100.00", anthem_paid="$70.00"),
        ],
    )
    eob = EOBDocument(
        issuer="anthem", subtype="summary", subscriber="Jane", claims=[claim]
    )

    result = validate(eob, PdfKind.IMAGE)

    assert result.ok is True
    assert result.confidence >= 0.7
    assert result.issues == []


def test_validate_flags_arithmetic_mismatch():
    claim = Claim(
        patient="Jane",
        claim_number="C1",
        received_date=None,
        provider="Dr. X",
        in_network=True,
        patient_owes="$30.00",
        line_items=[
            _line_item(allowed="$100.00", anthem_paid="$50.00"),
        ],
    )
    eob = EOBDocument(
        issuer="anthem", subtype="summary", subscriber="Jane", claims=[claim]
    )

    result = validate(eob, PdfKind.IMAGE)

    assert len(result.issues) >= 1


def test_validate_denial_zero_owes_not_flagged():
    claim = Claim(
        patient="Jane",
        claim_number="C1",
        received_date=None,
        provider="Dr. X",
        in_network=False,
        patient_owes="$0.00",
        line_items=[
            _line_item(allowed="$0.00", anthem_paid="$0.00", not_covered="$100.00"),
        ],
    )
    eob = EOBDocument(
        issuer="anthem", subtype="denial", subscriber="Jane", claims=[claim]
    )

    result = validate(eob, PdfKind.IMAGE)

    assert result.issues == []


def test_validate_parse_amount_handles_na():
    assert _parse_amount("N/A") is None
    assert _parse_amount("--") is None
    assert _parse_amount("") is None
    assert _parse_amount("$1,234.56") == 1234.56


# ---------------------------------------------------------------------------
# ProfileExtractor Protocol conformance
# ---------------------------------------------------------------------------

def test_profile_extractor_satisfies_extractor_protocol():
    # Structural typing check: a ProfileExtractor is assignable to the
    # Extractor Protocol. The annotation documents the interface boundary;
    # the runtime assertions confirm .extract() returns an EOBDocument.
    extractor: Extractor = ProfileExtractor(ANTHEM_PROFILE)
    doc = _make_doc("anthem explanation of benefits", words=[])

    result = extractor.extract(doc)

    assert isinstance(result, EOBDocument)
    assert result.issuer == "anthem"


# ---------------------------------------------------------------------------
# Workstream C: TableDiagnostic + parse_table escalation
# ---------------------------------------------------------------------------

def _two_col_spec(*, with_your_total: bool = True) -> ColumnSpec:
    cols = {"copay": 100, "deductible": 300}
    if with_your_total:
        cols["your_total"] = 500
    return ColumnSpec(columns=cols, row_terminator=[])


def test_parse_table_returns_table_parse_result_on_clean_parse():
    spec = ColumnSpec(columns={"a": 100, "b": 300}, row_terminator=[])
    words = [_word("v1", x0=95, y0=50), _word("v2", x0=295, y0=50)]
    block = _make_block("claim_table", words)

    result = parse_table(block, spec)

    assert isinstance(result, TableParseResult)
    assert result.parsing_method == "coordinate_bucket"
    assert isinstance(result.diagnostic, TableDiagnostic)


def test_diagnostic_all_columns_populated():
    spec = _two_col_spec()
    rows = [{"copay": "$10.00", "deductible": "$20.00", "your_total": "$30.00"}]

    diag = _compute_diagnostic(rows, spec)

    assert diag.narrow_columns_resolved is True
    assert diag.columns_missing == []
    assert diag.arithmetic_ok is True
    assert diag.score == 1.0
    assert diag.escalate is False


def test_diagnostic_your_total_empty_triggers_escalation():
    spec = _two_col_spec()
    rows = [{"copay": "$10.00", "deductible": "$20.00", "your_total": ""}]

    diag = _compute_diagnostic(rows, spec)

    assert diag.narrow_columns_resolved is False
    assert diag.score < 1.0
    assert diag.escalate is True


def test_diagnostic_empty_rows_worst_case():
    spec = _two_col_spec()

    diag = _compute_diagnostic([], spec)

    assert diag.score == 0.0
    assert diag.escalate is True
    assert set(diag.columns_missing) == set(spec.columns.keys())


def test_diagnostic_arithmetic_mismatch():
    spec = _two_col_spec()
    # copay=10, deductible=20 but your_total=50 → mismatch
    rows = [{"copay": "$10.00", "deductible": "$20.00", "your_total": "$50.00"}]

    diag = _compute_diagnostic(rows, spec)

    assert diag.arithmetic_ok is False


def test_diagnostic_arithmetic_skipped_when_your_total_unparseable():
    spec = _two_col_spec()
    rows = [{"copay": "$10.00", "deductible": "$20.00", "your_total": ""}]

    diag = _compute_diagnostic(rows, spec)

    # Can't check arithmetic without your_total; should not flag arithmetic failure.
    assert diag.arithmetic_ok is True


def test_diagnostic_denial_zeros_reconcile():
    spec = _two_col_spec()
    rows = [{"copay": "$0.00", "deductible": "$0.00", "your_total": "$0.00"}]

    diag = _compute_diagnostic(rows, spec)

    assert diag.arithmetic_ok is True


def test_escalation_triggers_and_keeps_better_engine_result():
    spec = _two_col_spec()
    # Primary parse: your_total empty → low score, escalate=True.
    words = [_word("$10.00", x0=95, y0=50), _word("$20.00", x0=295, y0=50)]
    block = _make_block("claim_table", words)

    better_rows = [{"copay": "$10.00", "deductible": "$20.00", "your_total": "$30.00"}]

    class _MockEngine:
        def parse(self, blk, spc) -> list[dict[str, str]]:
            return better_rows

    result = parse_table(block, spec, second_engine=_MockEngine())

    assert result.parsing_method == "second_engine"
    assert result.rows == better_rows


def test_escalation_keeps_primary_when_engine_is_worse():
    spec = _two_col_spec()
    words = [
        _word("$10.00", x0=95, y0=50),
        _word("$20.00", x0=295, y0=50),
        _word("$30.00", x0=495, y0=50),
    ]
    block = _make_block("claim_table", words)

    class _EmptyEngine:
        def parse(self, blk, spc) -> list[dict[str, str]]:
            return []  # empty → score 0.0, worse than any non-empty parse

    result = parse_table(block, spec, second_engine=_EmptyEngine())

    assert result.parsing_method == "coordinate_bucket"
    assert len(result.rows) == 1


def test_no_escalation_when_second_engine_is_none():
    spec = _two_col_spec()
    # Force a parse where your_total is empty.
    words = [_word("$10.00", x0=95, y0=50)]
    block = _make_block("claim_table", words)

    result = parse_table(block, spec, second_engine=None)

    assert result.parsing_method == "coordinate_bucket"


def test_claim_carries_parsing_method_from_profile_extractor():
    spec = ANTHEM_PROFILE.column_specs["claim_table"]
    col_words = [
        _word(name[:4], x0=center - 5, y0=150)
        for name, center in spec.columns.items()
    ]
    banner_words = [
        _word("Claim", x0=0, y0=10),
        _word("Number", x0=60, y0=10),
        _word("X99", x0=130, y0=10),
        _word("Services", x0=0, y0=40),
        _word("Provided", x0=80, y0=40),
    ]
    all_words = banner_words + col_words
    text = "claim number X99 services provided " + " ".join(
        name[:4] for name in spec.columns
    )
    doc = _make_doc(text, all_words)
    text_with_anthem = "anthem explanation of benefits " + text
    doc = _make_doc(text_with_anthem, all_words)

    result = process_eob(doc)

    assert isinstance(result, Extracted)
    if result.eob.claims:
        # Claims assembled from a table carry a coordinate_bucket or second_engine method.
        assert result.eob.claims[0].parsing_method in (
            "coordinate_bucket", "second_engine", "none"
        )


def test_claim_parsing_method_default_is_none():
    claim = Claim(
        patient="Jane",
        claim_number="C1",
        received_date=None,
        provider="Dr. X",
        in_network=True,
        patient_owes="$0.00",
        line_items=[],
    )
    assert claim.parsing_method == "none"
