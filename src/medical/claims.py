"""
Claims & adjudication lifecycle helpers.

Tables managed here (schema owned by src.database.init_db()):
- claims
- claim_external_ids
- charges (table only — helpers added in later phases)
- adjudications
- claim_events

All public functions:
- are synchronous (callers should wrap in asyncio.to_thread() inside async code)
- return dict | None or list[dict]
- never raise — they catch exceptions, log with exc_info=True, and return None / []
"""

import json
import logging
import sqlite3
from typing import Optional

from src.database import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal: append a claim_event row (cursor-level, no commit)
# ---------------------------------------------------------------------------

def _append_event(
    cursor: sqlite3.Cursor,
    claim_id: int,
    event_type: str,
    payload: Optional[str],
) -> None:
    """
    Insert a claim_events row using the supplied cursor.

    Caller is responsible for committing the surrounding transaction.
    `payload` must be a pre-serialized JSON string (or None).
    Never raises — logs and returns on failure.
    """
    try:
        cursor.execute(
            """
            INSERT INTO claim_events (claim_id, event_type, payload)
            VALUES (?, ?, ?)
            """,
            (claim_id, event_type, payload),
        )
    except Exception:
        logger.error(
            f"Failed to append claim_event claim_id={claim_id} event_type='{event_type}'",
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

def create_claim(
    db_path: str,
    service_date: str,
    billing_practice_id: int,
    billed_amount: float,
    encounter_id: Optional[int] = None,
    insurer_id: Optional[int] = None,
) -> Optional[dict]:
    """
    Insert a new claim. Returns the created row dict, or None on duplicate
    match-key / failure. Also appends a 'created' claim_event in the same transaction.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO claims
                (service_date, billing_practice_id, encounter_id, insurer_id, billed_amount)
            VALUES (?, ?, ?, ?, ?)
            """,
            (service_date, billing_practice_id, encounter_id, insurer_id, billed_amount),
        )
        claim_id = cursor.lastrowid

        payload = json.dumps({
            "service_date": service_date,
            "billing_practice_id": billing_practice_id,
            "encounter_id": encounter_id,
            "insurer_id": insurer_id,
            "billed_amount": billed_amount,
        })
        _append_event(cursor, claim_id, "created", payload)

        conn.commit()

        cursor.execute(
            """
            SELECT id, service_date, billing_practice_id, encounter_id,
                   insurer_id, billed_amount, current_status
            FROM claims
            WHERE id = ?
            """,
            (claim_id,),
        )
        row = cursor.fetchone()
        logger.info(
            f"Created claim id={claim_id} service_date={service_date} "
            f"practice_id={billing_practice_id} amount={billed_amount}"
        )
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        logger.debug(
            f"Duplicate claim match-key service_date={service_date} "
            f"practice_id={billing_practice_id} amount={billed_amount}, skipping"
        )
        return None
    except Exception:
        logger.error(
            f"Failed to create claim service_date={service_date} "
            f"practice_id={billing_practice_id} amount={billed_amount}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def add_external_id(
    db_path: str,
    claim_id: int,
    system: str,
    external_id: str,
) -> Optional[dict]:
    """
    Attach an external identifier (e.g., payer claim number) to a claim.

    Returns the inserted row dict, or None on duplicate (system, external_id) / failure.
    Appends an 'external_id_added' claim_event in the same transaction.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO claim_external_ids (claim_id, system, external_id)
            VALUES (?, ?, ?)
            """,
            (claim_id, system, external_id),
        )
        ext_id = cursor.lastrowid

        payload = json.dumps({"system": system, "external_id": external_id})
        _append_event(cursor, claim_id, "external_id_added", payload)

        conn.commit()

        cursor.execute(
            """
            SELECT id, claim_id, system, external_id
            FROM claim_external_ids
            WHERE id = ?
            """,
            (ext_id,),
        )
        row = cursor.fetchone()
        logger.info(
            f"Added external id ext_id={ext_id} claim_id={claim_id} "
            f"system='{system}' external_id='{external_id}'"
        )
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        logger.debug(
            f"Duplicate external id system='{system}' external_id='{external_id}', skipping"
        )
        return None
    except Exception:
        logger.error(
            f"Failed to add external id claim_id={claim_id} "
            f"system='{system}' external_id='{external_id}'",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def find_by_match_key(
    db_path: str,
    service_date: str,
    billing_practice_id: int,
    billed_amount: float,
) -> Optional[dict]:
    """
    Look up a claim by (service_date, billing_practice_id, billed_amount).

    Uses ABS(billed_amount - ?) < 0.005 for float-safe comparison.
    Returns the claim dict or None.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, service_date, billing_practice_id, encounter_id,
                   insurer_id, billed_amount, current_status
            FROM claims
            WHERE service_date = ?
              AND billing_practice_id = ?
              AND billed_amount IS NOT NULL
              AND ABS(billed_amount - ?) < 0.005
            """,
            (service_date, billing_practice_id, billed_amount),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(
                f"Found claim by match-key id={row['id']} "
                f"service_date={service_date} practice_id={billing_practice_id}"
            )
            return dict(row)
        logger.debug(
            f"No claim found for match-key service_date={service_date} "
            f"practice_id={billing_practice_id} amount={billed_amount}"
        )
        return None
    except Exception:
        logger.error(
            f"Failed to find claim by match-key service_date={service_date} "
            f"practice_id={billing_practice_id} amount={billed_amount}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def find_submitted_by_date_and_practice(
    db_path: str,
    service_date: str,
    practice_id: int,
) -> list[dict]:
    """
    Find all claims still in 'submitted' status for a given service_date and
    billing practice, regardless of billed_amount.

    Used for amount-tolerant claim matching (Phase 12): when no exact match-key
    hit exists, surface prior submitted bills for the same date+practice as
    ambiguity candidates the user can link to.

    Ordered by id ASC (insertion order, oldest first — the claims table has no
    created_at column). Returns [] on failure or when no submitted claims exist.
    Never raises.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, service_date, billing_practice_id, encounter_id,
                   insurer_id, billed_amount, current_status
            FROM claims
            WHERE current_status = 'submitted'
              AND service_date = ?
              AND billing_practice_id = ?
            ORDER BY id ASC
            """,
            (service_date, practice_id),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(
            f"find_submitted_by_date_and_practice: {len(rows)} submitted claim(s) "
            f"service_date={service_date} practice_id={practice_id}"
        )
        return rows
    except Exception:
        logger.error(
            f"Failed to find submitted claims service_date={service_date} "
            f"practice_id={practice_id}",
            exc_info=True,
        )
        return []
    finally:
        conn.close()


def find_by_external_id(
    db_path: str,
    system: str,
    external_id: str,
) -> Optional[dict]:
    """
    Look up a claim by an external identifier (system + external_id).

    Returns the claim dict or None.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.id AS id,
                   c.service_date AS service_date,
                   c.billing_practice_id AS billing_practice_id,
                   c.encounter_id AS encounter_id,
                   c.insurer_id AS insurer_id,
                   c.billed_amount AS billed_amount,
                   c.current_status AS current_status
            FROM claims c
            JOIN claim_external_ids cei ON cei.claim_id = c.id
            WHERE cei.system = ? AND cei.external_id = ?
            """,
            (system, external_id),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(
                f"Found claim by external id system='{system}' "
                f"external_id='{external_id}' -> claim_id={row['id']}"
            )
            return dict(row)
        logger.debug(
            f"No claim found for external id system='{system}' external_id='{external_id}'"
        )
        return None
    except Exception:
        logger.error(
            f"Failed to find claim by external id system='{system}' "
            f"external_id='{external_id}'",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Adjudication lifecycle
# ---------------------------------------------------------------------------

def adjudicate_claim(
    db_path: str,
    claim_id: int,
    adjudication_date: str,
    allowed_amount: Optional[float],
    plan_paid: Optional[float],
    member_owed: Optional[float],
    paid_to_member: Optional[float] = None,
    notes: Optional[str] = None,
) -> Optional[dict]:
    """
    Record an adjudication for a claim. Handles initial and re-adjudications:

    - revision N is computed as MAX(revision) + 1 for this claim (or 1 if none).
    - If N > 1, the immediately prior revision (N-1) has its superseded_by
      pointer set to the new row's id.
    - claims.current_status is updated to 'adjudicated' (N == 1) or
      'readjudicated' (N > 1).
    - A claim_events row is appended ('adjudicated' or 'readjudicated').

    Entire operation runs in a single transaction. Returns the new
    adjudication row dict, or None on failure (transaction rolled back).
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()

        # 1. Determine new revision
        cursor.execute(
            "SELECT MAX(revision) AS max_rev FROM adjudications WHERE claim_id = ?",
            (claim_id,),
        )
        max_row = cursor.fetchone()
        max_rev = max_row["max_rev"] if max_row else None
        new_revision = 1 if max_rev is None else int(max_rev) + 1

        # 2. Insert new adjudication
        cursor.execute(
            """
            INSERT INTO adjudications
                (claim_id, adjudication_date, allowed_amount, plan_paid,
                 member_owed, paid_to_member, revision, superseded_by, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                claim_id,
                adjudication_date,
                allowed_amount,
                plan_paid,
                member_owed,
                paid_to_member,
                new_revision,
                notes,
            ),
        )
        new_adj_id = cursor.lastrowid

        # 4. If a re-adjudication, mark immediate prior as superseded
        if new_revision > 1:
            cursor.execute(
                """
                UPDATE adjudications
                SET superseded_by = ?
                WHERE claim_id = ?
                  AND revision = ?
                  AND superseded_by IS NULL
                """,
                (new_adj_id, claim_id, new_revision - 1),
            )

        # 5. Update claim status
        new_status = "adjudicated" if new_revision == 1 else "readjudicated"
        cursor.execute(
            "UPDATE claims SET current_status = ? WHERE id = ?",
            (new_status, claim_id),
        )

        # 6. Append claim_event
        event_type = "adjudicated" if new_revision == 1 else "readjudicated"
        payload = json.dumps({
            "adjudication_id": new_adj_id,
            "revision": new_revision,
            "adjudication_date": adjudication_date,
            "allowed_amount": allowed_amount,
            "plan_paid": plan_paid,
            "member_owed": member_owed,
            "paid_to_member": paid_to_member,
        })
        _append_event(cursor, claim_id, event_type, payload)

        # 7. Commit and return the new row
        conn.commit()

        cursor.execute(
            """
            SELECT id, claim_id, adjudication_date, allowed_amount, plan_paid,
                   member_owed, paid_to_member, revision, superseded_by, notes
            FROM adjudications
            WHERE id = ?
            """,
            (new_adj_id,),
        )
        row = cursor.fetchone()
        logger.info(
            f"Adjudicated claim_id={claim_id} adj_id={new_adj_id} "
            f"revision={new_revision} status='{new_status}'"
        )
        return dict(row) if row else None
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.error(
                f"Rollback failed for adjudicate_claim claim_id={claim_id}",
                exc_info=True,
            )
        logger.error(
            f"Failed to adjudicate claim_id={claim_id} date={adjudication_date}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Claim events
# ---------------------------------------------------------------------------

def get_claim_events(db_path: str, claim_id: int) -> list[dict]:
    """Return all claim_events rows for a claim, ordered by id ASC (insertion order)."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, claim_id, event_type, payload, occurred_at
            FROM claim_events
            WHERE claim_id = ?
            ORDER BY id ASC
            """,
            (claim_id,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(f"Retrieved {len(rows)} claim_events for claim_id={claim_id}")
        return rows
    except Exception:
        logger.error(
            f"Failed to get claim_events for claim_id={claim_id}",
            exc_info=True,
        )
        return []
    finally:
        conn.close()
