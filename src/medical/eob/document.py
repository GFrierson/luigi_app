"""
Dual-path canonical ``Document`` builder.

A clean text-layer PDF (``from_text_layer``) and an image/garbage PDF routed
through OCR (``from_ocr``) both normalize to the same ``Document`` shape, so
downstream extraction is path-agnostic. ``to_document`` orchestrates routing
via ``classify_pdf`` and raises ``NotAPdf`` only for genuine non-PDF input.

Missing system dependencies degrade gracefully: a missing Tesseract binary
yields an empty IMAGE ``Document`` (mirroring ``_rasterize_pdf``'s missing-
Poppler swallow) rather than raising.
"""

import logging
from typing import Any

from src.medical.eob.classify import classify_pdf
from src.medical.eob.types import Document, PdfKind, Word

logger = logging.getLogger(__name__)


OCR_DPI = 300
IMAGE_DPI = 150


class NotAPdf(Exception):
    pass


def _render_page_png(page: Any, dpi: int) -> bytes:
    """Render a fitz page to PNG bytes at the given DPI, then release the pixmap."""
    import fitz

    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=matrix)
    data = pix.tobytes("png")
    pix = None
    return data


def from_text_layer(stream: bytes) -> Document:
    """Build a Document from a PDF's native text layer (word bboxes + page PNGs)."""
    import fitz

    all_words: list[Word] = []
    page_texts: list[str] = []
    page_images: list[bytes] = []

    with fitz.open(stream=stream, filetype="pdf") as pdf:
        for p, page in enumerate(pdf):
            for w in page.get_text("words"):
                all_words.append(
                    Word(
                        text=w[4],
                        x0=int(w[0]),
                        y0=int(w[1]),
                        x1=int(w[2]),
                        y1=int(w[3]),
                        page=p,
                    )
                )
            page_texts.append(page.get_text("text"))
            page_images.append(_render_page_png(page, IMAGE_DPI))

    return Document(
        text="\n".join(page_texts).strip(),
        words=all_words,
        page_images=page_images,
        source=PdfKind.TEXT,
    )


def from_ocr(stream: bytes, dpi: int = OCR_DPI) -> Document:
    """
    Build a Document by rasterizing each page and running Tesseract OCR.

    A missing Tesseract binary degrades to an empty IMAGE Document rather than
    raising. All token confidence levels are kept for Phase 1 (tunable later).
    """
    import io

    import fitz
    import pytesseract
    from PIL import Image

    all_words: list[Word] = []
    page_texts: list[str] = []
    page_images: list[bytes] = []

    try:
        with fitz.open(stream=stream, filetype="pdf") as pdf:
            for p, page in enumerate(pdf):
                png = _render_page_png(page, dpi)
                page_images.append(png)

                img = Image.open(io.BytesIO(png))
                data = pytesseract.image_to_data(
                    img, output_type=pytesseract.Output.DICT
                )

                tokens: list[str] = []
                for i, raw in enumerate(data["text"]):
                    t = raw.strip()
                    if not t:
                        continue
                    left = int(data["left"][i])
                    top = int(data["top"][i])
                    width = int(data["width"][i])
                    height = int(data["height"][i])
                    all_words.append(
                        Word(
                            text=t,
                            x0=left,
                            y0=top,
                            x1=left + width,
                            y1=top + height,
                            page=p,
                        )
                    )
                    tokens.append(t)
                page_texts.append(" ".join(tokens))
    except pytesseract.TesseractNotFoundError:
        logger.error(
            "from_ocr: Tesseract binary not found; degrading to empty IMAGE "
            "Document. Install tesseract (apt: tesseract-ocr / brew: tesseract).",
            exc_info=True,
        )
        return Document(text="", words=[], page_images=[], source=PdfKind.IMAGE)

    return Document(
        text="\n".join(page_texts).strip(),
        words=all_words,
        page_images=page_images,
        source=PdfKind.IMAGE,
    )


def to_document(stream: bytes) -> Document:
    """
    Route a PDF byte stream to the appropriate builder and return a Document.

    NOT_PDF -> raise NotAPdf. TEXT -> from_text_layer. IMAGE/MIXED -> from_ocr
    (MIXED is treated as IMAGE; from_ocr already stamps source=IMAGE).
    """
    kind = classify_pdf(stream)
    if kind is PdfKind.NOT_PDF:
        raise NotAPdf("stream is not a parseable PDF")
    if kind is PdfKind.TEXT:
        return from_text_layer(stream)
    if kind is PdfKind.MIXED:
        logger.info("to_document: MIXED PDF treated as IMAGE (full-page OCR).")
        return from_ocr(stream)
    return from_ocr(stream)
