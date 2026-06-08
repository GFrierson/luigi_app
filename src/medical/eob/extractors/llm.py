"""
LLM vision fallback extractor (Phase 3).

``LLMVisionExtractor`` satisfies the ``Extractor`` Protocol from
``src.medical.eob.types``. It is the fallback path the pipeline reaches for
documents whose issuer was not recognized by a deterministic profile: it sends
the document's per-page PNG images to an OpenRouter vision model and asks for a
structured EOB JSON object, which it parses into an ``EOBDocument``.

Design notes:
    - The OpenRouter client and settings are resolved fresh inside ``extract``
      (not at construction time), mirroring the pattern in ``src/agent.py`` and
      ``src/medical/extraction.py``. This keeps construction side-effect-free
      so the extractor can be instantiated as a module-level constant.
    - ``extract`` never raises. On ANY failure it logs with ``exc_info=True``
      and returns a safe empty ``EOBDocument`` (issuer 'unknown', subtype
      'summary').
"""

import base64
import json
import logging
from typing import Optional

from openai import OpenAI

from src.config import get_settings
from src.medical.eob.types import (
    Claim,
    Document,
    EOBDocument,
    EOBSubtype,
    LineItem,
)

logger = logging.getLogger(__name__)


# Valid EOBSubtype literal values; JSON-supplied subtypes outside this set are
# coerced to the default below.
_VALID_SUBTYPES: frozenset[str] = frozenset(
    {"summary", "denial", "payment_notice", "duplicate_notice"}
)
_DEFAULT_SUBTYPE: EOBSubtype = "summary"

_MAX_TOKENS = 4000

_SYSTEM_PROMPT = (
    "You are extracting structured data from an insurance EXPLANATION OF "
    "BENEFITS (EOB) document. You are given one or more page images. Read every "
    "page and return ONLY a single JSON object (no prose, no markdown fences) "
    "with exactly this shape:\n"
    "{\n"
    '  "issuer": "string",\n'
    '  "subtype": "summary|denial|payment_notice|duplicate_notice",\n'
    '  "subscriber": "string",\n'
    '  "claims": [{\n'
    '    "patient": "string",\n'
    '    "claim_number": "string",\n'
    '    "received_date": "string|null",\n'
    '    "provider": "string",\n'
    '    "in_network": true,\n'
    '    "patient_owes": "string",\n'
    '    "line_items": [{\n'
    '      "service_date": "string",\n'
    '      "service": "string",\n'
    '      "reason_code": "string",\n'
    '      "doctor_charges": "string",\n'
    '      "discounts": "string",\n'
    '      "allowed": "string",\n'
    '      "anthem_paid": "string",\n'
    '      "copay": "string",\n'
    '      "deductible": "string",\n'
    '      "coinsurance": "string",\n'
    '      "not_covered": "string",\n'
    '      "your_total": "string"\n'
    "    }]\n"
    "  }]\n"
    "}\n"
    "All monetary amounts are strings exactly as printed (keep the $ sign if "
    "shown). Use null for received_date when it is not present. in_network is a "
    "boolean. subtype must be one of the four listed values."
)

_USER_TEXT = "Extract the EOB into the JSON object described above."


def _coerce_subtype(value: object) -> EOBSubtype:
    """Validate a JSON subtype, defaulting to 'summary' when not recognized."""
    if isinstance(value, str) and value in _VALID_SUBTYPES:
        return value  # type: ignore[return-value]
    return _DEFAULT_SUBTYPE


def _build_line_item(raw: dict) -> LineItem:
    """Construct a ``LineItem`` from a raw JSON dict, defaulting missing fields."""
    return LineItem(
        service_date=str(raw.get("service_date", "")),
        service=str(raw.get("service", "")),
        reason_code=str(raw.get("reason_code", "")),
        doctor_charges=str(raw.get("doctor_charges", "")),
        discounts=str(raw.get("discounts", "")),
        allowed=str(raw.get("allowed", "")),
        anthem_paid=str(raw.get("anthem_paid", "")),
        copay=str(raw.get("copay", "")),
        deductible=str(raw.get("deductible", "")),
        coinsurance=str(raw.get("coinsurance", "")),
        not_covered=str(raw.get("not_covered", "")),
        your_total=str(raw.get("your_total", "")),
    )


def _build_claim(raw: dict) -> Claim:
    """Construct a ``Claim`` (with nested line items) from a raw JSON dict."""
    line_items_raw = raw.get("line_items") or []
    line_items = [_build_line_item(li) for li in line_items_raw if isinstance(li, dict)]
    received_date = raw.get("received_date")
    return Claim(
        patient=str(raw.get("patient", "")),
        claim_number=str(raw.get("claim_number", "")),
        received_date=received_date if isinstance(received_date, str) else None,
        provider=str(raw.get("provider", "")),
        in_network=bool(raw.get("in_network", False)),
        patient_owes=str(raw.get("patient_owes", "")),
        line_items=line_items,
    )


def _parse_eob_json(payload: dict) -> EOBDocument:
    """Construct an ``EOBDocument`` from a parsed JSON payload dict."""
    claims_raw = payload.get("claims") or []
    claims = [_build_claim(c) for c in claims_raw if isinstance(c, dict)]
    return EOBDocument(
        issuer=str(payload.get("issuer", "unknown")),
        subtype=_coerce_subtype(payload.get("subtype")),
        subscriber=str(payload.get("subscriber", "")),
        claims=claims,
    )


def _empty_eob() -> EOBDocument:
    """Return the safe fallback EOBDocument used on any extraction failure."""
    return EOBDocument(
        issuer="unknown",
        subtype=_DEFAULT_SUBTYPE,
        subscriber="",
        claims=[],
    )


def _build_messages(page_images: list[bytes]) -> list[dict]:
    """
    Build the chat-completions message list: a system prompt followed by a user
    message containing one image block per page plus a final text instruction.
    """
    content: list[dict] = []
    for png_bytes in page_images:
        b64 = base64.b64encode(png_bytes).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )
    content.append({"type": "text", "text": _USER_TEXT})
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


class LLMVisionExtractor:
    """Vision-LLM fallback extractor satisfying the ``Extractor`` Protocol."""

    def extract(self, doc: Document) -> EOBDocument:
        """
        Extract an ``EOBDocument`` from ``doc.page_images`` via a vision LLM.

        Never raises: on any failure logs with exc_info=True and returns a
        safe empty ``EOBDocument`` (issuer 'unknown', subtype 'summary').
        """
        try:
            settings = get_settings()
            client = OpenAI(
                api_key=settings.OPENROUTER_API_KEY,
                base_url=settings.OPENROUTER_BASE_URL,
            )
            messages = _build_messages(doc.page_images)
            response = client.chat.completions.create(
                model=settings.LLM_VISION_MODEL,
                messages=messages,
                max_tokens=_MAX_TOKENS,
                response_format={"type": "json_object"},
            )
            content: Optional[str] = response.choices[0].message.content
            if not content:
                logger.error("LLM extraction returned empty content")
                return _empty_eob()
            payload = json.loads(content)
            if not isinstance(payload, dict):
                logger.error("LLM extraction returned non-object JSON")
                return _empty_eob()
            return _parse_eob_json(payload)
        except Exception:
            logger.error("LLM extraction failed", exc_info=True)
            return _empty_eob()
