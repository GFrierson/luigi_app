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
      sparse text will yield best-effort/empty extractions; rendering scanned
      pages to raster images is deferred to a later phase (no `pdf2image`
      dependency in v1 since it requires a system-level Poppler install).
    - Images (JPEG/PNG) are sent as base64-encoded image_url parts to the
      OpenRouter vision endpoint.
    - All exceptions are caught at the boundary; the function returns None
      on any failure and logs with exc_info=True.
"""

import base64
import logging
import os
from typing import Literal, Optional

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader

from src.config import get_settings

logger = logging.getLogger(__name__)


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
    # here — extraction always categorizes into one of the three known kinds.
    doc_type: Literal["statement", "eob", "receipt"]
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
  "doc_type": "statement" | "eob" | "receipt",
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


def _extract_pdf_text(file_path: str) -> str:
    """Extract text from all pages of a PDF using pypdf. Returns '' on error."""
    try:
        reader = PdfReader(file_path)
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
        return "\n".join(parts).strip()
    except Exception:
        logger.error(f"Failed to extract text from PDF path='{file_path}'", exc_info=True)
        return ""


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


def extract_from_file(file_path: str, mime_type: str) -> Optional[ExtractionResult]:
    """
    Extract structured fields from a medical document file via the OpenRouter
    vision-capable LLM.

    Args:
        file_path: absolute path to the file on disk
        mime_type: MIME type as reported by the upload (best-effort)

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
            # v1 strategy: text-extract via pypdf. Scanned PDFs will produce
            # sparse text; we still attempt LLM extraction and let the model
            # decide based on whatever it sees. PDF -> raster rendering is
            # deferred (would require pdf2image + Poppler at the system level).
            text = _extract_pdf_text(file_path)
            if len(text) <= 50:
                logger.warning(
                    f"PDF text extraction was sparse ({len(text)} chars) for "
                    f"path='{file_path}' — likely a scanned PDF; v1 still attempts "
                    f"extraction with the limited text we have."
                )
            prompt, _hint = _select_prompt(text)
            messages = _build_text_message(prompt, text)

        elif kind == "image":
            with open(file_path, "rb") as f:
                raw_bytes = f.read()
            image_b64 = base64.b64encode(raw_bytes).decode("ascii")
            # We cannot peek at image content for prompt selection; default to
            # statement and let the model classify via doc_type field.
            prompt = STATEMENT_PROMPT
            normalized_mime = mime_type.lower() if mime_type else "image/jpeg"
            if normalized_mime not in ("image/jpeg", "image/jpg", "image/png"):
                normalized_mime = "image/jpeg"
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
