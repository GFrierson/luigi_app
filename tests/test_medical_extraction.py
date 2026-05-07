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
