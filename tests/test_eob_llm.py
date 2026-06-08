"""
Tests for the Phase 3 EOB LLM fallback path and unknown-issuer logging.

Covers:
    src/medical/eob/pipeline.py  (process_eob llm_override branch, LLM mocked)
    src/medical/eob/corpus.py    (log_unknown against a real SQLite DB)
"""

import sqlite3
from unittest.mock import MagicMock, patch

from src.database import init_db
from src.medical.eob.corpus import log_unknown
from src.medical.eob.pipeline import process_eob
from src.medical.eob.types import Document, Extracted, PdfKind


def _mock_settings() -> MagicMock:
    """Settings stub avoiding real env / OpenRouter coupling."""
    return MagicMock(
        OPENROUTER_API_KEY="k",
        OPENROUTER_BASE_URL="https://x",
        LLM_VISION_MODEL="m",
    )


def _mock_openai_returning(json_content: str) -> MagicMock:
    """OpenAI class mock whose client returns the given JSON string content."""
    mock_openai = MagicMock()
    completion = MagicMock()
    completion.choices[0].message.content = json_content
    mock_openai.return_value.chat.completions.create.return_value = completion
    return mock_openai


def test_llm_override_returns_extracted():
    """An unrecognized-issuer doc with llm_override routes through the LLM fallback."""
    doc = Document(
        text="Member benefit summary from an unrecognized payer.",
        words=[],
        page_images=[b"\x89PNG\r\n\x1a\n" + b"\x00" * 16],
        source=PdfKind.IMAGE,
    )
    llm_json = (
        '{"issuer": "cigna", "subtype": "summary", '
        '"subscriber": "John Doe", "claims": []}'
    )

    with patch(
        "src.medical.eob.extractors.llm.get_settings",
        return_value=_mock_settings(),
    ), patch(
        "src.medical.eob.extractors.llm.OpenAI",
        _mock_openai_returning(llm_json),
    ):
        result = process_eob(doc, llm_override=True)

    assert isinstance(result, Extracted)
    assert result.extractor == "llm"
    assert result.eob.issuer == "cigna"


def test_log_unknown_writes_flag(tmp_path):
    """log_unknown sets documents.notes to the unknown-issuer sentinel and returns the row."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO documents (chat_id, file_path, doc_type)
        VALUES (?, ?, ?)
        """,
        (1, "test.pdf", "eob"),
    )
    document_id = cursor.lastrowid
    conn.commit()
    conn.close()

    returned = log_unknown(document_id, db_path)

    assert returned is not None
    assert returned["notes"] == "eob:unknown_issuer"

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT notes FROM documents WHERE id = ?", (document_id,))
    notes = cursor.fetchone()[0]
    conn.close()

    assert notes == "eob:unknown_issuer"
