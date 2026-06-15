"""
LLM vision fallback extractor (Phase 3).

``LLMVisionExtractor`` satisfies the ``GroundedExtractor`` Protocol from
``src.medical.eob.types``. It is the fallback path the pipeline reaches for
documents whose issuer was not recognized by a deterministic profile: it sends
the document's per-page PNG images to an OpenRouter vision model and asks for a
grounded EOB JSON envelope, which it normalizes into an ``EOBDocument`` plus a
``GroundingReport`` recording per-field provenance.

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
from src.medical.eob.extractors.grounding import check_grounding
from src.medical.eob.types import (
    Claim,
    Document,
    EOBDocument,
    EOBSubtype,
    GroundedField,
    GroundingReport,
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
    "BENEFITS (EOB) document. You are given one or more page images.\n\n"
    "GROUNDING RULES (read carefully):\n"
    "- Transcribe ONLY values literally visible on the page. NEVER infer, "
    "complete, paraphrase, or guess a missing value.\n"
    "- For every leaf field, return a grounded field object with: the dotted "
    'field path ("field"), the transcribed value ("value"), the 0-based page '
    'index it appears on ("page"), the verbatim token(s) copied from the page '
    'image ("span"), and a boolean "found".\n'
    "- When a value is NOT present on any page, return it as "
    '{"field": "...", "value": null, "page": null, "span": null, '
    '"found": false}.\n'
    "- The \"span\" must be an exact copy of the on-page token(s) for that "
    "value (same characters, including $ and commas if printed).\n"
    "- Arithmetic is permitted ONLY over values you have already cited (e.g. "
    "summing cited line-item amounts to populate a totals field). No prose "
    "composition.\n"
    '- "subtype" must be one of: "summary", "denial", "payment_notice", '
    '"duplicate_notice". "in_network" is a boolean expressed as the string '
    '"true" or "false" in its value field.\n\n'
    "Return ONLY a single JSON envelope (no prose, no markdown fences) with "
    "exactly this shape, where EVERY leaf is a grounded field object:\n"
    "{\n"
    '  "issuer":     {"field": "issuer", "value": "...", "page": 0, '
    '"span": "...", "found": true},\n'
    '  "subtype":    {"field": "subtype", "value": "summary", "page": 0, '
    '"span": "...", "found": true},\n'
    '  "subscriber": {"field": "subscriber", "value": "...", "page": 0, '
    '"span": "...", "found": true},\n'
    '  "claims": [{\n'
    '    "patient":       {"field": "claims[0].patient", ...},\n'
    '    "claim_number":  {"field": "claims[0].claim_number", ...},\n'
    '    "received_date": {"field": "claims[0].received_date", ...},\n'
    '    "provider":      {"field": "claims[0].provider", ...},\n'
    '    "in_network":    {"field": "claims[0].in_network", '
    '"value": "true", ...},\n'
    '    "patient_owes":  {"field": "claims[0].patient_owes", ...},\n'
    '    "line_items": [{\n'
    '      "service_date":   {"field": "claims[0].line_items[0].service_date", ...},\n'
    '      "service":        {"field": "claims[0].line_items[0].service", ...},\n'
    '      "reason_code":    {"field": "claims[0].line_items[0].reason_code", ...},\n'
    '      "doctor_charges": {"field": "claims[0].line_items[0].doctor_charges", ...},\n'
    '      "discounts":      {"field": "claims[0].line_items[0].discounts", ...},\n'
    '      "allowed":        {"field": "claims[0].line_items[0].allowed", ...},\n'
    '      "anthem_paid":    {"field": "claims[0].line_items[0].anthem_paid", ...},\n'
    '      "copay":          {"field": "claims[0].line_items[0].copay", ...},\n'
    '      "deductible":     {"field": "claims[0].line_items[0].deductible", ...},\n'
    '      "coinsurance":    {"field": "claims[0].line_items[0].coinsurance", ...},\n'
    '      "not_covered":    {"field": "claims[0].line_items[0].not_covered", ...},\n'
    '      "your_total":     {"field": "claims[0].line_items[0].your_total", ...}\n'
    "    }]\n"
    "  }]\n"
    "}\n"
)

_USER_TEXT = "Extract the EOB into the grounded JSON envelope described above."


def _coerce_subtype(value: object) -> EOBSubtype:
    """Validate a JSON subtype, defaulting to 'summary' when not recognized."""
    if isinstance(value, str) and value in _VALID_SUBTYPES:
        return value  # type: ignore[return-value]
    return _DEFAULT_SUBTYPE


def _parse_grounded_field(raw: object) -> tuple[str, GroundedField]:
    """
    Unwrap one grounded field dict into ``(str_value, GroundedField)``.

    ``str_value`` is ``""`` when found=False or value is None (coerced, never
    the literal ``"None"``). Never raises — returns a safe empty tuple on bad
    input.
    """
    if not isinstance(raw, dict):
        return ("", GroundedField(field="", value=None, page=None, span=None, found=False))

    found = bool(raw.get("found", False))
    if found:
        value = raw.get("value")
        page = raw.get("page")
        span = raw.get("span")
    else:
        value, page, span = None, None, None

    str_value = str(value) if (value is not None and found) else ""
    return (
        str_value,
        GroundedField(
            field=str(raw.get("field", "")),
            value=str_value if found else None,
            page=int(page) if isinstance(page, int) else None,
            span=str(span) if span is not None else None,
            found=found,
        ),
    )


def _unwrap_envelope(payload: dict) -> tuple[dict, list[GroundedField]]:
    """
    Normalize a grounded envelope into a flat ``EOBDocument``-shaped dict plus
    the list of all leaf ``GroundedField`` provenance records.

    This is the normalization boundary: after it runs, ``_parse_eob_json`` /
    ``_build_claim`` / ``_build_line_item`` operate on the flat dict unchanged.
    ``in_network`` is parsed from its ``"true"/"false"`` string value into a
    bool.
    """
    grounded: list[GroundedField] = []
    flat: dict = {}

    for key in ("issuer", "subtype", "subscriber"):
        str_value, gf = _parse_grounded_field(payload.get(key))
        flat[key] = str_value
        grounded.append(gf)

    flat_claims: list[dict] = []
    claims_raw = payload.get("claims") or []
    for claim_raw in claims_raw:
        if not isinstance(claim_raw, dict):
            continue
        flat_claim: dict = {}
        for key in (
            "patient",
            "claim_number",
            "received_date",
            "provider",
            "patient_owes",
        ):
            str_value, gf = _parse_grounded_field(claim_raw.get(key))
            flat_claim[key] = str_value
            grounded.append(gf)

        in_network_str, in_network_gf = _parse_grounded_field(claim_raw.get("in_network"))
        flat_claim["in_network"] = in_network_str.strip().lower() == "true"
        grounded.append(in_network_gf)

        flat_line_items: list[dict] = []
        line_items_raw = claim_raw.get("line_items") or []
        for li_raw in line_items_raw:
            if not isinstance(li_raw, dict):
                continue
            flat_li: dict = {}
            for key in (
                "service_date",
                "service",
                "reason_code",
                "doctor_charges",
                "discounts",
                "allowed",
                "anthem_paid",
                "copay",
                "deductible",
                "coinsurance",
                "not_covered",
                "your_total",
            ):
                str_value, gf = _parse_grounded_field(li_raw.get(key))
                flat_li[key] = str_value
                grounded.append(gf)
            flat_line_items.append(flat_li)

        flat_claim["line_items"] = flat_line_items
        flat_claims.append(flat_claim)

    flat["claims"] = flat_claims
    return (flat, grounded)


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
    """Vision-LLM fallback extractor satisfying the ``GroundedExtractor`` Protocol."""

    def extract(self, doc: Document) -> tuple[EOBDocument, GroundingReport]:
        """
        Extract an ``EOBDocument`` plus a ``GroundingReport`` from
        ``doc.page_images`` via a vision LLM.

        Never raises: on any failure logs with exc_info=True and returns a
        safe empty ``EOBDocument`` and an empty ``GroundingReport``.
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
                return _empty_eob(), GroundingReport(fields=[], ungrounded=[])
            payload = json.loads(content)
            if not isinstance(payload, dict):
                logger.error("LLM extraction returned non-object JSON")
                return _empty_eob(), GroundingReport(fields=[], ungrounded=[])
            flat_dict, grounded_fields = _unwrap_envelope(payload)
            eob = _parse_eob_json(flat_dict)
            report = check_grounding(grounded_fields, doc)
            return eob, report
        except Exception:
            logger.error("LLM extraction failed", exc_info=True)
            return _empty_eob(), GroundingReport(fields=[], ungrounded=[])
