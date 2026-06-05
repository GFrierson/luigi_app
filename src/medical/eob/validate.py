"""
Arithmetic and subtype-aware validation of a parsed ``EOBDocument``.

``validate`` cross-checks each claim's amounts (sum of allowed ≈ plan paid +
patient responsibility), is aware that denials / duplicate notices legitimately
have zero patient responsibility, and treats certain reason codes as
informational rather than errors. It returns a ``ValidationResult`` carrying a
confidence score; TEXT-sourced documents (clean text layer) are floored to high
confidence since OCR noise is absent.

Never raises: any failure returns a not-ok, zero-confidence result and is
logged with ``exc_info=True``.
"""

import logging

from src.medical.eob.types import (
    EOBDocument,
    PdfKind,
    ValidationResult,
)

logger = logging.getLogger(__name__)


OK_CONFIDENCE_THRESHOLD = 0.7

# Amount tolerance for arithmetic balance checks (dollars).
_AMOUNT_TOLERANCE = 0.01

# Confidence deducted per validation issue.
_ISSUE_PENALTY = 0.15

# Minimum confidence floor applied to clean TEXT-layer sources.
_TEXT_CONFIDENCE_FLOOR = 0.9

# Reason codes that are informational only (do not penalize confidence).
_INFO_REASON_CODES = {"ADU", "033", "A1"}

# Reason codes that signal a genuine adjudication issue.
_ISSUE_REASON_CODES = {"015"}

# Subtypes where a zero patient responsibility is expected (not an error).
_ZERO_OWES_OK_SUBTYPES = {"denial", "duplicate_notice"}

# Strings that represent a missing / not-applicable amount.
_NA_TOKENS = {"n/a", "--", "na", "none"}


def _parse_amount(s: str) -> float | None:
    """
    Parse a money string like ``'$1,234.56'`` into ``1234.56``.

    Returns ``None`` for unparseable input (e.g. ``'N/A'``, ``'--'``, empty).
    """
    if s is None:
        return None
    cleaned = s.replace("$", "").replace(",", "").strip()
    if cleaned == "" or cleaned.lower() in _NA_TOKENS:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def validate(eob: EOBDocument, source: PdfKind) -> ValidationResult:
    """
    Validate a parsed ``EOBDocument`` and return a ``ValidationResult``.

    Never raises — returns an ok=False / confidence=0.0 result on failure.
    """
    try:
        issues: list[str] = []
        subtype = eob.subtype

        for claim in eob.claims:
            allowed_sum = 0.0
            paid_sum = 0.0
            for item in claim.line_items:
                allowed = _parse_amount(item.allowed)
                paid = _parse_amount(item.anthem_paid)
                if allowed is not None:
                    allowed_sum += allowed
                if paid is not None:
                    paid_sum += paid

                code = item.reason_code.strip().upper() if item.reason_code else ""
                if code in {c.upper() for c in _ISSUE_REASON_CODES}:
                    issues.append(
                        f"claim {claim.claim_number}: reason code {code} flags an issue"
                    )

            owes = _parse_amount(claim.patient_owes)
            zero_owes_ok = subtype in _ZERO_OWES_OK_SUBTYPES
            effective_owes = owes if owes is not None else 0.0

            if owes is None and not zero_owes_ok:
                # No usable patient-responsibility figure; only flag a mismatch
                # when there is an allowed/paid imbalance to explain.
                if abs(allowed_sum - paid_sum) > _AMOUNT_TOLERANCE:
                    issues.append(
                        f"claim {claim.claim_number}: arithmetic mismatch "
                        f"(allowed {allowed_sum:.2f} != paid {paid_sum:.2f} + owes 0.00)"
                    )
                continue

            if zero_owes_ok and effective_owes == 0.0:
                continue

            if abs(allowed_sum - (paid_sum + effective_owes)) > _AMOUNT_TOLERANCE:
                issues.append(
                    f"claim {claim.claim_number}: arithmetic mismatch "
                    f"(allowed {allowed_sum:.2f} != paid {paid_sum:.2f} + "
                    f"owes {effective_owes:.2f})"
                )

        confidence = 1.0 - _ISSUE_PENALTY * len(issues)
        confidence = max(0.0, confidence)
        if source == PdfKind.TEXT:
            confidence = max(confidence, _TEXT_CONFIDENCE_FLOOR)

        ok = confidence >= OK_CONFIDENCE_THRESHOLD
        return ValidationResult(ok=ok, confidence=confidence, issues=issues)
    except Exception:
        logger.error("validate: unexpected failure", exc_info=True)
        return ValidationResult(
            ok=False, confidence=0.0, issues=["validate: unexpected failure"]
        )
