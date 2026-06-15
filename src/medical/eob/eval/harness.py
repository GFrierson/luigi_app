"""
Eval harness: run the EOB pipeline over each labeled fixture and write a
per-field results table (Workstream B).

For every expectation under ``expected_dir``:

    1. Deserialize the expected ``EOBDocument``.
    2. Load the fixture PDF bytes (best-effort; missing PDF -> skip + warn).
    3. ``to_document(pdf_bytes)`` — catch ALL exceptions (e.g. ``NotAPdf``);
       on failure emit a "miss" row per expected field.
    4. ``process_eob(doc)`` — ``Unreadable``/``UnknownType`` likewise emit a
       "miss" per expected field.
    5. ``Extracted`` -> parse ungrounded field paths from ``validation.issues``
       (the ``GroundingReport`` is consumed inside the pipeline and not exposed),
       then ``diff_eob`` per field.
    6. Tag each row with ``run_id``/``fixture`` and ``insert_eval_row``.

Total-extraction-failure rows are produced by diffing the expectation against an
empty ``EOBDocument`` — every populated expected field then resolves to "miss",
keeping the accuracy denominator correct. Never raises per fixture: one bad
fixture does not abort the run.

Pattern reference: ``src/medical/eob/corpus.py`` (never-raise, logger pattern).
"""

import glob
import logging
import os
import uuid
from typing import Optional

from src.medical.eob.document import to_document
from src.medical.eob.eval.diff import diff_eob
from src.medical.eob.eval.expectations import Expectation, load_expectation
from src.medical.eob.eval.store import init_eval_db, insert_eval_row
from src.medical.eob.pipeline import process_eob
from src.medical.eob.types import EOBDocument, Extracted

logger = logging.getLogger(__name__)


_UNGROUNDED_PREFIX = "ungrounded field: "

# Confidence stamped on rows for a total extraction failure (no usable output).
_FAILURE_CONFIDENCE = 0.0

# Extractor label for rows where extraction never produced an EOBDocument.
_FAILURE_EXTRACTOR = "none"


def _empty_eob() -> EOBDocument:
    """
    An EOBDocument with no extracted content, for total-failure diffs.

    Every field is empty (including ``subtype``) so each populated expected field
    diffs as a "miss" rather than a spurious "match" — the accuracy denominator
    must count a total failure against all expected fields. ``subtype`` is typed
    as ``EOBSubtype`` (a Literal) but ``""`` is an acceptable runtime sentinel for
    "nothing extracted".
    """
    return EOBDocument(issuer="", subtype="", subscriber="", claims=[])  # type: ignore[arg-type]


def _ungrounded_paths(issues: list[str]) -> list[str]:
    """Parse ungrounded field paths out of validation issue strings."""
    return [
        issue.removeprefix(_UNGROUNDED_PREFIX)
        for issue in issues
        if issue.startswith(_UNGROUNDED_PREFIX)
    ]


def _diff_total_failure(exp: Expectation, extractor: str) -> list[dict]:
    """Emit one 'miss' row per populated expected field for a failed extraction."""
    return diff_eob(
        fixture=exp.fixture,
        insurer=exp.insurer,
        kind=exp.kind,
        expected=exp.eob,
        actual=_empty_eob(),
        extractor=extractor,
        confidence=_FAILURE_CONFIDENCE,
        ungrounded=[],
    )


def _load_pdf_bytes(fixture_dir: str, fixture: str) -> Optional[bytes]:
    """Read fixture PDF bytes; return None (logged) if the file is absent."""
    pdf_path = os.path.join(fixture_dir, f"{fixture}.pdf")
    if not os.path.exists(pdf_path):
        logger.warning(
            f"run_harness: no PDF for fixture={fixture!r} at {pdf_path}; skipping"
        )
        return None
    try:
        with open(pdf_path, "rb") as fh:
            return fh.read()
    except Exception:
        logger.error(
            f"run_harness: failed to read PDF for fixture={fixture!r}",
            exc_info=True,
        )
        return None


def _rows_for_fixture(
    exp: Expectation, pdf_bytes: bytes, llm_override: bool
) -> list[dict]:
    """Run the pipeline for one fixture and return its per-field diff rows."""
    try:
        doc = to_document(pdf_bytes)
    except Exception:
        logger.warning(
            f"run_harness: to_document failed for fixture={exp.fixture!r}; "
            f"emitting miss rows",
            exc_info=True,
        )
        return _diff_total_failure(exp, _FAILURE_EXTRACTOR)

    result = process_eob(doc, llm_override=llm_override)
    if not isinstance(result, Extracted):
        logger.info(
            f"run_harness: fixture={exp.fixture!r} produced "
            f"{type(result).__name__}; emitting miss rows"
        )
        return _diff_total_failure(exp, _FAILURE_EXTRACTOR)

    ungrounded = _ungrounded_paths(result.validation.issues)
    return diff_eob(
        fixture=exp.fixture,
        insurer=exp.insurer,
        kind=exp.kind,
        expected=exp.eob,
        actual=result.eob,
        extractor=result.extractor,
        confidence=result.validation.confidence,
        ungrounded=ungrounded,
    )


def run_harness(
    fixture_dir: str,
    expected_dir: str,
    eval_db_path: str,
    run_id: Optional[str] = None,
    llm_override: bool = False,
) -> str:
    """
    Run the eval over every expectation in ``expected_dir`` and write the
    per-field results to ``eval_db_path``.

    Returns the ``run_id`` used (generated if not supplied). Never raises — a
    failing fixture is logged and skipped.
    """
    run_id = run_id or uuid.uuid4().hex
    init_eval_db(eval_db_path)

    expectation_files = sorted(glob.glob(os.path.join(expected_dir, "*.json")))
    if not expectation_files:
        logger.warning(
            f"run_harness: no expectation files found in {expected_dir}"
        )

    for json_path in expectation_files:
        exp = load_expectation(json_path)
        if exp is None:
            continue

        pdf_bytes = _load_pdf_bytes(fixture_dir, exp.fixture)
        if pdf_bytes is None:
            continue

        rows = _rows_for_fixture(exp, pdf_bytes, llm_override)
        for row in rows:
            row["run_id"] = run_id
            row["fixture"] = exp.fixture
            insert_eval_row(eval_db_path, row)

        logger.info(
            f"run_harness: fixture={exp.fixture!r} wrote {len(rows)} rows "
            f"(run_id={run_id})"
        )

    return run_id
