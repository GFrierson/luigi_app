"""
Tests for Phase 4 ingestion: matching, confirmation, and end-to-end pipeline.

Covers:
    src/medical/matching.py
    src/medical/confirmation.py
    src/medical/ingestion.py (mocked LLM + save_document)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.database import init_db
from src.medical.confirmation import (
    build_confirmation_message,
    parse_confirmation_reply,
)
from src.medical.entities import (
    add_practice_alias,
    create_practice,
    create_provider,
)
from src.medical.extraction import (
    ExtractedAdjudication,
    ExtractedClaim,
    ExtractedPractice,
    ExtractedProvider,
    ExtractionResult,
    extract_from_file,
)
from src.medical.ingestion import _pending_confirmations, ingest_document
from src.medical.matching import match_claim, match_practice, match_provider


@pytest.fixture
def db_path(tmp_path):
    """Create an isolated SQLite DB initialized with init_db()."""
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


@pytest.fixture(autouse=True)
def _clear_pending_state():
    """Ensure module-level confirmation state is empty between tests."""
    _pending_confirmations.clear()
    yield
    _pending_confirmations.clear()


# ---------------------------------------------------------------------------
# match_practice
# ---------------------------------------------------------------------------

def test_match_practice_exact_name(db_path):
    """Exact case-insensitive name hit returns the practice dict."""
    created = create_practice(db_path, "Manhattan Pain Medicine")
    result = match_practice(db_path, "Manhattan Pain Medicine")
    assert result is not None
    assert result["id"] == created["id"]
    assert result["name"] == "Manhattan Pain Medicine"


def test_match_practice_alias_hit(db_path):
    """A registered alias resolves to the canonical practice row."""
    created = create_practice(db_path, "Manhattan Pain Medicine")
    add_practice_alias(db_path, created["id"], "MPM")

    result = match_practice(db_path, "MPM")
    assert result is not None
    assert result["id"] == created["id"]


def test_match_practice_fuzzy_hit(db_path):
    """A near-match name (score >= 85) returns the practice dict."""
    created = create_practice(db_path, "Manhattan Pain Medicine")

    # Very close: missing one letter, extra space
    result = match_practice(db_path, "Manhattan Pain Medicin")
    assert result is not None
    assert result["id"] == created["id"]


def test_match_practice_fuzzy_miss(db_path):
    """A garbage / unrelated query returns None."""
    create_practice(db_path, "Manhattan Pain Medicine")

    result = match_practice(db_path, "Banana Truck Repair Co.")
    assert result is None


# ---------------------------------------------------------------------------
# match_provider
# ---------------------------------------------------------------------------

def test_match_provider_exact_name(db_path):
    """Exact provider name returns the provider dict."""
    created = create_provider(db_path, "Dr. Siefferman")

    result = match_provider(db_path, "Dr. Siefferman")
    assert result is not None
    assert result["id"] == created["id"]


def test_match_provider_returns_none_for_unknown(db_path):
    """An unrelated provider name returns None (no fuzzy hit at score >= 85)."""
    create_provider(db_path, "Dr. Siefferman")

    result = match_provider(db_path, "Banana Truck Driver")
    assert result is None


# ---------------------------------------------------------------------------
# match_claim
# ---------------------------------------------------------------------------

def test_match_claim_found(db_path):
    """match_claim delegates to find_by_match_key and returns the existing claim."""
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    from src.medical.claims import create_claim
    created = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice["id"],
        billed_amount=250.00,
    )
    assert created is not None

    result = match_claim(db_path, "2025-09-23", practice["id"], 250.00)
    assert result is not None
    assert result["id"] == created["id"]
    assert result["service_date"] == "2025-09-23"


def test_match_claim_not_found(db_path):
    """match_claim returns None when no claim has this match-key."""
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    result = match_claim(db_path, "2099-01-01", practice["id"], 9999.99)
    assert result is None


# ---------------------------------------------------------------------------
# build_confirmation_message
# ---------------------------------------------------------------------------

def test_build_confirmation_message_eob_all_matched():
    """All-matched EOB message contains 'confirm' and the practice name."""
    extraction = ExtractionResult(
        doc_type="eob",
        document_date="2025-11-06",
        practices=[ExtractedPractice(name="Manhattan Pain Medicine")],
        providers=[],
        claims=[
            ExtractedClaim(
                service_date="2025-09-23",
                billed_amount=250.00,
                practice_name="Manhattan Pain Medicine",
            )
        ],
        adjudications=[],
    )
    match_results = {
        "practices": [
            {"name": "Manhattan Pain Medicine", "matched": True, "practice_id": 1}
        ],
        "claims": [
            {"service_date": "2025-09-23", "matched": True, "claim_id": 1}
        ],
    }

    message = build_confirmation_message(extraction, match_results)
    assert "confirm" in message.lower()
    assert "Manhattan Pain Medicine" in message
    assert "EOB" in message


def test_build_confirmation_message_unmatched_practice():
    """Unmatched practice surfaces a 'not recognized' line in the action section."""
    extraction = ExtractionResult(
        doc_type="statement",
        document_date="2025-11-06",
        practices=[ExtractedPractice(name="Unknown Practice")],
        providers=[],
        claims=[
            ExtractedClaim(
                service_date="2025-09-23",
                billed_amount=250.00,
                practice_name="Unknown Practice",
            )
        ],
        adjudications=[],
    )
    match_results = {
        "practices": [
            {"name": "Unknown Practice", "matched": False, "practice_id": None}
        ],
        "claims": [
            {"service_date": "2025-09-23", "matched": False, "claim_id": None}
        ],
    }

    message = build_confirmation_message(extraction, match_results)
    assert "Unknown Practice" in message
    assert "not recognized" in message
    assert "Action required" in message


# ---------------------------------------------------------------------------
# parse_confirmation_reply
# ---------------------------------------------------------------------------

def test_parse_confirmation_reply_yes():
    """A bare 'yes' is treated as confirmation."""
    result = parse_confirmation_reply("yes", [])
    assert result == {"action": "confirm"}


def test_parse_confirmation_reply_confirm():
    """'confirm' (case-insensitive) is treated as confirmation."""
    result = parse_confirmation_reply("Confirm", [])
    assert result == {"action": "confirm"}


def test_parse_confirmation_reply_numbered_correction():
    """A numbered reply is parsed as a correction action with index + text."""
    result = parse_confirmation_reply("2 wrong amount, should be 300", [])
    assert result["action"] == "correction"
    assert result["item_index"] == 2
    assert "wrong amount" in result["correction_text"]


def test_parse_confirmation_reply_free_text():
    """A free-text reply that doesn't match confirm/numbered patterns falls through."""
    result = parse_confirmation_reply("what about the deductible", [])
    assert result["action"] == "free_text"
    assert result["text"] == "what about the deductible"


# ---------------------------------------------------------------------------
# Integration: ingest_document with mocked LLM + save_document
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_document_eob_full_pipeline(db_path, tmp_path):
    """
    End-to-end: mocked extract + saved document -> _pending_confirmations is
    populated and the returned message contains the practice name.
    """
    practice = create_practice(db_path, "Manhattan Pain Medicine")

    fake_extraction = ExtractionResult(
        doc_type="eob",
        document_date="2025-11-06",
        practices=[ExtractedPractice(name="Manhattan Pain Medicine")],
        providers=[ExtractedProvider(name="Dr. Siefferman")],
        claims=[
            ExtractedClaim(
                service_date="2025-09-23",
                billed_amount=250.00,
                practice_name="Manhattan Pain Medicine",
                provider_name="Dr. Siefferman",
            )
        ],
        adjudications=[
            ExtractedAdjudication(
                adjudication_date="2025-11-06",
                allowed_amount=200.00,
                plan_paid=160.00,
                member_owed=40.00,
            )
        ],
    )

    saved_doc_row = {
        "id": 1,
        "chat_id": 12345,
        "file_path": str(tmp_path / "fake.pdf"),
        "original_name": "eob.pdf",
        "mime_type": "application/pdf",
        "doc_type": "other",
        "document_date": None,
        "notes": None,
        "created_at": "2025-11-06 00:00:00",
    }

    mock_context = MagicMock()
    mock_context.job_queue = MagicMock()
    mock_context.job_queue.run_once = MagicMock()
    mock_context.bot = MagicMock()
    mock_context.bot.send_message = AsyncMock()

    with patch(
        "src.medical.ingestion.save_document", return_value=saved_doc_row
    ), patch(
        "src.medical.ingestion.extract_from_file", return_value=fake_extraction
    ):
        message = await ingest_document(
            db_path,
            str(tmp_path / "documents"),
            12345,
            b"fake pdf bytes",
            "eob.pdf",
            "application/pdf",
            mock_context,
        )

    assert "Manhattan Pain Medicine" in message
    assert 12345 in _pending_confirmations
    pending = _pending_confirmations[12345]
    assert pending["document_id"] == 1
    assert pending["extraction"].doc_type == "eob"
    # The practice should have been resolved and present in the cache
    assert pending["practice_id_by_name"]["Manhattan Pain Medicine"] == practice["id"]
    # TTL expiry was scheduled
    mock_context.job_queue.run_once.assert_called_once()


# ---------------------------------------------------------------------------
# Phase 9: scanned-PDF rasterization + multi-image album paths
# ---------------------------------------------------------------------------

# Minimal JSON that validates as an ExtractionResult, used for all Phase 9
# LLM-response mocks.
_VALID_EXTRACTION_JSON = (
    '{"doc_type": "statement", "document_date": null, "practices": [], '
    '"providers": [], "claims": [], "adjudications": [], "raw_text": null}'
)


def _mock_settings() -> MagicMock:
    """Settings stub avoiding real env / OpenRouter coupling."""
    return MagicMock(
        OPENROUTER_API_KEY="k",
        OPENROUTER_BASE_URL="https://x",
        VISION_MODEL="m",
    )


def _mock_openai_client() -> MagicMock:
    """OpenAI class mock whose client returns a valid ExtractionResult JSON."""
    mock_openai = MagicMock()
    completion = MagicMock()
    completion.choices[0].message.content = _VALID_EXTRACTION_JSON
    mock_openai.return_value.chat.completions.create.return_value = completion
    return mock_openai


def test_sparse_pdf_triggers_rasterization():
    """A sparse 1-page PDF routes through _rasterize_pdf into a vision call."""
    with patch(
        "src.medical.extraction.get_settings", return_value=_mock_settings()
    ), patch(
        "src.medical.extraction.OpenAI", _mock_openai_client()
    ), patch(
        "src.medical.extraction._extract_pdf_text", return_value=("", [""])
    ), patch(
        "src.medical.extraction._rasterize_pdf",
        return_value=[("abc123", "image/jpeg")],
    ) as mock_rasterize:
        result = extract_from_file("/tmp/x.pdf", "application/pdf")

    assert result is not None
    mock_rasterize.assert_called_once()


def test_dense_pdf_skips_rasterization():
    """A dense PDF uses the text path and never rasterizes."""
    with patch(
        "src.medical.extraction.get_settings", return_value=_mock_settings()
    ), patch(
        "src.medical.extraction.OpenAI", _mock_openai_client()
    ), patch(
        "src.medical.extraction._extract_pdf_text",
        return_value=("x" * 500, ["x" * 500]),
    ), patch(
        "src.medical.extraction._rasterize_pdf"
    ) as mock_rasterize:
        extract_from_file("/tmp/x.pdf", "application/pdf")

    mock_rasterize.assert_not_called()


def test_sparse_pdf_threshold_scales_with_page_count():
    """250 chars across 3 pages is below 100*3=300, so rasterization triggers."""
    with patch(
        "src.medical.extraction.get_settings", return_value=_mock_settings()
    ), patch(
        "src.medical.extraction.OpenAI", _mock_openai_client()
    ), patch(
        "src.medical.extraction._extract_pdf_text",
        return_value=("a" * 250, ["a" * 83, "a" * 83, "a" * 84]),
    ), patch(
        "src.medical.extraction._rasterize_pdf",
        return_value=[("abc", "image/jpeg")],
    ) as mock_rasterize:
        extract_from_file("/tmp/x.pdf", "application/pdf")

    mock_rasterize.assert_called_once()


def test_multi_image_album_produces_single_extraction(tmp_path):
    """A primary image plus extra_image_bytes completes via the multi-image path."""
    jpg_path = tmp_path / "img.jpg"
    jpg_path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)

    with patch(
        "src.medical.extraction.get_settings", return_value=_mock_settings()
    ), patch(
        "src.medical.extraction.OpenAI", _mock_openai_client()
    ):
        result = extract_from_file(
            str(jpg_path),
            "image/jpeg",
            extra_image_bytes=[
                b"\xff\xd8\xff" + b"\x00" * 10,
                b"\xff\xd8\xff" + b"\x00" * 10,
            ],
        )

    assert result is not None


# ---------------------------------------------------------------------------
# Phase 11: document layout learning (src/medical/layout.py + extraction hook)
# ---------------------------------------------------------------------------

from src.medical.layout import (  # noqa: E402
    detect_relevant_pages,
    load_template,
    score_page_relevance,
    update_template,
)


def test_score_page_relevance_blank():
    """An empty page scores 0.0 (no ZeroDivisionError)."""
    assert score_page_relevance("") == 0.0


def test_score_page_relevance_dense():
    """A page of 100 non-whitespace chars scores above 0.5."""
    assert score_page_relevance("x" * 100) > 0.5


def test_detect_relevant_pages_strips_blanks():
    """Leading and trailing blank pages are stripped from the relevant span."""
    pages = ["", "content here with many chars", "more content", ""]
    assert detect_relevant_pages(pages) == [1, 2]


def test_detect_relevant_pages_all_blank_returns_empty():
    """An all-blank document yields no relevant pages."""
    assert detect_relevant_pages(["", "", ""]) == []


def test_update_template_union_expands_on_second_call(tmp_path):
    """Two updates with overlapping page sets persist their sorted union."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    update_template(db_path, "eob", None, [0, 1])
    update_template(db_path, "eob", None, [1, 2])

    assert load_template(db_path, "eob", None) == [0, 1, 2]


def _capturing_openai_client() -> tuple[MagicMock, dict]:
    """
    OpenAI class mock that records the `messages` payload of the LLM call so
    tests can assert which page text reached the model. Returns
    (mock_openai, captured) where captured['messages'] holds the last payload.
    """
    captured: dict = {}

    def _create(*args, **kwargs):
        captured["messages"] = kwargs["messages"]
        completion = MagicMock()
        completion.choices[0].message.content = _VALID_EXTRACTION_JSON
        return completion

    mock_openai = MagicMock()
    mock_openai.return_value.chat.completions.create.side_effect = _create
    return mock_openai, captured


def test_extraction_uses_stored_range():
    """A stored template restricts the LLM payload to the learned pages."""
    mock_openai, captured = _capturing_openai_client()
    pages = ["PAGE_ZERO_TEXT " * 40, "PAGE_ONE_TEXT " * 40]
    full_text = "\n".join(pages)

    with patch(
        "src.medical.extraction.get_settings", return_value=_mock_settings()
    ), patch(
        "src.medical.extraction.OpenAI", mock_openai
    ), patch(
        "src.medical.extraction._extract_pdf_text",
        return_value=(full_text, pages),
    ), patch(
        "src.medical.extraction.load_template", return_value=[1]
    ):
        result = extract_from_file("/tmp/x.pdf", "application/pdf", db_path="fake")

    assert result is not None
    payload = str(captured["messages"])
    assert "PAGE_ONE_TEXT" in payload
    assert "PAGE_ZERO_TEXT" not in payload


def test_extraction_stores_template_on_first_call():
    """With no stored template, extraction learns and persists one."""
    mock_openai, _captured = _capturing_openai_client()
    pages = ["PAGE_ZERO_TEXT " * 40, "PAGE_ONE_TEXT " * 40]
    full_text = "\n".join(pages)

    with patch(
        "src.medical.extraction.get_settings", return_value=_mock_settings()
    ), patch(
        "src.medical.extraction.OpenAI", mock_openai
    ), patch(
        "src.medical.extraction._extract_pdf_text",
        return_value=(full_text, pages),
    ), patch(
        "src.medical.extraction.load_template", return_value=None
    ), patch(
        "src.medical.extraction.update_template"
    ) as mock_update:
        extract_from_file("/tmp/x.pdf", "application/pdf", db_path="fake")

    mock_update.assert_called_once()


def test_extraction_skips_layout_when_no_db_path():
    """Without db_path, no layout template is consulted."""
    mock_openai, _captured = _capturing_openai_client()
    pages = ["PAGE_ZERO_TEXT " * 40, "PAGE_ONE_TEXT " * 40]
    full_text = "\n".join(pages)

    with patch(
        "src.medical.extraction.get_settings", return_value=_mock_settings()
    ), patch(
        "src.medical.extraction.OpenAI", mock_openai
    ), patch(
        "src.medical.extraction._extract_pdf_text",
        return_value=(full_text, pages),
    ), patch(
        "src.medical.extraction.load_template"
    ) as mock_load:
        extract_from_file("/tmp/x.pdf", "application/pdf")

    mock_load.assert_not_called()


def test_extraction_stored_indices_out_of_bounds_falls_back():
    """Stored indices beyond the page count fall back to all pages, no IndexError."""
    mock_openai, captured = _capturing_openai_client()
    pages = ["PAGE_ZERO_TEXT " * 40, "PAGE_ONE_TEXT " * 40]
    full_text = "\n".join(pages)

    with patch(
        "src.medical.extraction.get_settings", return_value=_mock_settings()
    ), patch(
        "src.medical.extraction.OpenAI", mock_openai
    ), patch(
        "src.medical.extraction._extract_pdf_text",
        return_value=(full_text, pages),
    ), patch(
        "src.medical.extraction.load_template", return_value=[10, 11]
    ):
        result = extract_from_file("/tmp/x.pdf", "application/pdf", db_path="fake")

    assert result is not None
    payload = str(captured["messages"])
    assert "PAGE_ZERO_TEXT" in payload
    assert "PAGE_ONE_TEXT" in payload
