"""
Confirmation message construction and reply parsing (Phase 4).

This module is pure: it builds human-readable strings from extraction +
match results and parses user replies into structured action dicts.
There are no DB writes here.

Two public functions:
    - build_confirmation_message(extraction, match_results) -> str
    - parse_confirmation_reply(reply_text, pending_items) -> dict
"""

import logging
import re
from typing import Optional

from src.medical.extraction import ExtractionResult

logger = logging.getLogger(__name__)


_CONFIRM_WORDS = {"yes", "y", "confirm", "ok", "okay", "looks good", "good"}


def _format_amount(amount: Optional[float]) -> str:
    """Render a USD amount as $X.XX, or '?' if missing."""
    if amount is None:
        return "?"
    try:
        return f"${amount:,.2f}"
    except (TypeError, ValueError):
        return "?"


def _format_date_short(iso_date: Optional[str]) -> str:
    """Render an ISO date as 'Mon DD' (e.g., 'Sep 23'). Falls back to raw on parse errors."""
    if not iso_date:
        return "?"
    try:
        from datetime import datetime
        parsed = datetime.strptime(iso_date, "%Y-%m-%d")
        return parsed.strftime("%b %d")
    except (TypeError, ValueError):
        return iso_date


def build_confirmation_message(
    extraction: ExtractionResult,
    match_results: dict,
) -> str:
    """
    Build a human-readable confirmation message describing what was extracted
    and which entities matched existing records.

    `match_results` shape:
        {
            "practices": [{"name": "...", "matched": bool, "practice_id": int|None}],
            "claims":    [{"service_date": "...", "matched": bool, "claim_id": int|None}],
        }

    Numbered items are ONLY listed for unmatched / action-required entries.
    Already-matched items appear in a "Matched" section without numbers.
    """
    lines: list[str] = []

    # Header
    doc_label = extraction.doc_type.upper() if extraction.doc_type == "eob" else extraction.doc_type.capitalize()
    if extraction.document_date:
        lines.append(f"Document received: {doc_label} ({extraction.document_date})")
    else:
        lines.append(f"Document received: {doc_label}")
    lines.append("")

    practice_results = match_results.get("practices", []) or []
    claim_results = match_results.get("claims", []) or []

    matched_lines: list[str] = []
    action_lines: list[str] = []
    action_index = 0

    # Practices
    for entry in practice_results:
        name = entry.get("name", "")
        if entry.get("matched"):
            matched_lines.append(f"  Practice: {name} ✓")
        else:
            action_index += 1
            action_lines.append(
                f"  {action_index}. Practice \"{name}\" — not recognized. New practice?"
            )

    # Claims (cross-reference billed_amount from the extraction.claims list)
    claim_billed_by_date = {c.service_date: c.billed_amount for c in extraction.claims}
    for entry in claim_results:
        sd = entry.get("service_date", "")
        billed = claim_billed_by_date.get(sd)
        billed_str = _format_amount(billed)
        date_short = _format_date_short(sd)
        if entry.get("matched"):
            matched_lines.append(f"  Claim: {date_short} ({billed_str}) ✓")
        else:
            action_index += 1
            action_lines.append(
                f"  {action_index}. Claim {date_short} ({billed_str}) — no existing match. Create new?"
            )

    # Providers (extracted but not matched -> action required)
    for prov in extraction.providers:
        action_index += 1
        action_lines.append(
            f"  {action_index}. Provider \"{prov.name}\" — not recognized. New provider?"
        )

    # Adjudications (informational only — do not require numbered confirmation)
    if extraction.adjudications:
        adj_lines: list[str] = []
        for a in extraction.adjudications:
            parts = [f"adjudicated {a.adjudication_date}"]
            if a.allowed_amount is not None:
                parts.append(f"allowed {_format_amount(a.allowed_amount)}")
            if a.plan_paid is not None:
                parts.append(f"plan paid {_format_amount(a.plan_paid)}")
            if a.member_owed is not None:
                parts.append(f"member owed {_format_amount(a.member_owed)}")
            if a.paid_to_member is not None:
                parts.append(f"paid to member {_format_amount(a.paid_to_member)}")
            adj_lines.append("  " + ", ".join(parts))
        if adj_lines:
            matched_lines.append("  Adjudication:")
            matched_lines.extend("  " + line for line in adj_lines)

    if matched_lines:
        lines.append("Matched:")
        lines.extend(matched_lines)
        lines.append("")

    if action_lines:
        lines.append("Action required:")
        lines.extend(action_lines)
        lines.append("")

    if not action_lines:
        lines.append("Reply \"confirm\" to save.")
    else:
        lines.append(
            "Reply \"confirm\" to save as-is, or \"<number> <correction>\" "
            "to fix a numbered item."
        )

    return "\n".join(lines).rstrip() + "\n"


def parse_confirmation_reply(
    reply_text: str,
    pending_items: list[dict],
) -> dict:
    """
    Classify a user reply to a confirmation prompt.

    Returns one of:
      {"action": "confirm"}
      {"action": "correction", "item_index": int, "correction_text": str}
      {"action": "free_text", "text": <reply_text>}
      {"action": "unknown"}

    Never raises.
    """
    try:
        if reply_text is None:
            return {"action": "unknown"}

        normalized = reply_text.strip()
        if not normalized:
            return {"action": "unknown"}

        lowered = normalized.lower()

        # Confirm phrases (exact match on whole reply)
        if lowered in _CONFIRM_WORDS:
            return {"action": "confirm"}

        # Numbered correction: starts with a digit, optional dot/colon, then text
        match = re.match(r"^(\d+)\s*[.:)\-]?\s+(.*\S.*)$", normalized)
        if match:
            return {
                "action": "correction",
                "item_index": int(match.group(1)),
                "correction_text": match.group(2).strip(),
            }

        # Anything else is free text — fall through to handle_message
        return {"action": "free_text", "text": reply_text}

    except Exception:
        logger.error(
            f"parse_confirmation_reply failed for reply={reply_text!r}",
            exc_info=True,
        )
        return {"action": "unknown"}
