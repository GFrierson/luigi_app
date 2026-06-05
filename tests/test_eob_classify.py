"""
Tests for src/medical/eob/classify.py.

PDFs are fabricated in-process via fitz (text/garbage layers) and Pillow +
fitz (image-only). No fixtures, no asyncio — all sync.
"""

import io

import fitz

from src.medical.eob.classify import (
    MIN_ALPHA_RATIO,
    MIN_USABLE_CHARS,
    _alpha_ratio,
    _has_expected_anchor,
    _page_is_usable,
    classify_pdf,
)
from src.medical.eob.types import PdfKind


def _make_text_pdf() -> bytes:
    """A clean single-page PDF with a real text layer including an anchor phrase."""
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
    """A single-page PDF whose only content is a rasterized image (no text layer)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 300), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), "Anthem EOB rendered as pixels", fill="black")
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")

    doc = fitz.open()
    page = doc.new_page(width=600, height=300)
    page.insert_image(fitz.Rect(0, 0, 600, 300), stream=img_buf.getvalue())
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_garbage_text_pdf() -> bytes:
    """
    A PDF with a real but garbage text layer: high punctuation/digit noise, no
    real words and no anchor, so its alpha ratio falls below MIN_ALPHA_RATIO.
    """
    doc = fitz.open()
    page = doc.new_page()
    garbage = "1@2#3&4*5^7~8`9_0+=<>?!" * 6
    page.insert_text((72, 72), garbage, fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_mixed_pdf() -> bytes:
    """Two pages: page 0 clean text + anchor, page 1 image-only (no text layer)."""
    from PIL import Image, ImageDraw

    doc = fitz.open()
    page0 = doc.new_page()
    page0.insert_text(
        (72, 72),
        "Anthem Explanation of Benefits Claim Number 1234567 Subscriber John Doe",
        fontsize=12,
    )

    img = Image.new("RGB", (600, 300), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), "image only second page", fill="black")
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")
    page1 = doc.new_page(width=600, height=300)
    page1.insert_image(fitz.Rect(0, 0, 600, 300), stream=img_buf.getvalue())

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# classify_pdf
# ---------------------------------------------------------------------------

def test_classify_clean_text_pdf_is_text():
    assert classify_pdf(_make_text_pdf()) == PdfKind.TEXT


def test_classify_image_only_pdf_is_image():
    assert classify_pdf(_make_image_pdf()) == PdfKind.IMAGE


def test_classify_garbage_layer_is_image():
    stream = _make_garbage_text_pdf()
    # Self-validate the fixture: the real text layer must score below threshold.
    with fitz.open(stream=stream, filetype="pdf") as pdf:
        page_text = pdf[0].get_text("text")
    assert _alpha_ratio(page_text) < MIN_ALPHA_RATIO
    assert classify_pdf(stream) == PdfKind.IMAGE


def test_classify_mixed_pdf_is_mixed():
    assert classify_pdf(_make_mixed_pdf()) == PdfKind.MIXED


def test_classify_junk_is_not_pdf():
    assert classify_pdf(b"not a pdf") == PdfKind.NOT_PDF


# ---------------------------------------------------------------------------
# _alpha_ratio
# ---------------------------------------------------------------------------

def test_alpha_ratio_clean_text():
    assert _alpha_ratio("Hello, world! $50.00") > 0.9


def test_alpha_ratio_garbage():
    # `|` is the only illegible char here; digits/./-/ are legible per spec, so
    # the ratio is below clean text but not necessarily below the gate. The gate
    # (test_classify_garbage_layer_is_image) exercises a genuinely low-alpha page.
    garbage = _alpha_ratio("123...///|||---")
    clean = _alpha_ratio("Hello, world! $50.00")
    assert garbage < clean
    assert garbage < 1.0


# ---------------------------------------------------------------------------
# anchor checks
# ---------------------------------------------------------------------------

def test_anchor_check_true():
    assert _has_expected_anchor("This is an ANTHEM document") is True


def test_anchor_check_false():
    assert _has_expected_anchor("a grocery receipt for bananas") is False


def test_anchor_override_rescues_borderline_page():
    # Borderline page: long enough, low alpha ratio, but contains an anchor.
    noisy = "|||---+++===///" * 3  # low alpha
    with_anchor = noisy + " anthem explanation of benefits " + noisy
    assert len(with_anchor.strip()) >= MIN_USABLE_CHARS
    assert _alpha_ratio(with_anchor) < MIN_ALPHA_RATIO
    assert _page_is_usable(with_anchor) is True

    # Same noise, no anchor: the anchor never demotes, low alpha stays unusable.
    no_anchor = noisy + " " + noisy
    assert len(no_anchor.strip()) >= MIN_USABLE_CHARS
    assert _alpha_ratio(no_anchor) < MIN_ALPHA_RATIO
    assert _page_is_usable(no_anchor) is False
