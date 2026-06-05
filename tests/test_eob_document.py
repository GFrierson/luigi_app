"""
Tests for src/medical/eob/document.py and src/medical/eob/artifacts.py.

PDFs are fabricated in-process via fitz/Pillow. Tesseract is always mocked
(the binary is an external dependency); SQLite is not used here.
"""

import io
from unittest.mock import patch

import fitz
import pytesseract
import pytest

from src.medical.eob.artifacts import detect_artifacts
from src.medical.eob.document import (
    IMAGE_DPI,
    NotAPdf,
    from_ocr,
    from_text_layer,
    to_document,
)
from src.medical.eob.types import Document, PdfKind, Word


# Canned pytesseract.image_to_data DICT: two real tokens + one empty token.
MOCK_TESS_DATA = {
    "text": ["Hello", "world", ""],
    "conf": ["90", "85", "-1"],
    "left": [10, 60, 0],
    "top": [20, 20, 0],
    "width": [40, 50, 0],
    "height": [15, 15, 0],
    "block_num": [1, 1, 0],
    "line_num": [1, 1, 0],
}


def _make_text_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Anthem Explanation of Benefits Claim Number 1234567 Subscriber John Doe",
        fontsize=12,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_image_pdf() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 300), "white")
    ImageDraw.Draw(img).text((20, 20), "rendered as pixels", fill="black")
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")

    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    page.insert_image(fitz.Rect(0, 0, 600, 300), stream=img_buf.getvalue())
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# from_text_layer
# ---------------------------------------------------------------------------

def test_from_text_layer_returns_document():
    doc = from_text_layer(_make_text_pdf())
    assert doc.source == PdfKind.TEXT
    assert len(doc.words) > 0
    assert doc.words[0].page == 0
    assert len(doc.page_images) == 1
    assert doc.page_images[0].startswith(b"\x89PNG")


def test_from_text_layer_word_coords_are_ints():
    doc = from_text_layer(_make_text_pdf())
    for w in doc.words:
        assert type(w.x0) is int
        assert type(w.y0) is int
        assert type(w.x1) is int
        assert type(w.y1) is int
        assert type(w.page) is int


def test_from_text_layer_page_images_correct_dpi():
    from PIL import Image

    stream = _make_text_pdf()
    with fitz.open(stream=stream, filetype="pdf") as pdf:
        page_width_pts = pdf[0].rect.width

    doc = from_text_layer(stream)
    img = Image.open(io.BytesIO(doc.page_images[0]))
    expected_px = page_width_pts * IMAGE_DPI / 72
    assert abs(img.width - expected_px) <= 5


# ---------------------------------------------------------------------------
# from_ocr
# ---------------------------------------------------------------------------

def test_from_ocr_with_mocked_tesseract():
    with patch("pytesseract.image_to_data", return_value=MOCK_TESS_DATA):
        doc = from_ocr(_make_image_pdf())
    assert doc.source == PdfKind.IMAGE
    assert len(doc.words) == 2
    assert doc.words[0].x1 == 50  # 10 + 40
    assert doc.words[0].y1 == 35  # 20 + 15
    assert doc.words[0].page == 0


def test_from_ocr_tesseract_not_found_degrades_gracefully():
    with patch(
        "pytesseract.image_to_data",
        side_effect=pytesseract.TesseractNotFoundError(),
    ):
        doc = from_ocr(_make_image_pdf())
    assert doc.source == PdfKind.IMAGE
    assert doc.text == ""
    assert doc.words == []


# ---------------------------------------------------------------------------
# to_document
# ---------------------------------------------------------------------------

def test_to_document_routes_text_pdf():
    doc = to_document(_make_text_pdf())
    assert doc.source == PdfKind.TEXT


def test_to_document_routes_image_pdf():
    with patch("pytesseract.image_to_data", return_value=MOCK_TESS_DATA):
        doc = to_document(_make_image_pdf())
    assert doc.source == PdfKind.IMAGE


def test_to_document_raises_not_a_pdf():
    with pytest.raises(NotAPdf):
        to_document(b"not a pdf")


def test_text_and_ocr_documents_identical_shape():
    doc_text = from_text_layer(_make_text_pdf())
    with patch("pytesseract.image_to_data", return_value=MOCK_TESS_DATA):
        doc_ocr = from_ocr(_make_image_pdf())

    assert type(doc_text.text) is str and type(doc_ocr.text) is str
    assert type(doc_text.words) is list and type(doc_ocr.words) is list
    assert type(doc_text.page_images) is list and type(doc_ocr.page_images) is list
    for img in doc_text.page_images + doc_ocr.page_images:
        assert type(img) is bytes
    # They differ only in source.
    assert doc_text.source == PdfKind.TEXT
    assert doc_ocr.source == PdfKind.IMAGE


# ---------------------------------------------------------------------------
# detect_artifacts
# ---------------------------------------------------------------------------

def test_detect_artifacts_check_flag():
    doc = Document(
        text="pay to the order of John Doe",
        words=[],
        page_images=[],
        source=PdfKind.TEXT,
    )
    assert "check" in detect_artifacts(doc)


def test_detect_artifacts_eop_flag():
    doc = Document(
        text="this page is a remittance advice statement",
        words=[],
        page_images=[],
        source=PdfKind.TEXT,
    )
    assert "eop" in detect_artifacts(doc)


def test_detect_artifacts_ach_flag():
    doc = Document(
        text="payment sent via trace number 998877",
        words=[],
        page_images=[],
        source=PdfKind.TEXT,
    )
    assert "ach" in detect_artifacts(doc)


def test_detect_artifacts_out_of_order_flag():
    words = [
        Word(text="page", x0=0, y0=0, x1=1, y1=1, page=0),
        Word(text="2", x0=0, y0=0, x1=1, y1=1, page=0),
        Word(text="of", x0=0, y0=0, x1=1, y1=1, page=0),
        Word(text="3", x0=0, y0=0, x1=1, y1=1, page=0),
        Word(text="page", x0=0, y0=0, x1=1, y1=1, page=1),
        Word(text="1", x0=0, y0=0, x1=1, y1=1, page=1),
        Word(text="of", x0=0, y0=0, x1=1, y1=1, page=1),
        Word(text="3", x0=0, y0=0, x1=1, y1=1, page=1),
    ]
    doc = Document(text="", words=words, page_images=[], source=PdfKind.TEXT)
    assert "out_of_order" in detect_artifacts(doc)


def test_detect_artifacts_multi_doc_flag():
    doc = Document(
        text="page 1 of 5\nsome body text\npage 1 of 3",
        words=[],
        page_images=[],
        source=PdfKind.TEXT,
    )
    assert "multi_doc" in detect_artifacts(doc)


def test_detect_artifacts_clean_eob_no_flags():
    doc = Document(
        text="Anthem Explanation of Benefits claim number 12345",
        words=[],
        page_images=[],
        source=PdfKind.TEXT,
    )
    assert detect_artifacts(doc) == []


def test_detect_artifacts_never_raises():
    doc = Document(text="", words=[], page_images=[], source=PdfKind.TEXT)
    result = detect_artifacts(doc)
    assert isinstance(result, list)
