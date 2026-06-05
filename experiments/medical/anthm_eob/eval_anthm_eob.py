"""
Local eval harness for the Anthem EOB deterministic extractor.

Reads ``annotations.csv`` (rows with ``review_status == "verified"`` only),
runs each annotated PDF through ``to_document`` -> ``process_eob``, and compares
the hypothesized issuer / subtype / subscriber / claim_count against the
verified ground truth.

Gate: passes vacuously (with a warning) while fewer than ``_MIN_SAMPLES``
verified rows exist; once there are at least ``_MIN_SAMPLES``, requires field
precision >= ``_PRECISION_THRESHOLD``.

This is manual/local-only and is intentionally NOT wired into
``run_all_extractor_evals.py`` until enough samples are annotated.
"""

import csv
import logging
import os

from src.medical.eob.document import to_document
from src.medical.eob.pipeline import process_eob
from src.medical.eob.types import Extracted

logger = logging.getLogger(__name__)


_ANNOTATIONS_CSV = os.path.join(os.path.dirname(__file__), "annotations.csv")

_MIN_SAMPLES = 15
_PRECISION_THRESHOLD = 0.90


def _load_verified_rows(csv_path: str) -> list[dict]:
    """Return annotation rows whose review_status == 'verified'."""
    rows: list[dict] = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (row.get("review_status") or "").strip().lower() == "verified":
                    rows.append(row)
    except FileNotFoundError:
        logger.warning("eval_anthm_eob: annotations file not found at %s", csv_path)
    except Exception:
        logger.error("eval_anthm_eob: failed to read annotations", exc_info=True)
    return rows


def _hypothesize(file_path: str) -> dict:
    """Run the pipeline over a PDF and return hypothesized comparison fields."""
    try:
        with open(file_path, "rb") as f:
            doc = to_document(f.read())
        result = process_eob(doc)
        if isinstance(result, Extracted):
            return {
                "issuer": result.extractor,
                "subtype": result.eob.subtype,
                "subscriber": result.eob.subscriber,
                "claim_count": str(len(result.eob.claims)),
            }
        return {"issuer": "", "subtype": "", "subscriber": "", "claim_count": "0"}
    except Exception:
        logger.error(
            "eval_anthm_eob: hypothesis failed for %s", file_path, exc_info=True
        )
        return {"issuer": "", "subtype": "", "subscriber": "", "claim_count": "0"}


def _compare(row: dict, hyp: dict) -> tuple[int, int]:
    """Return (correct_fields, total_fields) for one annotated row."""
    checks = [
        ((row.get("true_issuer") or "").strip(), hyp["issuer"].strip()),
        ((row.get("true_subtype") or "").strip(), hyp["subtype"].strip()),
        (
            (row.get("true_subscriber") or "").strip().lower(),
            hyp["subscriber"].strip().lower(),
        ),
        ((row.get("true_claim_count") or "").strip(), hyp["claim_count"].strip()),
    ]
    correct = sum(1 for truth, got in checks if truth == got)
    return correct, len(checks)


def run_eval() -> dict:
    """
    Run the Anthem EOB eval and return ``{"passed", "precision", "n"}``.

    Passes vacuously when n < _MIN_SAMPLES; otherwise requires precision >=
    _PRECISION_THRESHOLD.
    """
    rows = _load_verified_rows(_ANNOTATIONS_CSV)
    n = len(rows)

    if n < _MIN_SAMPLES:
        logger.warning(
            "eval_anthm_eob: only %d verified sample(s) (< %d); passing "
            "vacuously until more EOBs are annotated.",
            n,
            _MIN_SAMPLES,
        )
        return {"passed": True, "precision": 0.0, "n": n}

    total_correct = 0
    total_fields = 0
    for row in rows:
        hyp = _hypothesize((row.get("file_path") or "").strip())
        correct, fields = _compare(row, hyp)
        total_correct += correct
        total_fields += fields

    precision = total_correct / total_fields if total_fields else 0.0
    passed = precision >= _PRECISION_THRESHOLD
    logger.info(
        "eval_anthm_eob: n=%d precision=%.3f passed=%s", n, precision, passed
    )
    return {"passed": passed, "precision": precision, "n": n}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    outcome = run_eval()
    logger.info("eval_anthm_eob result: %s", outcome)
