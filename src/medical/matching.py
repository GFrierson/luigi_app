"""
Entity matching helpers for ingestion (Phase 4).

Layered match strategy:
    1. Exact: try resolve_practice / resolve_provider (handles name + alias).
    2. Fuzzy: rapidfuzz.process.extractOne with score_cutoff=85 against all
       names + aliases. If we find a fuzzy hit, resolve back to the canonical
       row.
    3. None: log a 'propose-new' info line and return None — the ingestion
       layer will surface this as an unmatched entity in the confirmation
       message.

For claims, matching is a thin wrapper around find_by_match_key from
src.medical.claims since the (service_date, billing_practice_id, billed_amount)
key is already deterministic.

All public functions:
    - are SYNCHRONOUS (callers should wrap in asyncio.to_thread())
    - never raise — they catch exceptions, log with exc_info=True, return None
"""

import logging
from typing import Optional

from rapidfuzz import process as rf_process

from src.database import get_connection
from src.medical.claims import (
    find_by_match_key,
    find_submitted_by_date_and_practice,
)
from src.medical.entities import resolve_practice, resolve_provider

logger = logging.getLogger(__name__)


_FUZZY_SCORE_CUTOFF = 85


# ---------------------------------------------------------------------------
# Internal: pull all candidates (name + aliases) from a join
# ---------------------------------------------------------------------------

def _load_practice_candidates(db_path: str) -> dict[str, int]:
    """
    Return {candidate_string: practice_id} for fuzzy matching.

    Includes both practices.name and practice_aliases.alias. If the same
    string appears for multiple practices (rare), the last write wins —
    that's acceptable for fuzzy hint lookup.
    """
    out: dict[str, int] = {}
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM practices")
        for row in cursor.fetchall():
            out[row["name"]] = row["id"]
        cursor.execute(
            """
            SELECT practice_id, alias
            FROM practice_aliases
            """
        )
        for row in cursor.fetchall():
            out[row["alias"]] = row["practice_id"]
        return out
    except Exception:
        logger.error("Failed to load practice candidates for fuzzy matching", exc_info=True)
        return out
    finally:
        conn.close()


def _load_provider_candidates(db_path: str) -> dict[str, int]:
    """Return {candidate_string: provider_id} for fuzzy matching."""
    out: dict[str, int] = {}
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM providers")
        for row in cursor.fetchall():
            out[row["name"]] = row["id"]
        cursor.execute(
            """
            SELECT provider_id, alias
            FROM provider_aliases
            """
        )
        for row in cursor.fetchall():
            out[row["alias"]] = row["provider_id"]
        return out
    except Exception:
        logger.error("Failed to load provider candidates for fuzzy matching", exc_info=True)
        return out
    finally:
        conn.close()


def _fetch_practice_by_id(db_path: str, practice_id: int) -> Optional[dict]:
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name FROM practices WHERE id = ?",
            (practice_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception:
        logger.error(f"Failed to fetch practice id={practice_id}", exc_info=True)
        return None
    finally:
        conn.close()


def _fetch_provider_by_id(db_path: str, provider_id: int) -> Optional[dict]:
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name FROM providers WHERE id = ?",
            (provider_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception:
        logger.error(f"Failed to fetch provider id={provider_id}", exc_info=True)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public matchers
# ---------------------------------------------------------------------------

def match_practice(db_path: str, name: str) -> Optional[dict]:
    """
    Resolve `name` to a practice row.

    Strategy:
      1. resolve_practice (exact name + alias)
      2. rapidfuzz against all known practice names+aliases at score >= 85
      3. None (logs 'propose-new')
    """
    if not name or not name.strip():
        return None

    try:
        exact = resolve_practice(db_path, name)
        if exact:
            return exact

        candidates = _load_practice_candidates(db_path)
        if not candidates:
            logger.info(f"propose-new practice: {name}")
            return None

        choice = rf_process.extractOne(
            name,
            list(candidates.keys()),
            score_cutoff=_FUZZY_SCORE_CUTOFF,
        )
        if choice is None:
            logger.info(f"propose-new practice: {name}")
            return None

        matched_string, score, _idx = choice
        practice_id = candidates[matched_string]
        result = _fetch_practice_by_id(db_path, practice_id)
        if result:
            logger.debug(
                f"Fuzzy-matched practice query='{name}' -> '{matched_string}' "
                f"(score={score:.1f}) practice_id={practice_id}"
            )
        return result
    except Exception:
        logger.error(f"match_practice failed for name='{name}'", exc_info=True)
        return None


def match_provider(db_path: str, name: str) -> Optional[dict]:
    """
    Resolve `name` to a provider row.

    Same layered strategy as match_practice.
    """
    if not name or not name.strip():
        return None

    try:
        exact = resolve_provider(db_path, name)
        if exact:
            return exact

        candidates = _load_provider_candidates(db_path)
        if not candidates:
            logger.info(f"propose-new provider: {name}")
            return None

        choice = rf_process.extractOne(
            name,
            list(candidates.keys()),
            score_cutoff=_FUZZY_SCORE_CUTOFF,
        )
        if choice is None:
            logger.info(f"propose-new provider: {name}")
            return None

        matched_string, score, _idx = choice
        provider_id = candidates[matched_string]
        result = _fetch_provider_by_id(db_path, provider_id)
        if result:
            logger.debug(
                f"Fuzzy-matched provider query='{name}' -> '{matched_string}' "
                f"(score={score:.1f}) provider_id={provider_id}"
            )
        return result
    except Exception:
        logger.error(f"match_provider failed for name='{name}'", exc_info=True)
        return None


def match_claim(
    db_path: str,
    service_date: str,
    practice_id: int,
    billed_amount: float,
) -> Optional[dict]:
    """
    Look up an existing claim by the canonical match key, with an amount-tolerant
    ambiguity fallback (Phase 12).

    Strategy:
      1. find_by_match_key (exact service_date + practice_id + billed_amount).
         A hit returns the claim dict unchanged (caller treats this as matched).
      2. On miss, if practice_id is not None, look up prior *submitted* claims
         for the same date+practice via find_submitted_by_date_and_practice.
         - 0 results -> return None (unchanged behavior; caller creates new).
         - 1+ results -> return an ambiguity dict:
             {"matched": False, "suggested_link": [claim, ...],
              "match_type": "prior_bill"}
           The caller surfaces these as "link?" candidates rather than a match.

    Never raises.
    """
    try:
        exact = find_by_match_key(db_path, service_date, practice_id, billed_amount)
        if exact is not None:
            return exact

        if practice_id is None:
            return None

        prior = find_submitted_by_date_and_practice(db_path, service_date, practice_id)
        if not prior:
            return None

        logger.info(
            f"match_claim: no exact match but {len(prior)} prior submitted "
            f"bill(s) found service_date={service_date} practice_id={practice_id} "
            f"amount={billed_amount}"
        )
        return {
            "matched": False,
            "suggested_link": prior,
            "match_type": "prior_bill",
        }
    except Exception:
        logger.error(
            f"match_claim failed for service_date={service_date} "
            f"practice_id={practice_id} amount={billed_amount}",
            exc_info=True,
        )
        return None


def rematch_after_correction(db_path: str, pending: dict) -> dict:
    """
    Re-run entity matching after a correction has been applied to `pending`.

    Operates on the already-corrected pending state (see apply_correction):
      - For each practice entry with practice_id is None: re-run match_practice
        and update the entry's matched/practice_id plus practice_id_by_name.
      - For each unmatched claim entry: re-run match_claim using the practice_id
        resolved for that claim's practice_name (None practice_id -> stays
        unmatched).
      - For every provider in extraction.providers: re-run match_provider
        (informational; providers are not stored in match_results).

    Returns the updated match_results. On any error, logs with exc_info=True and
    returns match_results unchanged.
    """
    match_results = pending.get("match_results", {}) or {}
    try:
        extraction = pending["extraction"]
        practice_id_by_name: dict = pending.setdefault("practice_id_by_name", {})

        practice_results = match_results.get("practices", []) or []
        claim_results = match_results.get("claims", []) or []

        # Re-match practices that have no resolved id yet.
        for entry in practice_results:
            if entry.get("practice_id") is not None:
                continue
            name = entry.get("name", "")
            matched = match_practice(db_path, name)
            entry["matched"] = matched is not None
            entry["practice_id"] = matched["id"] if matched else None
            practice_id_by_name[name] = matched["id"] if matched else None

        # Pair claim_results with extraction.claims by list index. Both lists are
        # built in lockstep during ingestion, so index i corresponds to the same
        # claim in both. Index pairing avoids the date-keyed dict collision that
        # would drop one of two same-date claims with different amounts.
        for i, entry in enumerate(claim_results):
            if entry.get("matched"):
                continue
            if i >= len(extraction.claims):
                continue
            claim = extraction.claims[i]
            pname = claim.practice_name
            pid = practice_id_by_name.get(pname)
            if pid is None:
                entry["matched"] = False
                entry["claim_id"] = None
                entry.pop("suggested_link", None)
                entry.pop("match_type", None)
                continue
            matched_claim = match_claim(db_path, claim.service_date, pid, claim.billed_amount)
            if matched_claim is None:
                entry["matched"] = False
                entry["claim_id"] = None
                entry.pop("suggested_link", None)
                entry.pop("match_type", None)
            elif matched_claim.get("suggested_link"):
                # Amount-tolerant ambiguity: surface link candidates, stay unmatched.
                entry["matched"] = False
                entry["claim_id"] = None
                entry["suggested_link"] = matched_claim["suggested_link"]
                entry["match_type"] = matched_claim.get("match_type", "prior_bill")
            else:
                entry["matched"] = True
                entry["claim_id"] = matched_claim["id"]
                entry.pop("suggested_link", None)
                entry.pop("match_type", None)

        # Providers: re-run match_provider (spec includes this); results are
        # informational only and not persisted into match_results.
        for prov in extraction.providers:
            match_provider(db_path, prov.name)

        return match_results
    except Exception:
        logger.error("rematch_after_correction failed", exc_info=True)
        return match_results
