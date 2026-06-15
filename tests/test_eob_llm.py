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
from src.medical.eob.extractors.grounding import check_grounding
from src.medical.eob.extractors.llm import LLMVisionExtractor
from src.medical.eob.pipeline import process_eob
from src.medical.eob.types import (
    Document,
    Extracted,
    GroundedField,
    PdfKind,
    Word,
)
from src.medical.eob.validate import validate


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
        '{"issuer": {"field": "issuer", "value": "cigna", "page": 0, '
        '"span": "cigna", "found": true}, '
        '"subtype": {"field": "subtype", "value": "summary", "page": 0, '
        '"span": "summary", "found": true}, '
        '"subscriber": {"field": "subscriber", "value": "John Doe", "page": 0, '
        '"span": "John Doe", "found": true}, '
        '"claims": []}'
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


def _make_doc_with_words(words: list[Word]) -> Document:
    return Document(text="", words=words, page_images=[], source=PdfKind.IMAGE)


def _envelope_with_anthem_paid(value: str, page: int, span: str) -> str:
    """Grounded envelope with one claim and one line item carrying anthem_paid."""
    return (
        '{"issuer": {"field": "issuer", "value": "Anthem", "page": 0, '
        '"span": "Anthem", "found": true}, '
        '"subtype": {"field": "subtype", "value": "summary", "page": 0, '
        '"span": "summary", "found": true}, '
        '"subscriber": {"field": "subscriber", "value": "JANE DOE", "page": 0, '
        '"span": "JANE DOE", "found": true}, '
        '"claims": [{'
        '"patient": {"field": "claims[0].patient", "value": "JANE DOE", '
        '"page": 1, "span": "JANE DOE", "found": true}, '
        '"claim_number": {"field": "claims[0].claim_number", "value": "X1", '
        '"page": 1, "span": "X1", "found": true}, '
        '"received_date": {"field": "claims[0].received_date", "value": null, '
        '"page": null, "span": null, "found": false}, '
        '"provider": {"field": "claims[0].provider", "value": "DR WHO", '
        '"page": 1, "span": "DR WHO", "found": true}, '
        '"in_network": {"field": "claims[0].in_network", "value": "true", '
        '"page": 1, "span": "In Network", "found": true}, '
        '"patient_owes": {"field": "claims[0].patient_owes", "value": "0.00", '
        '"page": 1, "span": "0.00", "found": true}, '
        '"line_items": [{'
        f'"anthem_paid": {{"field": "claims[0].line_items[0].anthem_paid", '
        f'"value": "{value}", "page": {page}, "span": "{span}", "found": true}}'
        "}]"
        "}]}"
    )


def test_absent_field_is_empty_string_and_not_ungrounded():
    """A found=false field yields "" in the EOBDocument and is not flagged ungrounded."""
    doc = _make_doc_with_words([])
    llm_json = (
        '{"issuer": {"field": "issuer", "value": "Anthem", "page": 0, '
        '"span": "Anthem", "found": true}, '
        '"subtype": {"field": "subtype", "value": "summary", "page": 0, '
        '"span": "summary", "found": true}, '
        '"subscriber": {"field": "subscriber", "value": null, "page": null, '
        '"span": null, "found": false}, '
        '"claims": []}'
    )

    with patch(
        "src.medical.eob.extractors.llm.get_settings",
        return_value=_mock_settings(),
    ), patch(
        "src.medical.eob.extractors.llm.OpenAI",
        _mock_openai_returning(llm_json),
    ):
        eob, report = LLMVisionExtractor().extract(doc)

    assert eob.subscriber == ""
    assert "subscriber" not in report.ungrounded


def test_field_on_correct_page_is_grounded():
    """A value whose span appears on the cited page is not flagged ungrounded."""
    doc = _make_doc_with_words(
        [Word(text="207.20", x0=0, y0=0, x1=10, y1=10, page=2)]
    )
    llm_json = _envelope_with_anthem_paid(value="207.20", page=2, span="207.20")

    with patch(
        "src.medical.eob.extractors.llm.get_settings",
        return_value=_mock_settings(),
    ), patch(
        "src.medical.eob.extractors.llm.OpenAI",
        _mock_openai_returning(llm_json),
    ):
        eob, report = LLMVisionExtractor().extract(doc)

    assert eob.claims[0].line_items[0].anthem_paid == "207.20"
    assert "claims[0].line_items[0].anthem_paid" not in report.ungrounded


def test_field_citing_wrong_page_is_flagged_ungrounded():
    """A value citing a page where its span does not appear is flagged ungrounded."""
    doc = _make_doc_with_words(
        [Word(text="207.20", x0=0, y0=0, x1=10, y1=10, page=0)]
    )
    llm_json = _envelope_with_anthem_paid(value="207.20", page=2, span="207.20")

    with patch(
        "src.medical.eob.extractors.llm.get_settings",
        return_value=_mock_settings(),
    ), patch(
        "src.medical.eob.extractors.llm.OpenAI",
        _mock_openai_returning(llm_json),
    ):
        _eob, report = LLMVisionExtractor().extract(doc)

    assert "claims[0].line_items[0].anthem_paid" in report.ungrounded


def test_ungrounded_field_penalizes_confidence():
    """validate with an ungrounded report drops confidence and records an issue."""
    doc = _make_doc_with_words(
        [Word(text="207.20", x0=0, y0=0, x1=10, y1=10, page=0)]
    )
    llm_json = _envelope_with_anthem_paid(value="207.20", page=2, span="207.20")

    with patch(
        "src.medical.eob.extractors.llm.get_settings",
        return_value=_mock_settings(),
    ), patch(
        "src.medical.eob.extractors.llm.OpenAI",
        _mock_openai_returning(llm_json),
    ):
        eob, report = LLMVisionExtractor().extract(doc)

    result = validate(eob, PdfKind.IMAGE, grounding_report=report)

    assert result.confidence < 1.0
    assert any("ungrounded" in i for i in result.issues)


def test_check_grounding_empty_words_flags_found_fields():
    """With no words on the page, a found=True field is ungrounded."""
    fields = [
        GroundedField(field="anthem_paid", value="100", page=0, span="100", found=True)
    ]
    doc = _make_doc_with_words([])
    report = check_grounding(fields, doc)
    assert "anthem_paid" in report.ungrounded


def test_check_grounding_skips_not_found_fields():
    """A found=False field is skipped and never appears in ungrounded."""
    fields = [
        GroundedField(
            field="received_date", value=None, page=None, span=None, found=False
        )
    ]
    doc = _make_doc_with_words([])
    report = check_grounding(fields, doc)
    assert "received_date" not in report.ungrounded
