"""
Payment recording and application helpers (Phase 3).

Tables managed here (schema owned by src.database.init_db()):
- payments
- payment_applications

A `payment` is any movement of money between two parties (insurer/member/
practice/hsa/fsa). A `payment_application` allocates some or all of a payment
to a specific claim. The unique key (payment_id, claim_id) means a single
payment cannot be applied to the same claim twice; split payments across
multiple claims by recording multiple payment_applications rows with different
applied_amount values.

All public functions:
- are synchronous (callers should wrap in asyncio.to_thread() inside async code)
- return dict | None or list[dict]
- never raise — they catch exceptions, log with exc_info=True, and return None / []
"""

import logging
import sqlite3
from typing import Optional

from src.database import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

def record_payment(
    db_path: str,
    payment_date: str,
    amount: float,
    from_party: str,
    to_party: str,
    method: Optional[str],
    reference: Optional[str],
    notes: Optional[str],
) -> Optional[dict]:
    """
    Insert a new payment row. Returns the created row dict, or None on failure.

    `from_party` / `to_party` must be one of: 'insurer','member','hsa','fsa','practice'
    (enforced by CHECK constraint at the DB layer).
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO payments
                (payment_date, amount, from_party, to_party, method, reference, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (payment_date, amount, from_party, to_party, method, reference, notes),
        )
        payment_id = cursor.lastrowid
        conn.commit()

        cursor.execute(
            """
            SELECT id, payment_date, amount, from_party, to_party,
                   method, reference, notes, created_at
            FROM payments
            WHERE id = ?
            """,
            (payment_id,),
        )
        row = cursor.fetchone()
        logger.info(
            f"Recorded payment id={payment_id} date={payment_date} amount={amount} "
            f"from='{from_party}' to='{to_party}'"
        )
        return dict(row) if row else None
    except Exception:
        logger.error(
            f"Failed to record payment date={payment_date} amount={amount} "
            f"from='{from_party}' to='{to_party}'",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def apply_payment(
    db_path: str,
    payment_id: int,
    claim_id: int,
    applied_amount: float,
) -> Optional[dict]:
    """
    Allocate a portion of a payment to a specific claim.

    Returns the inserted payment_applications row dict, or None on duplicate
    UNIQUE(payment_id, claim_id) / failure.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO payment_applications (payment_id, claim_id, applied_amount)
            VALUES (?, ?, ?)
            """,
            (payment_id, claim_id, applied_amount),
        )
        application_id = cursor.lastrowid
        conn.commit()

        cursor.execute(
            """
            SELECT id, payment_id, claim_id, applied_amount
            FROM payment_applications
            WHERE id = ?
            """,
            (application_id,),
        )
        row = cursor.fetchone()
        logger.info(
            f"Applied payment_id={payment_id} to claim_id={claim_id} "
            f"applied_amount={applied_amount} (application_id={application_id})"
        )
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        logger.debug(
            f"Duplicate payment_application payment_id={payment_id} "
            f"claim_id={claim_id}, skipping"
        )
        return None
    except Exception:
        logger.error(
            f"Failed to apply payment_id={payment_id} to claim_id={claim_id} "
            f"applied_amount={applied_amount}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def get_payment_applications(
    db_path: str,
    claim_id: int,
) -> list[dict]:
    """
    Return all payment applications for a claim, joined to their parent payment.

    Each row contains the full payment_applications columns plus payment_date,
    amount, from_party, to_party, method, and reference from the joined
    payments row.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT pa.id             AS id,
                   pa.payment_id     AS payment_id,
                   pa.claim_id       AS claim_id,
                   pa.applied_amount AS applied_amount,
                   p.payment_date    AS payment_date,
                   p.amount          AS amount,
                   p.from_party      AS from_party,
                   p.to_party        AS to_party,
                   p.method          AS method,
                   p.reference       AS reference
            FROM payment_applications pa
            JOIN payments p ON p.id = pa.payment_id
            WHERE pa.claim_id = ?
            ORDER BY pa.id ASC
            """,
            (claim_id,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(
            f"Retrieved {len(rows)} payment_application(s) for claim_id={claim_id}"
        )
        return rows
    except Exception:
        logger.error(
            f"Failed to get payment_applications for claim_id={claim_id}",
            exc_info=True,
        )
        return []
    finally:
        conn.close()
