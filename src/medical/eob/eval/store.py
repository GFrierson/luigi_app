"""
Persistence for the per-failure-mode eval harness (Workstream B).

The ``eval_results`` table holds one row per (fixture, claim, field) diffed by
the harness, tagged with the failure-mode dimensions (insurer, kind, subtype,
block_type, field). Reports (``report.py``) are ``pandas.groupby`` over this
table, so the cutover gate can read accuracy per bucket rather than a single
aggregate.

Follows the open-commit-close, never-raise pattern used elsewhere in the EOB
data layer (see ``src/medical/eob/corpus.py``).
"""

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


# Columns written by the harness, in insert order. ``run_id``, ``fixture`` and
# the diff columns are all supplied per row; ``ts`` defaults in SQL.
_INSERT_COLUMNS = (
    "run_id",
    "fixture",
    "insurer",
    "kind",
    "subtype",
    "block_type",
    "field",
    "extractor",
    "expected",
    "actual",
    "outcome",
    "confidence",
)


def init_eval_db(db_path: str) -> None:
    """
    Create the ``eval_results`` table if it does not exist.

    Idempotent: safe to call repeatedly. Never raises — logs on failure.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_results (
                run_id     TEXT NOT NULL,
                fixture    TEXT NOT NULL,
                insurer    TEXT,
                kind       TEXT,
                subtype    TEXT,
                block_type TEXT,
                field      TEXT,
                extractor  TEXT,
                expected   TEXT,
                actual     TEXT,
                outcome    TEXT,
                confidence REAL,
                ts         DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    except Exception:
        logger.error(f"init_eval_db: failed to init {db_path}", exc_info=True)
    finally:
        conn.close()


def insert_eval_row(db_path: str, row: dict) -> None:
    """
    Insert one diff row into ``eval_results``.

    ``row`` must carry the keys in ``_INSERT_COLUMNS`` (missing keys default to
    None). Parameterized insert. Never raises — logs on failure.
    """
    conn = sqlite3.connect(db_path)
    try:
        values = tuple(row.get(col) for col in _INSERT_COLUMNS)
        placeholders = ", ".join("?" for _ in _INSERT_COLUMNS)
        columns = ", ".join(_INSERT_COLUMNS)
        conn.execute(
            f"INSERT INTO eval_results ({columns}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
    except Exception:
        logger.error(
            f"insert_eval_row: failed to insert row for fixture="
            f"{row.get('fixture')!r} field={row.get('field')!r}",
            exc_info=True,
        )
    finally:
        conn.close()


def get_eval_results(db_path: str, run_id: Optional[str] = None) -> list[dict]:
    """
    Fetch eval rows as dicts, optionally filtered to a single ``run_id``.

    Never raises — returns an empty list on failure.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if run_id is None:
            cursor.execute("SELECT * FROM eval_results")
        else:
            cursor.execute(
                "SELECT * FROM eval_results WHERE run_id = ?", (run_id,)
            )
        return [dict(r) for r in cursor.fetchall()]
    except Exception:
        logger.error(
            f"get_eval_results: failed to fetch from {db_path} "
            f"(run_id={run_id!r})",
            exc_info=True,
        )
        return []
    finally:
        conn.close()
