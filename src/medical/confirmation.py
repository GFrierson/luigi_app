"""
Confirmation message construction and reply parsing (Phase 4).

This module is pure: it builds human-readable strings from extraction +
match results and parses user replies into structured action dicts.
There are no DB writes here.

Two public functions:
    - build_confirmation_message(extraction, match_results) -> str
    - parse_confirmation_reply(reply_text, pending_items) -> dict
"""

import copy
import logging
import re
from typing import Optional

from src.medical.extraction import ExtractionResult

logger = logging.getLogger(__name__)


_CONFIRM_WORDS = {"yes", "y", "confirm", "ok", "okay", "looks good", "good"}
_CANCEL_WORDS = {"cancel", "discard", "nevermind", "never mind"}


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

        # Cancel phrases (checked BEFORE the numbered-correction regex)
        if lowered in _CANCEL_WORDS:
            return {"action": "cancel"}

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


def apply_correction(pending: dict, action: dict) -> Optional[dict]:
    """
    Apply a single numbered correction to a deep copy of `pending`.

    The numbered item space mirrors the "Action required" list in
    build_confirmation_message, built in this exact order:
        1. practices where not entry["matched"]
        2. claims where not entry["matched"]
        3. all providers from extraction.providers

    `action` shape: {"item_index": int (1-based), "correction_text": str}

    Behavior:
      - Returns None (without mutating the caller's dict) if item_index is out
        of range — the caller surfaces a user-facing "no such item" message.
      - For a practice name correction: updates the practice entry's name,
        re-keys practice_id_by_name (old name dropped, new name -> None), and
        rewrites every extraction.claims[*].practice_name that referenced the
        old name.
      - For a claim service_date correction: updates the claim match entry's
        service_date and the corresponding extraction.claims[j].service_date.
      - For a provider correction: updates extraction.providers[k].name.
      - On any unexpected error: logs with exc_info=True and returns the
        ORIGINAL pending unchanged (not None).
    """
    try:
        updated = copy.deepcopy(pending)
        extraction: ExtractionResult = updated["extraction"]
        match_results: dict = updated["match_results"]
        practice_id_by_name: dict = updated.setdefault("practice_id_by_name", {})

        practice_results = match_results.get("practices", []) or []
        claim_results = match_results.get("claims", []) or []

        # Build the same (kind, list_index) ordering used for numbering.
        ordered: list[tuple[str, int]] = []
        for i, entry in enumerate(practice_results):
            if not entry.get("matched"):
                ordered.append(("practice", i))
        for j, entry in enumerate(claim_results):
            if not entry.get("matched"):
                ordered.append(("claim", j))
        for k in range(len(extraction.providers)):
            ordered.append(("provider", k))

        item_index = action.get("item_index")
        if not isinstance(item_index, int) or item_index < 1 or item_index > len(ordered):
            logger.error(
                f"apply_correction: item_index={item_index!r} out of range "
                f"(1..{len(ordered)})",
                exc_info=True,
            )
            return None

        correction_text = (action.get("correction_text") or "").strip()
        kind, list_idx = ordered[item_index - 1]

        if kind == "practice":
            entry = practice_results[list_idx]
            old_name = entry.get("name", "")
            new_name = correction_text
            entry["name"] = new_name
            # Mark as needing re-match.
            entry["matched"] = False
            entry["practice_id"] = None
            # Re-key practice_id_by_name.
            practice_id_by_name.pop(old_name, None)
            practice_id_by_name[new_name] = None
            # Rewrite every claim that referenced the old practice name.
            for claim in extraction.claims:
                if claim.practice_name == old_name:
                    claim.practice_name = new_name

        elif kind == "claim":
            entry = claim_results[list_idx]
            new_date = correction_text
            entry["service_date"] = new_date
            entry["matched"] = False
            entry["claim_id"] = None
            if 0 <= list_idx < len(extraction.claims):
                extraction.claims[list_idx].service_date = new_date

        elif kind == "provider":
            extraction.providers[list_idx].name = correction_text

        return updated

    except Exception:
        logger.error(
            f"apply_correction: unexpected failure for action={action!r}",
            exc_info=True,
        )
        return pending
