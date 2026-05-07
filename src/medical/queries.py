"""
Reconciliation queries (Phase 5).

Read-only helpers built on top of the SQL views defined in src.database.init_db():
- v_claim_obligation
- v_member_holds
- v_encounter_balance

Plus a direct query against `claim_events` for "today's re-adjudications".

All public functions:
- are synchronous (callers should wrap in asyncio.to_thread() inside async code)
- return dict | None or list[dict]
- never raise — they catch exceptions, log with exc_info=True, and return None / []
"""

import logging
from typing import Optional

from src.database import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-claim / per-practice / global net-obligation queries
# ---------------------------------------------------------------------------

def get_claim_obligation(db_path: str, claim_id: int) -> Optional[dict]:
    """Return the v_claim_obligation row for a single claim, or None."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT claim_id, service_date, billing_practice_id, practice_name,
                   billed_amount, member_owed, payments_applied, net_obligation
            FROM v_claim_obligation
            WHERE claim_id = ?
            """,
            (claim_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        logger.debug(f"No v_claim_obligation row for claim_id={claim_id}")
        return None
    except Exception:
        logger.error(
            f"Failed to get_claim_obligation for claim_id={claim_id}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def get_obligations_by_practice(db_path: str, practice_id: int) -> list[dict]:
    """Return all v_claim_obligation rows for a practice, newest service_date first."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT claim_id, service_date, billing_practice_id, practice_name,
                   billed_amount, member_owed, payments_applied, net_obligation
            FROM v_claim_obligation
            WHERE billing_practice_id = ?
            ORDER BY service_date DESC
            """,
            (practice_id,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(
            f"Retrieved {len(rows)} obligations for practice_id={practice_id}"
        )
        return rows
    except Exception:
        logger.error(
            f"Failed to get_obligations_by_practice practice_id={practice_id}",
            exc_info=True,
        )
        return []
    finally:
        conn.close()


def get_global_obligations(db_path: str) -> list[dict]:
    """Return every v_claim_obligation row, ordered by practice then service_date."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT claim_id, service_date, billing_practice_id, practice_name,
                   billed_amount, member_owed, payments_applied, net_obligation
            FROM v_claim_obligation
            ORDER BY billing_practice_id, service_date
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(f"Retrieved {len(rows)} global obligation rows")
        return rows
    except Exception:
        logger.error("Failed to get_global_obligations", exc_info=True)
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Per-encounter rollup
# ---------------------------------------------------------------------------

def get_encounter_balance(db_path: str, encounter_id: int) -> Optional[dict]:
    """Return the v_encounter_balance row for a single encounter, or None."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT encounter_id, service_date, practice_id, practice_name,
                   total_net_obligation
            FROM v_encounter_balance
            WHERE encounter_id = ?
            """,
            (encounter_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        logger.debug(f"No v_encounter_balance row for encounter_id={encounter_id}")
        return None
    except Exception:
        logger.error(
            f"Failed to get_encounter_balance encounter_id={encounter_id}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Member-holds (insurer→member payments not yet forwarded)
# ---------------------------------------------------------------------------

def get_member_holds_overdue(db_path: str, days_threshold: int = 7) -> list[dict]:
    """Return v_member_holds rows where days_held > days_threshold."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT claim_id, billing_practice_id, practice_name, service_date,
                   payment_id, payment_date, held_amount, days_held
            FROM v_member_holds
            WHERE days_held > ?
            ORDER BY days_held DESC
            """,
            (days_threshold,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(
            f"Retrieved {len(rows)} member-hold rows over {days_threshold} days"
        )
        return rows
    except Exception:
        logger.error(
            f"Failed to get_member_holds_overdue threshold={days_threshold}",
            exc_info=True,
        )
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Re-adjudication views (current re-adjudicated claims + today's events)
# ---------------------------------------------------------------------------

def get_readjudicated_claims(db_path: str) -> list[dict]:
    """
    Return claims whose current (non-superseded) adjudication is revision > 1.

    Joined with practices for human-readable practice_name.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.id              AS claim_id,
                   c.service_date    AS service_date,
                   pr.name           AS practice_name,
                   a.revision        AS revision,
                   a.adjudication_date AS adjudication_date
            FROM claims c
            JOIN adjudications a ON a.claim_id = c.id
            JOIN practices pr    ON pr.id = c.billing_practice_id
            WHERE a.superseded_by IS NULL
              AND a.revision > 1
            ORDER BY a.adjudication_date DESC
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(f"Retrieved {len(rows)} re-adjudicated claims")
        return rows
    except Exception:
        logger.error("Failed to get_readjudicated_claims", exc_info=True)
        return []
    finally:
        conn.close()


def get_recent_readjudication_events(db_path: str) -> list[dict]:
    """
    Return claim_events of type 'readjudicated' that occurred since the start
    of today (server local date). Used by the daily re-adjudication alert job.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, claim_id, event_type, payload, occurred_at
            FROM claim_events
            WHERE event_type = 'readjudicated'
              AND occurred_at >= date('now', 'start of day')
            ORDER BY occurred_at ASC
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(f"Retrieved {len(rows)} re-adjudication events for today")
        return rows
    except Exception:
        logger.error("Failed to get_recent_readjudication_events", exc_info=True)
        return []
    finally:
        conn.close()
