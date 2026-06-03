"""
Document layout learning (Phase 11).

Learns which pages of a multi-page document actually carry content for a given
(doc_type, practice) combination, so that future extractions can skip filler
pages (cover sheets, blank backs, marketing inserts) before the LLM call.

Two pure helpers operate on extracted page text:
    - score_page_relevance(page_text) -> float
    - detect_relevant_pages(pages, threshold) -> list[int]

Two DB helpers persist and recall learned templates (schema owned by
src.database.init_db()):
    - load_template(db_path, doc_type, practice_id) -> Optional[list[int]]
    - update_template(db_path, doc_type, practice_id, observed_pages) -> None

The DB helpers follow the project pattern: synchronous, never raise (log with
exc_info=True and return None / a default), always close the connection.
"""

import json
import logging
import sqlite3
from typing import Optional

from src.database import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure scoring helpers
# ---------------------------------------------------------------------------

def score_page_relevance(page_text: str) -> float:
    """
    Score a page's relevance as the ratio of non-whitespace characters to total
    characters. Returns 0.0 for empty text (avoids ZeroDivisionError).
    """
    if not page_text:
        return 0.0
    total = len(page_text)
    if total == 0:
        return 0.0
    non_ws = sum(1 for ch in page_text if not ch.isspace())
    return non_ws / total


def detect_relevant_pages(pages: list[str], threshold: float = 0.05) -> list[int]:
    """
    Return the 0-based indices of pages whose relevance score exceeds
    `threshold`, with leading and trailing below-threshold pages stripped.

    Below-threshold pages sandwiched between above-threshold pages are kept, so
    the result is a contiguous span from the first to the last relevant page.

    Returns [] when no page exceeds the threshold — callers must handle the
    empty case and fall back to all pages.
    """
    above = [i for i, page in enumerate(pages) if score_page_relevance(page) > threshold]
    if not above:
        return []
    first, last = above[0], above[-1]
    return list(range(first, last + 1))


# ---------------------------------------------------------------------------
# Template persistence
# ---------------------------------------------------------------------------

def load_template(
    db_path: str,
    doc_type: str,
    practice_id: Optional[int],
) -> Optional[list[int]]:
    """
    Load the learned relevant-page list for a (doc_type, practice_id) pair.

    Returns the JSON-decoded list[int], or None if no template exists or on
    failure (logged).
    """
    conn = get_connection(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT relevant_pages
            FROM document_templates
            WHERE doc_type = ? AND COALESCE(practice_id, 0) = COALESCE(?, 0)
            """,
            (doc_type, practice_id),
        )
        row = cursor.fetchone()
        if row is None:
            logger.debug(
                f"load_template: no template for doc_type='{doc_type}' "
                f"practice_id={practice_id}"
            )
            return None
        pages = json.loads(row["relevant_pages"])
        logger.debug(
            f"load_template: doc_type='{doc_type}' practice_id={practice_id} "
            f"-> {pages}"
        )
        return pages
    except Exception:
        logger.error(
            f"load_template: failed for doc_type='{doc_type}' "
            f"practice_id={practice_id}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def update_template(
    db_path: str,
    doc_type: str,
    practice_id: Optional[int],
    observed_pages: list[int],
) -> None:
    """
    Upsert a learned template, unioning the newly observed relevant pages with
    any previously stored set (sorted). Increments sample_count on update.

    Never raises — logs with exc_info=True and returns None on failure.
    """
    conn = get_connection(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Read existing pages first so we can store the union.
        cursor.execute(
            """
            SELECT relevant_pages
            FROM document_templates
            WHERE doc_type = ? AND COALESCE(practice_id, 0) = COALESCE(?, 0)
            """,
            (doc_type, practice_id),
        )
        row = cursor.fetchone()
        stored: list[int] = []
        if row is not None:
            try:
                stored = json.loads(row["relevant_pages"])
            except Exception:
                logger.error(
                    f"update_template: failed to decode stored pages for "
                    f"doc_type='{doc_type}' practice_id={practice_id}; treating "
                    f"as empty",
                    exc_info=True,
                )
                stored = []

        union_pages = sorted(set(stored) | set(observed_pages))
        union_json = json.dumps(union_pages)

        cursor.execute(
            """
            INSERT INTO document_templates (doc_type, practice_id, relevant_pages, sample_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(doc_type, COALESCE(practice_id, 0)) DO UPDATE SET
                relevant_pages = excluded.relevant_pages,
                sample_count = sample_count + 1,
                updated_at = datetime('now')
            """,
            (doc_type, practice_id, union_json),
        )
        conn.commit()
        logger.info(
            f"update_template: doc_type='{doc_type}' practice_id={practice_id} "
            f"-> {union_pages}"
        )
        return None
    except Exception:
        logger.error(
            f"update_template: failed for doc_type='{doc_type}' "
            f"practice_id={practice_id}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()
