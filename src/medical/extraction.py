"""
Vision-based extraction for medical documents (Phase 4).

Pipeline:
    file_bytes -> doc_type detection -> prompt selection -> LLM call ->
    ExtractionResult (Pydantic-validated)

Three layers in this module:
    1. Pydantic output schemas (ExtractionResult and friends)
    2. Per-doc-type prompts (STATEMENT_PROMPT, EOB_PROMPT, RECEIPT_PROMPT)
    3. extract_from_file(file_path, mime_type) entry point

`extract_from_file` is SYNCHRONOUS — callers (async ingestion code) should
wrap it in `asyncio.to_thread()`.

Design notes:
    - PDFs are processed via pypdf text extraction. Scanned PDFs that produce
      sparse text (below SPARSE_TEXT_THRESHOLD chars/page) are rasterized via
      `pdf2image.convert_from_path()` and sent to the vision model as a
      multi-image payload (Phase 9). `pdf2image` requires a system-level
      Poppler install (apt: poppler-utils / brew: poppler).
    - Images (JPEG/PNG) are sent as base64-encoded image_url parts to the
      OpenRouter vision endpoint. Multi-photo albums are packed into a single
      vision call via _build_multi_image_message (Phase 9).
    - All exceptions are caught at the boundary; the function returns None
      on any failure and logs with exc_info=True.
"""

import base64
import importlib
import io
import logging
import os
from typing import Literal, Optional

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader

from src.config import get_settings
from src.medical.eob.anchors import _INSURER_PHRASE_MAP
from src.medical.extractors.allowlist import EXTRACTOR_ALLOWLIST
from src.medical.layout import detect_relevant_pages, load_template, update_template

logger = logging.getLogger(__name__)


# Coarse insurer detection for deterministic-extractor dispatch (Phase 13).
# Valid insurer keys are the right-hand values here; allowlist entries must
# reference one of them. The phrase map now lives in src/medical/eob/anchors.py
# so it is shared with the EOB classifier's anchor-rescue gate.


def _detect_insurer(text: str) -> Optional[str]:
    """
    Return the insurer key whose phrase appears in `text`, or None.

    Never raises — returns None on any error.
    """
    try:
        lowered = text.lower()
        for phrase, insurer in _INSURER_PHRASE_MAP:
            if phrase in lowered:
                return insurer
        return None
    except Exception:
        logger.error("_detect_insurer: unexpected failure", exc_info=True)
        return None

# Below this many characters of extracted text *per page*, a PDF is treated as
# scanned/sparse and routed to the rasterization (vision) path instead of text.
SPARSE_TEXT_THRESHOLD = 100

# Cap on images packed into a single multi-image vision call. Enforced by the
# album caller (_flush_photo_group); extract_from_file trusts the caller.
MAX_ALBUM_IMAGES = 6


# ---------------------------------------------------------------------------
# Layer 1: Pydantic output schemas
# ---------------------------------------------------------------------------

class ExtractedPractice(BaseModel):
    name: str
    aliases: list[str] = []


class ExtractedProvider(BaseModel):
    name: str
    aliases: list[str] = []


class ExtractedClaim(BaseModel):
    service_date: str  # ISO-8601 YYYY-MM-DD
    billed_amount: float
    practice_name: str
    provider_name: Optional[str] = None
    external_ids: list[dict] = []


class ExtractedAdjudication(BaseModel):
    adjudication_date: str
    allowed_amount: Optional[float] = None
    plan_paid: Optional[float] = None
    member_owed: Optional[float] = None
    paid_to_member: Optional[float] = None


class ExtractionResult(BaseModel):
    # Must align with the documents.doc_type CHECK constraint values
    # ('eob','statement','receipt','other'). 'other' is intentionally excluded
    # here — extraction always categorizes into one of the known kinds.
    # TODO Phase 15: extend documents.doc_type CHECK constraint to include 'check'
    doc_type: Literal["statement", "eob", "receipt", "check"]
    document_date: Optional[str] = None
    practices: list[ExtractedPractice] = []
    providers: list[ExtractedProvider] = []
    claims: list[ExtractedClaim] = []
    adjudications: list[ExtractedAdjudication] = []
    raw_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Layer 2: Prompts
# ---------------------------------------------------------------------------

_JSON_SCHEMA_HINT = """Return ONLY a JSON object with this shape (no prose, no markdown fences):
{
  "doc_type": "eob|statement|receipt|check",
  "document_date": "YYYY-MM-DD" | null,
  "practices": [{"name": "...", "aliases": ["..."]}],
  "providers": [{"name": "...", "aliases": ["..."]}],
  "claims": [{
    "service_date": "YYYY-MM-DD",
    "billed_amount": 0.00,
    "practice_name": "...",
    "provider_name": "..." | null,
    "external_ids": [{"system": "...", "external_id": "..."}]
  }],
  "adjudications": [{
    "adjudication_date": "YYYY-MM-DD",
    "allowed_amount": 0.00 | null,
    "plan_paid": 0.00 | null,
    "member_owed": 0.00 | null,
    "paid_to_member": 0.00 | null
  }],
  "raw_text": "...optional excerpt..."
}
All amounts are USD as decimal numbers (no $ sign, no commas).
"""

STATEMENT_PROMPT = (
    "You are extracting structured data from a medical billing STATEMENT "
    "(provider/practice statement showing balance due for one or more services).\n\n"
    "Extract the practice name, service date(s), and billed amount(s). "
    "Adjudications usually do not appear on statements; leave that list empty "
    "unless the statement explicitly shows insurer payments and member responsibility.\n\n"
    + _JSON_SCHEMA_HINT
)

EOB_PROMPT = (
    "You are extracting structured data from an INSURANCE EXPLANATION OF BENEFITS (EOB).\n\n"
    "Extract the practice (rendering provider organization), the rendering provider "
    "if listed by name, the service date, the billed amount, and an `adjudications` "
    "entry capturing allowed_amount, plan_paid, member_owed, and paid_to_member if "
    "the EOB shows the payer mailed funds to the member rather than the practice.\n\n"
    + _JSON_SCHEMA_HINT
)

RECEIPT_PROMPT = (
    "You are extracting structured data from a payment RECEIPT "
    "(proof a member paid a practice or insurer paid out).\n\n"
    "Extract the practice name, the date of payment, and the amount as `billed_amount` "
    "on the relevant claim entry. If multiple line items appear, group them into separate "
    "claims using the matching service_date for each.\n\n"
    + _JSON_SCHEMA_HINT
)


# ---------------------------------------------------------------------------
# Layer 3: extract_from_file
# ---------------------------------------------------------------------------

def _detect_doc_kind(mime_type: str, file_path: str) -> str:
    """
    Classify the input as 'pdf' or 'image' for routing.
    Returns 'pdf', 'image', or 'unknown'.
    """
    mt = (mime_type or "").lower()
    if mt == "application/pdf":
        return "pdf"
    if mt in ("image/jpeg", "image/jpg", "image/png"):
        return "image"
    # Fall back to extension
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".jpg", ".jpeg", ".png"):
        return "image"
    return "unknown"


def _extract_pdf_text(file_path: str) -> tuple[str, list[str]]:
    """
    Extract text from all pages of a PDF using pypdf.

    Returns a tuple of (full_text, per_page_texts) where full_text is the
    newline-joined, stripped concatenation and per_page_texts holds the raw
    text of each page (used to scale the sparse-text threshold by page count).
    Returns ("", []) on error.
    """
    try:
        reader = PdfReader(file_path)
        per_page_texts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            per_page_texts.append(text)
        full_text = "\n".join(per_page_texts).strip()
        return full_text, per_page_texts
    except Exception:
        logger.error(f"Failed to extract text from PDF path='{file_path}'", exc_info=True)
        return "", []


def _select_prompt(text_hint: str) -> tuple[str, str]:
    """
    Select an extraction prompt based on a coarse keyword check on already-extracted
    text. Defaults to STATEMENT_PROMPT for ambiguous content.

    Returns (prompt, expected_doc_type_hint).
    """
    lowered = text_hint.lower()
    if "explanation of benefits" in lowered or "eob" in lowered:
        return EOB_PROMPT, "eob"
    if "receipt" in lowered or "thank you for your payment" in lowered:
        return RECEIPT_PROMPT, "receipt"
    return STATEMENT_PROMPT, "statement"


def _rasterize_pdf(file_path: str) -> list[tuple[str, str]]:
    """
    Render every page of a PDF to a base64-encoded JPEG image via pdf2image.

    Returns a list of (b64_string, mime_type) tuples, one per page, where
    mime_type is always "image/jpeg". Returns [] on any failure (e.g. Poppler
    not installed) and logs with exc_info=True — callers fall through to the
    text path.
    """
    try:
        # Deferred import: pdf2image pulls in Poppler at call time, and keeping
        # the import local means a missing system dependency only affects the
        # scanned-PDF path rather than module import.
        from pdf2image import convert_from_path

        pages = convert_from_path(file_path)
        rasterized: list[tuple[str, str]] = []
        for image in pages:
            buf = io.BytesIO()
            image.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            rasterized.append((b64, "image/jpeg"))
        return rasterized
    except Exception:
        logger.error(
            f"_rasterize_pdf: failed to rasterize PDF path='{file_path}'",
            exc_info=True,
        )
        return []


def _build_image_message(prompt: str, image_b64: str, mime_type: str) -> list[dict]:
    """Build a chat-completions message list for an image payload."""
    image_url = f"data:{mime_type};base64,{image_b64}"
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]


def _build_multi_image_message(
    prompt: str, images: list[tuple[str, str]]
) -> list[dict]:
    """
    Build a single-user-message payload with one text block followed by N
    image blocks. `images` is a list of (b64_string, mime_type) tuples.

    Generalizes _build_image_message for the multi-image (scanned-PDF or
    photo-album) vision path.
    """
    content: list[dict] = [{"type": "text", "text": prompt}]
    for image_b64, mime_type in images:
        image_url = f"data:{mime_type};base64,{image_b64}"
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    return [{"role": "user", "content": content}]


def _build_text_message(prompt: str, document_text: str) -> list[dict]:
    """Build a chat-completions message list for a text payload."""
    return [
        {
            "role": "user",
            "content": (
                f"{prompt}\n\n---\nDocument text:\n{document_text}"
            ),
        }
    ]


def extract_from_file(
    file_path: str,
    mime_type: str,
    extra_image_bytes: Optional[list[bytes]] = None,
    db_path: Optional[str] = None,
    practice_id: Optional[int] = None,
) -> Optional[ExtractionResult]:
    """
    Extract structured fields from a medical document file via the OpenRouter
    vision-capable LLM.

    Args:
        file_path: absolute path to the file on disk
        mime_type: MIME type as reported by the upload (best-effort)
        extra_image_bytes: optional additional image payloads (a photo album).
            When provided on the image path, the primary image plus each extra
            are packed into a single multi-image vision call. The caller is
            responsible for enforcing the MAX_ALBUM_IMAGES cap.
        db_path: optional path to the user's SQLite DB. When provided on the
            dense-text PDF path, layout learning (Phase 11) is applied: a stored
            relevant-page template is used to filter pages before the LLM call,
            or — on a first sighting — relevant pages are detected and the
            template is learned for next time.
        practice_id: optional practice id used as the layout-template key. Often
            unknown at extraction time; defaults to None (the practice-agnostic
            template slot).

    Returns:
        ExtractionResult on success, or None on any failure (logged).
    """
    try:
        kind = _detect_doc_kind(mime_type, file_path)
        if kind == "unknown":
            logger.warning(
                f"extract_from_file: unsupported file type mime='{mime_type}' "
                f"path='{file_path}'"
            )
            return None

        config = get_settings()

        # Build the message payload depending on the file kind.
        if kind == "pdf":
            # Text-extract via pypdf first. If the per-page text density is below
            # SPARSE_TEXT_THRESHOLD (likely a scanned PDF), rasterize the pages
            # and route to the multi-image vision path instead.
            text, per_page_texts = _extract_pdf_text(file_path)
            page_count = max(len(per_page_texts), 1)
            if len(text.strip()) < SPARSE_TEXT_THRESHOLD * page_count:
                logger.info(
                    f"PDF text was sparse ({len(text.strip())} chars across "
                    f"{page_count} page(s)) for path='{file_path}' — attempting "
                    f"rasterization for a vision-based extraction."
                )
                rasterized = _rasterize_pdf(file_path)
                if rasterized:
                    prompt = STATEMENT_PROMPT
                    messages = _build_multi_image_message(prompt, rasterized)
                else:
                    logger.warning(
                        f"_rasterize_pdf returned no images for path='{file_path}' "
                        f"(Poppler missing or render failure); falling back to the "
                        f"sparse text path."
                    )
                    prompt, _hint = _select_prompt(text)
                    messages = _build_text_message(prompt, text)
            else:
                # Dense-text branch: apply Phase 11 layout learning before
                # building the LLM message, so filler pages are dropped from the
                # payload. doc_type_hint keys the learned template.
                #
                # Detect the insurer on the ORIGINAL full text (before layout
                # filtering) so brand phrases on dropped pages still count.
                insurer = _detect_insurer(text)
                prompt, doc_type_hint = _select_prompt(text)
                if db_path is not None:
                    stored = load_template(db_path, doc_type_hint, practice_id)
                    if stored is not None:
                        valid_indices = [i for i in stored if i < len(per_page_texts)]
                        if valid_indices:
                            per_page_texts = [per_page_texts[i] for i in valid_indices]
                            text = "\n".join(per_page_texts).strip()
                        # If every stored index is out of bounds, fall back to
                        # all pages (no filtering) — never raise IndexError.
                    else:
                        relevant = detect_relevant_pages(per_page_texts)
                        if relevant:
                            per_page_texts = [per_page_texts[i] for i in relevant]
                            text = "\n".join(per_page_texts).strip()
                        update_template(
                            db_path,
                            doc_type_hint,
                            practice_id,
                            relevant if relevant else list(range(page_count)),
                        )

                # Phase 13: deterministic-extractor dispatch. If the insurer is
                # recognized and has registered extractor(s), try each one with
                # the post-layout-filter `text`. A non-None result short-circuits
                # the LLM call; any failure or None falls through to the LLM.
                if insurer is not None and EXTRACTOR_ALLOWLIST:
                    for entry in EXTRACTOR_ALLOWLIST:
                        if entry["insurer"] == insurer:
                            try:
                                module = importlib.import_module(
                                    f"src.medical.extractors.{entry['module']}"
                                )
                                det_result = module.extract(text)
                                if det_result is not None:
                                    logger.info(
                                        "extract_from_file: deterministic extractor "
                                        "'%s' produced result; skipping LLM.",
                                        entry["extractor_version"],
                                    )
                                    return det_result
                                logger.warning(
                                    "extract_from_file: extractor '%s' returned None; "
                                    "falling through to LLM.",
                                    entry["extractor_version"],
                                )
                            except Exception:
                                logger.error(
                                    "extract_from_file: extractor '%s' raised "
                                    "unexpectedly; falling through to LLM.",
                                    entry["extractor_version"],
                                    exc_info=True,
                                )

                messages = _build_text_message(prompt, text)

        elif kind == "image":
            with open(file_path, "rb") as f:
                raw_bytes = f.read()
            image_b64 = base64.b64encode(raw_bytes).decode("ascii")
            normalized_mime = mime_type.lower() if mime_type else "image/jpeg"
            if normalized_mime not in ("image/jpeg", "image/jpg", "image/png"):
                normalized_mime = "image/jpeg"
            # We cannot peek at image content for prompt selection; default to
            # statement and let the model classify via doc_type field.
            prompt = STATEMENT_PROMPT
            if extra_image_bytes:
                # Photo album: pack the primary image plus each extra into a
                # single multi-image vision call.
                images: list[tuple[str, str]] = [(image_b64, normalized_mime)]
                for extra in extra_image_bytes:
                    extra_b64 = base64.b64encode(extra).decode("ascii")
                    images.append((extra_b64, "image/jpeg"))
                messages = _build_multi_image_message(prompt, images)
            else:
                messages = _build_image_message(prompt, image_b64, normalized_mime)

        else:
            return None

        client = OpenAI(
            api_key=config.OPENROUTER_API_KEY,
            base_url=config.OPENROUTER_BASE_URL,
        )

        # Many OpenRouter models accept response_format json_object; if the
        # backing model rejects it, the SDK will raise — caught below.
        try:
            response = client.chat.completions.create(
                model=config.VISION_MODEL,
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=2000,
            )
        except Exception:
            logger.warning(
                f"Vision call with response_format=json_object failed; "
                f"retrying without that hint for model='{config.VISION_MODEL}'",
                exc_info=True,
            )
            response = client.chat.completions.create(
                model=config.VISION_MODEL,
                messages=messages,
                max_tokens=2000,
            )

        content = response.choices[0].message.content
        if not content:
            logger.error("extract_from_file: empty response content from LLM")
            return None

        try:
            result = ExtractionResult.model_validate_json(content)
        except ValidationError:
            logger.error(
                f"extract_from_file: response failed Pydantic validation. "
                f"raw_response={content!r}",
                exc_info=True,
            )
            return None

        logger.info(
            f"extract_from_file: extracted doc_type='{result.doc_type}' with "
            f"{len(result.claims)} claim(s), {len(result.adjudications)} adjudication(s)"
        )
        return result

    except Exception:
        logger.error(
            f"extract_from_file: unexpected failure for path='{file_path}' "
            f"mime='{mime_type}'",
            exc_info=True,
        )
        return None
