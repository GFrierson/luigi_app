"""
Tests for the Phase 5 EOB Telegram vertical slice in src/telegram_handler.py.

Covers the _on_document EOB branch (confirm / consent / low-confidence / fall-
through) and the _on_message routing for confirm/cancel and consent yes/no.

External services are mocked: to_document / process_eob / detect_artifacts /
commit_eob_ingestion / ingest_document and the Telegram bot. SQLite is never
mocked, but these handler tests never touch a real DB write since the commit
path is mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.medical.ingestion import _pending_confirmations
from src.medical.eob.types import (
    Claim,
    Document,
    EOBDocument,
    Extracted,
    PdfKind,
    Unreadable,
    UnknownType,
    ValidationResult,
)
from src.telegram_handler import _on_document, _on_message


@pytest.fixture(autouse=True)
def clear_pending():
    _pending_confirmations.clear()
    yield
    _pending_confirmations.clear()


@pytest.fixture(autouse=True)
def mock_settings(tmp_path):
    """Point DATABASE_DIR / DOCUMENTS_DIR at a tmp dir for every test."""
    settings = MagicMock()
    settings.DATABASE_DIR = str(tmp_path / "db")
    settings.DOCUMENTS_DIR = str(tmp_path / "docs")
    with patch("src.telegram_handler.get_settings", return_value=settings), \
         patch("src.telegram_handler.init_db"):
        yield settings


def _make_extracted(
    *, subtype: str = "summary", ok: bool = True, n_claims: int = 1
) -> Extracted:
    claims = [
        Claim(
            patient="Jane Doe",
            claim_number=f"CLM00{i}",
            received_date="2025-10-05",
            provider="Dr. Smith",
            in_network=True,
            patient_owes="40.00",
            line_items=[],
        )
        for i in range(n_claims)
    ]
    eob = EOBDocument(
        issuer="anthem", subtype=subtype, subscriber="Jane Doe", claims=claims
    )
    validation = ValidationResult(ok=ok, confidence=0.95, issues=[])
    return Extracted(eob=eob, validation=validation, extractor="anthem")


def _make_document() -> Document:
    return Document(
        text="Anthem EOB", words=[], page_images=[b"png"], source=PdfKind.IMAGE
    )


def _make_doc_update(chat_id: int, *, mime_type: str, file_name: str) -> MagicMock:
    doc = MagicMock()
    doc.file_size = 1000
    doc.file_id = "fid"
    doc.file_name = file_name
    doc.mime_type = mime_type
    message = MagicMock()
    message.chat_id = chat_id
    message.document = doc
    update = MagicMock()
    update.message = message
    return update


def _make_context() -> MagicMock:
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    tg_file = MagicMock()
    tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"%PDF-fake"))
    context.bot.get_file = AsyncMock(return_value=tg_file)
    context.job_queue = None
    return context


def _make_text_update(chat_id: int, text: str) -> MagicMock:
    from_user = MagicMock()
    from_user.first_name = "Jane"
    message = MagicMock()
    message.chat_id = chat_id
    message.text = text
    message.message_id = 1
    message.from_user = from_user
    update = MagicMock()
    update.message = message
    return update


# ---------------------------------------------------------------------------
# 1. PDF with Anthem EOB stores pending confirm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_document_pdf_anthem_stores_pending_confirm():
    chat_id = 111
    update = _make_doc_update(chat_id, mime_type="application/pdf", file_name="eob.pdf")
    context = _make_context()
    extracted = _make_extracted(ok=True)

    with patch("src.telegram_handler.to_document", return_value=_make_document()), \
         patch("src.telegram_handler.detect_artifacts", return_value=[]), \
         patch("src.telegram_handler.process_eob", return_value=extracted):
        await _on_document(update, context)

    pending = _pending_confirmations.get(chat_id)
    assert pending is not None
    assert pending["kind"] == "eob"
    context.bot.send_message.assert_awaited_once()
    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert "EOB received" in sent_text
    assert "Reply confirm to save" in sent_text


# ---------------------------------------------------------------------------
# 2. confirm saves and bridges
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirm_eob_saves_and_bridges():
    chat_id = 222
    extracted = _make_extracted(ok=True)
    _pending_confirmations[chat_id] = {
        "kind": "eob",
        "result": extracted,
        "artifacts": [],
        "source": PdfKind.IMAGE,
        "file_bytes": b"%PDF",
        "original_name": "eob.pdf",
        "mime_type": "application/pdf",
        "unknown_consented": False,
    }
    update = _make_text_update(chat_id, "confirm")
    context = _make_context()

    with patch(
        "src.telegram_handler.commit_eob_ingestion", new_callable=AsyncMock
    ) as mock_commit:
        await _on_message(update, context)

    mock_commit.assert_awaited_once()
    assert chat_id not in _pending_confirmations
    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert sent_text == "EOB saved."


# ---------------------------------------------------------------------------
# 3. cancel clears pending, no DB writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_eob_clears_pending():
    chat_id = 333
    extracted = _make_extracted(ok=True)
    _pending_confirmations[chat_id] = {
        "kind": "eob",
        "result": extracted,
        "artifacts": [],
        "source": PdfKind.IMAGE,
        "file_bytes": b"%PDF",
        "original_name": "eob.pdf",
        "mime_type": "application/pdf",
        "unknown_consented": False,
    }
    update = _make_text_update(chat_id, "cancel")
    context = _make_context()

    with patch(
        "src.telegram_handler.commit_eob_ingestion", new_callable=AsyncMock
    ) as mock_commit:
        await _on_message(update, context)

    mock_commit.assert_not_awaited()
    assert chat_id not in _pending_confirmations
    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert sent_text == "Cancelled."


# ---------------------------------------------------------------------------
# 4. unknown issuer shows consent prompt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_document_unknown_issuer_shows_consent_prompt():
    chat_id = 444
    update = _make_doc_update(chat_id, mime_type="application/pdf", file_name="x.pdf")
    context = _make_context()
    doc = _make_document()

    with patch("src.telegram_handler.to_document", return_value=doc), \
         patch("src.telegram_handler.detect_artifacts", return_value=[]), \
         patch("src.telegram_handler.process_eob", return_value=UnknownType(doc)):
        await _on_document(update, context)

    pending = _pending_confirmations.get(chat_id)
    assert pending is not None
    assert pending["kind"] == "eob_consent"
    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert "AI vision" in sent_text


# ---------------------------------------------------------------------------
# 5. consent yes triggers LLM then a new confirm pending
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consent_yes_triggers_llm_then_confirm():
    chat_id = 555
    doc = _make_document()
    _pending_confirmations[chat_id] = {
        "kind": "eob_consent",
        "doc": doc,
        "artifacts": ["check"],
        "file_bytes": b"%PDF",
        "original_name": "x.pdf",
        "mime_type": "application/pdf",
    }
    update = _make_text_update(chat_id, "yes")
    context = _make_context()
    llm_extracted = _make_extracted(ok=True)

    with patch("src.telegram_handler.process_eob", return_value=llm_extracted):
        await _on_message(update, context)

    pending = _pending_confirmations.get(chat_id)
    assert pending is not None
    assert pending["kind"] == "eob"
    assert pending["unknown_consented"] is True
    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert "EOB received" in sent_text


# ---------------------------------------------------------------------------
# 6. consent no falls through to ingest_document
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consent_no_falls_through_to_ingest_document():
    chat_id = 666
    doc = _make_document()
    _pending_confirmations[chat_id] = {
        "kind": "eob_consent",
        "doc": doc,
        "artifacts": [],
        "file_bytes": b"%PDF",
        "original_name": "x.pdf",
        "mime_type": "application/pdf",
    }
    update = _make_text_update(chat_id, "no")
    context = _make_context()

    with patch(
        "src.telegram_handler.ingest_document",
        new_callable=AsyncMock,
        return_value="Saved as document.",
    ) as mock_ingest:
        await _on_message(update, context)

    mock_ingest.assert_awaited_once()
    assert chat_id not in _pending_confirmations
    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert sent_text == "Saved as document."


# ---------------------------------------------------------------------------
# 7. artifact flag surfaced in confirm message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_artifact_flag_in_confirm_message():
    chat_id = 777
    update = _make_doc_update(chat_id, mime_type="application/pdf", file_name="eob.pdf")
    context = _make_context()
    extracted = _make_extracted(ok=True)

    with patch("src.telegram_handler.to_document", return_value=_make_document()), \
         patch("src.telegram_handler.detect_artifacts", return_value=["check"]), \
         patch("src.telegram_handler.process_eob", return_value=extracted):
        await _on_document(update, context)

    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert "check" in sent_text


# ---------------------------------------------------------------------------
# 8. Unreadable falls through to ingest_document
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unreadable_falls_through_to_ingest_document():
    chat_id = 888
    update = _make_doc_update(chat_id, mime_type="application/pdf", file_name="eob.pdf")
    context = _make_context()

    with patch("src.telegram_handler.to_document", return_value=_make_document()), \
         patch("src.telegram_handler.detect_artifacts", return_value=[]), \
         patch("src.telegram_handler.process_eob", return_value=Unreadable("no text")), \
         patch(
             "src.telegram_handler.ingest_document",
             new_callable=AsyncMock,
             return_value="Saved.",
         ) as mock_ingest:
        await _on_document(update, context)

    mock_ingest.assert_awaited_once()
    assert chat_id not in _pending_confirmations


# ---------------------------------------------------------------------------
# 9. non-PDF (to_document raises NotAPdf) falls through to ingest_document
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_not_pdf_falls_through_to_ingest_document():
    from src.medical.eob.document import NotAPdf

    chat_id = 999
    update = _make_doc_update(chat_id, mime_type="application/pdf", file_name="eob.pdf")
    context = _make_context()

    with patch("src.telegram_handler.to_document", side_effect=NotAPdf("nope")), \
         patch("src.telegram_handler.detect_artifacts", return_value=[]), \
         patch(
             "src.telegram_handler.ingest_document",
             new_callable=AsyncMock,
             return_value="Saved.",
         ) as mock_ingest:
        await _on_document(update, context)

    mock_ingest.assert_awaited_once()
    assert chat_id not in _pending_confirmations


# ---------------------------------------------------------------------------
# 10. low-confidence EOB sends resend message, no pending
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_low_confidence_eob_sends_resend_message():
    chat_id = 1010
    update = _make_doc_update(chat_id, mime_type="application/pdf", file_name="eob.pdf")
    context = _make_context()
    extracted = _make_extracted(ok=False)

    with patch("src.telegram_handler.to_document", return_value=_make_document()), \
         patch("src.telegram_handler.detect_artifacts", return_value=[]), \
         patch("src.telegram_handler.process_eob", return_value=extracted), \
         patch(
             "src.telegram_handler.ingest_document", new_callable=AsyncMock
         ) as mock_ingest:
        await _on_document(update, context)

    assert chat_id not in _pending_confirmations
    mock_ingest.assert_not_awaited()
    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert "re-sending a clearer scan" in sent_text
