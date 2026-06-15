"""
Tests for the EOB eval harness (Workstream B).

Covers:
    src/medical/eob/eval/store.py    (init_eval_db, insert_eval_row, get_eval_results)
    src/medical/eob/eval/diff.py     (diff_eob — pure per-field diff)
    src/medical/eob/eval/harness.py  (run_harness; to_document + process_eob mocked)
    src/medical/eob/eval/report.py   (accuracy_by_insurer_kind, worst_buckets)

No live PDF or LLM calls — ``to_document`` and ``process_eob`` are mocked at the
harness boundary. SQLite is never mocked (per testing rules).
"""

import os
from unittest.mock import patch

from src.medical.eob.document import NotAPdf
from src.medical.eob.eval.diff import diff_eob
from src.medical.eob.eval.harness import run_harness
from src.medical.eob.eval.report import accuracy_by_insurer_kind, load_results, worst_buckets
from src.medical.eob.eval.store import (
    get_eval_results,
    init_eval_db,
    insert_eval_row,
)
from src.medical.eob.types import (
    Claim,
    Document,
    EOBDocument,
    Extracted,
    LineItem,
    PdfKind,
    Unreadable,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _line_item(**overrides: str) -> LineItem:
    base = {f: "" for f in LineItem.__annotations__}
    base.update(overrides)
    return LineItem(**base)


def _claim(**overrides) -> Claim:
    base: dict = {
        "patient": "JANE DOE",
        "claim_number": "C001",
        "received_date": "2026-05-12",
        "provider": "RIVERSIDE CLINIC",
        "in_network": True,
        "patient_owes": "$25.00",
        "line_items": [],
    }
    base.update(overrides)
    return Claim(**base)


def _eob(**overrides) -> EOBDocument:
    base: dict = {
        "issuer": "anthem",
        "subtype": "summary",
        "subscriber": "JANE DOE",
        "claims": [],
    }
    base.update(overrides)
    return EOBDocument(**base)


def _row(**overrides) -> dict:
    base: dict = {
        "run_id": "R1",
        "fixture": "f1",
        "insurer": "anthem",
        "kind": "text",
        "subtype": "summary",
        "block_type": "header",
        "field": "subscriber",
        "extractor": "anthem",
        "expected": "JANE DOE",
        "actual": "JANE DOE",
        "outcome": "match",
        "confidence": 0.95,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------

def test_store_init_is_idempotent(tmp_path):
    db = str(tmp_path / "eval.db")
    init_eval_db(db)
    init_eval_db(db)  # second call must not raise
    # Table exists and is queryable.
    assert get_eval_results(db) == []


def test_insert_and_fetch_round_trips(tmp_path):
    db = str(tmp_path / "eval.db")
    init_eval_db(db)
    insert_eval_row(db, _row(field="issuer", outcome="match", confidence=0.9))

    rows = get_eval_results(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["run_id"] == "R1"
    assert r["fixture"] == "f1"
    assert r["insurer"] == "anthem"
    assert r["kind"] == "text"
    assert r["subtype"] == "summary"
    assert r["field"] == "issuer"
    assert r["expected"] == "JANE DOE"
    assert r["actual"] == "JANE DOE"
    assert r["outcome"] == "match"
    assert r["confidence"] == 0.9


def test_get_eval_results_filters_by_run_id(tmp_path):
    db = str(tmp_path / "eval.db")
    init_eval_db(db)
    insert_eval_row(db, _row(run_id="A"))
    insert_eval_row(db, _row(run_id="B"))

    only_a = get_eval_results(db, run_id="A")
    assert len(only_a) == 1
    assert only_a[0]["run_id"] == "A"


# ---------------------------------------------------------------------------
# diff_eob
# ---------------------------------------------------------------------------

def test_diff_eob_match():
    eob = _eob(claims=[_claim(line_items=[_line_item(service="Office Visit")])])
    rows = diff_eob("f", "anthem", "text", eob, eob, "anthem", 0.95, [])
    assert rows  # non-empty
    assert all(r["outcome"] == "match" for r in rows)


def test_diff_eob_mismatch():
    expected = _eob(subscriber="JANE DOE")
    actual = _eob(subscriber="JOHN DOE")
    rows = diff_eob("f", "anthem", "text", expected, actual, "anthem", 0.95, [])
    sub = next(r for r in rows if r["field"] == "subscriber")
    assert sub["outcome"] == "mismatch"
    # Unchanged fields stay match.
    issuer = next(r for r in rows if r["field"] == "issuer")
    assert issuer["outcome"] == "match"


def test_diff_eob_miss():
    expected = _eob(subscriber="JANE DOE")
    actual = _eob(subscriber="")
    rows = diff_eob("f", "anthem", "text", expected, actual, "anthem", 0.95, [])
    sub = next(r for r in rows if r["field"] == "subscriber")
    assert sub["outcome"] == "miss"


def test_diff_eob_ungrounded():
    # Values match, but the field is flagged ungrounded -> ungrounded wins.
    eob = _eob(subscriber="JANE DOE")
    rows = diff_eob("f", "anthem", "text", eob, eob, "anthem", 0.95, ["subscriber"])
    sub = next(r for r in rows if r["field"] == "subscriber")
    assert sub["outcome"] == "ungrounded"


def test_diff_eob_claim_count_mismatch():
    expected = _eob(claims=[_claim(claim_number="C001"), _claim(claim_number="C002")])
    actual = _eob(claims=[_claim(claim_number="C001")])
    rows = diff_eob("f", "anthem", "text", expected, actual, "anthem", 0.95, [])

    # No IndexError, and the missing second claim's fields are all "miss".
    missing = [r for r in rows if r["field"].startswith("claims[1].")]
    assert missing  # the second claim was enumerated
    assert all(r["outcome"] == "miss" for r in missing)
    # The present first claim still matches.
    first = next(r for r in rows if r["field"] == "claims[0].claim_number")
    assert first["outcome"] == "match"


# ---------------------------------------------------------------------------
# run_harness
# ---------------------------------------------------------------------------

def _write_expectation(expected_dir: str, fixture: str, insurer: str) -> None:
    import json

    os.makedirs(expected_dir, exist_ok=True)
    payload = {
        "fixture": fixture,
        "insurer": insurer,
        "kind": "text",
        "subtype": "summary",
        "eob": {
            "issuer": insurer,
            "subtype": "summary",
            "subscriber": "JANE DOE",
            "claims": [
                {
                    "patient": "JANE DOE",
                    "claim_number": "C001",
                    "received_date": "2026-05-12",
                    "provider": "RIVERSIDE CLINIC",
                    "in_network": True,
                    "patient_owes": "$25.00",
                    "line_items": [],
                }
            ],
        },
    }
    with open(os.path.join(expected_dir, f"{fixture}.json"), "w") as fh:
        json.dump(payload, fh)


def test_run_harness_writes_rows(tmp_path):
    fixture_dir = str(tmp_path / "fix")
    expected_dir = str(tmp_path / "expected")
    db = str(tmp_path / "eval.db")
    os.makedirs(fixture_dir)
    _write_expectation(expected_dir, "anthem_summary_01", "anthem")
    # PDF bytes only need to exist; to_document is mocked.
    with open(os.path.join(fixture_dir, "anthem_summary_01.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4 fake")

    doc = Document(text="x", words=[], page_images=[], source=PdfKind.TEXT)
    extracted_eob = _eob(
        claims=[
            _claim(
                patient="JANE DOE",
                claim_number="C001",
                received_date="2026-05-12",
                provider="RIVERSIDE CLINIC",
                in_network=True,
                patient_owes="$25.00",
                line_items=[],
            )
        ]
    )
    result = Extracted(
        eob=extracted_eob,
        validation=ValidationResult(ok=True, confidence=0.95, issues=[]),
        extractor="anthem",
    )

    with patch("src.medical.eob.eval.harness.to_document", return_value=doc), patch(
        "src.medical.eob.eval.harness.process_eob", return_value=result
    ):
        run_id = run_harness(fixture_dir, expected_dir, db, run_id="RUN1")

    assert run_id == "RUN1"
    rows = get_eval_results(db, run_id="RUN1")
    assert rows
    assert all(r["fixture"] == "anthem_summary_01" for r in rows)
    assert all(r["insurer"] == "anthem" for r in rows)
    # A clean match across all populated fields.
    assert all(r["outcome"] == "match" for r in rows)
    # The subscriber field is present and matched.
    sub = next(r for r in rows if r["field"] == "subscriber")
    assert sub["outcome"] == "match"


def test_run_harness_total_failure_emits_miss_per_field(tmp_path):
    fixture_dir = str(tmp_path / "fix")
    expected_dir = str(tmp_path / "expected")
    db = str(tmp_path / "eval.db")
    os.makedirs(fixture_dir)
    _write_expectation(expected_dir, "anthem_summary_01", "anthem")
    with open(os.path.join(fixture_dir, "anthem_summary_01.pdf"), "wb") as fh:
        fh.write(b"garbage")

    # to_document raising NotAPdf must not crash; every expected field -> miss.
    with patch(
        "src.medical.eob.eval.harness.to_document", side_effect=NotAPdf("nope")
    ):
        run_harness(fixture_dir, expected_dir, db, run_id="FAIL")

    rows = get_eval_results(db, run_id="FAIL")
    assert rows
    # Every populated expected field is a miss against the empty actual.
    populated = [r for r in rows if r["expected"]]
    assert populated
    assert all(r["outcome"] == "miss" for r in populated)
    assert all(r["extractor"] == "none" for r in rows)


def test_run_harness_unreadable_emits_miss(tmp_path):
    fixture_dir = str(tmp_path / "fix")
    expected_dir = str(tmp_path / "expected")
    db = str(tmp_path / "eval.db")
    os.makedirs(fixture_dir)
    _write_expectation(expected_dir, "anthem_summary_01", "anthem")
    with open(os.path.join(fixture_dir, "anthem_summary_01.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4 fake")

    doc = Document(text="", words=[], page_images=[], source=PdfKind.IMAGE)
    with patch(
        "src.medical.eob.eval.harness.to_document", return_value=doc
    ), patch(
        "src.medical.eob.eval.harness.process_eob",
        return_value=Unreadable("no legible text"),
    ):
        run_harness(fixture_dir, expected_dir, db, run_id="UNREAD")

    rows = get_eval_results(db, run_id="UNREAD")
    populated = [r for r in rows if r["expected"]]
    assert populated
    assert all(r["outcome"] == "miss" for r in populated)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def test_accuracy_by_insurer_kind(tmp_path):
    db = str(tmp_path / "eval.db")
    init_eval_db(db)
    # anthem/text: 3 of 4 match -> 0.75
    insert_eval_row(db, _row(insurer="anthem", kind="text", outcome="match"))
    insert_eval_row(db, _row(insurer="anthem", kind="text", outcome="match"))
    insert_eval_row(db, _row(insurer="anthem", kind="text", outcome="match"))
    insert_eval_row(db, _row(insurer="anthem", kind="text", outcome="miss"))
    # cigna/image: 0 of 2 match -> 0.0
    insert_eval_row(db, _row(insurer="cigna", kind="image", outcome="mismatch"))
    insert_eval_row(db, _row(insurer="cigna", kind="image", outcome="miss"))

    df = load_results(db)
    report = accuracy_by_insurer_kind(df)

    anthem = report[(report["insurer"] == "anthem") & (report["kind"] == "text")]
    cigna = report[(report["insurer"] == "cigna") & (report["kind"] == "image")]
    assert float(anthem["accuracy"].iloc[0]) == 0.75
    assert float(cigna["accuracy"].iloc[0]) == 0.0
    # Worst bucket sorts first.
    assert report.iloc[0]["insurer"] == "cigna"


def test_worst_buckets_returns_sorted(tmp_path):
    db = str(tmp_path / "eval.db")
    init_eval_db(db)
    # field A: perfect
    insert_eval_row(db, _row(field="issuer", outcome="match"))
    # field B: half
    insert_eval_row(db, _row(field="subscriber", outcome="match"))
    insert_eval_row(db, _row(field="subscriber", outcome="miss"))
    # field C: zero
    insert_eval_row(db, _row(field="claims[0].provider", outcome="mismatch"))

    df = load_results(db)
    buckets = worst_buckets(df, n=10)

    accuracies = list(buckets["accuracy"])
    assert accuracies == sorted(accuracies)  # ascending, worst first
    assert float(buckets.iloc[0]["accuracy"]) == 0.0
    assert buckets.iloc[0]["field"] == "claims[0].provider"
