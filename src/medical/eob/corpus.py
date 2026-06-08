"""
Corpus bookkeeping for the EOB pipeline (Phase 3).

``log_unknown`` flags a stored ``documents`` row whose issuer the deterministic
profiles could not recognize, so unknown-issuer EOBs can be surfaced later for
profile authoring. It writes the sentinel note ``'eob:unknown_issuer'`` into the
existing ``documents.notes`` column — no schema migration is required.

Follows the open-commit-close, never-raise pattern used elsewhere in the
documents layer.
"""

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


# Sentinel written into documents.notes to mark an unrecognized-issuer EOB.
_UNKNOWN_ISSUER_FLAG = "eob:unknown_issuer"


def log_unknown(document_id: int, db_path: str) -> Optional[dict]:
    """
    Flag a documents row as having an unrecognized EOB issuer.

    Updates ``documents.notes`` to ``'eob:unknown_issuer'`` for the given
    ``document_id`` and returns the updated row as a dict.

    Never raises — returns None on any failure (including an unknown
    document_id), logging with exc_info=True.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE documents SET notes = ? WHERE id = ?",
            (_UNKNOWN_ISSUER_FLAG, document_id),
        )
        conn.commit()
        cursor.execute("SELECT * FROM documents WHERE id = ?", (document_id,))
        row = cursor.fetchone()
        if row is None:
            logger.warning(
                f"log_unknown: no documents row found for id={document_id}"
            )
            return None
        logger.info(f"log_unknown: flagged document id={document_id} as unknown issuer")
        return dict(row)
    except Exception:
        logger.error(
            f"log_unknown: failed to flag document id={document_id}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()
