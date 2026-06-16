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


def test_diff_eob_duplicate_line_items_full_match():
    """3 identical expected line items vs 3 identical actual → all match, zero miss/spurious."""
    item = _line_item(service_date="2026-01-01", service="Office Visit", copay="$20.00")
    claim = _claim(line_items=[item, item, item])
    eob = _eob(claims=[claim])
    rows = diff_eob("f", "anthem", "text", eob, eob, "anthem", 0.95, [])
    line_item_rows = [r for r in rows if "line_items" in r["field"]]
    assert line_item_rows  # items were diffed
    assert all(r["outcome"] == "match" for r in line_item_rows), (
        [r for r in line_item_rows if r["outcome"] != "match"]
    )


def test_diff_eob_duplicate_under_extraction():
    """3 identical expected vs 2 actual → 2 match, 1 miss, 0 spurious."""
    item = _line_item(service_date="2026-01-01", service="Office Visit", copay="$20.00")
    exp_claim = _claim(line_items=[item, item, item])
    act_claim = _claim(line_items=[item, item])
    rows = diff_eob(
        "f", "anthem", "text",
        _eob(claims=[exp_claim]), _eob(claims=[act_claim]),
        "anthem", 0.95, [],
    )
    li_rows = [r for r in rows if "line_items" in r["field"]]
    assert any(r["outcome"] == "miss" for r in li_rows), "expected at least one miss"
    assert not any(r["outcome"] == "spurious" for r in li_rows), "no spurious expected"


def test_diff_eob_duplicate_over_extraction():
    """2 identical expected vs 3 actual → 2 match, 1 spurious, 0 miss."""
    item = _line_item(service_date="2026-01-01", service="Office Visit", copay="$20.00")
    exp_claim = _claim(line_items=[item, item])
    act_claim = _claim(line_items=[item, item, item])
    rows = diff_eob(
        "f", "anthem", "text",
        _eob(claims=[exp_claim]), _eob(claims=[act_claim]),
        "anthem", 0.95, [],
    )
    li_rows = [r for r in rows if "line_items" in r["field"]]
    assert any(r["outcome"] == "spurious" for r in li_rows), "expected a spurious row"
    assert not any(r["outcome"] == "miss" for r in li_rows), "no miss expected"


def test_diff_eob_spurious_line_item():
    """Expected 1 item, actual 2 where second shares zero monetary fields with expected.

    Guards against the coincidental-zero-monetary case: a second actual item that
    shares no identity fields must NOT qualify as a match candidate, even if both
    have 0-valued monetary fields.
    """
    expected_item = _line_item(
        service_date="2026-01-01", service="Office Visit", reason_code="N130",
        copay="$20.00", deductible="$0.00",
    )
    # Second actual item: completely different identity, all zero monetary.
    spurious_item = _line_item(
        service_date="2026-02-15", service="Lab Work", reason_code="",
        copay="$0.00", deductible="$0.00",
    )
    exp_claim = _claim(line_items=[expected_item])
    act_claim = _claim(line_items=[expected_item, spurious_item])
    rows = diff_eob(
        "f", "anthem", "text",
        _eob(claims=[exp_claim]), _eob(claims=[act_claim]),
        "anthem", 0.95, [],
    )
    li_rows = [r for r in rows if "line_items" in r["field"]]
    assert any(r["outcome"] == "spurious" for r in li_rows), "second item must be spurious"
    assert not any(r["outcome"] == "miss" for r in li_rows), "matched item must not be miss"
    # The matched expected item's fields should all be match.
    matched = [r for r in li_rows if "spurious" not in r["field"] and r["outcome"] != "spurious"]
    assert all(r["outcome"] == "match" for r in matched), matched


def test_diff_eob_reordered_line_items():
    """Expected [A, B] vs actual [B, A] (distinct) → all match, no miss, no spurious."""
    item_a = _line_item(service_date="2026-01-01", service="Office Visit", reason_code="N130", copay="$20.00")
    item_b = _line_item(service_date="2026-01-02", service="X-Ray", reason_code="N140", copay="$10.00")
    exp_claim = _claim(line_items=[item_a, item_b])
    act_claim = _claim(line_items=[item_b, item_a])
    rows = diff_eob(
        "f", "anthem", "text",
        _eob(claims=[exp_claim]), _eob(claims=[act_claim]),
        "anthem", 0.95, [],
    )
    li_rows = [r for r in rows if "line_items" in r["field"]]
    assert li_rows
    assert all(r["outcome"] == "match" for r in li_rows), (
        [r for r in li_rows if r["outcome"] != "match"]
    )


def test_diff_eob_near_duplicate_monetary_tiebreak():
    """Two same-identity items differing in one monetary field pair to their closest counterpart."""
    # Both expected items share identity; they differ only in copay.
    item_a = _line_item(service_date="2026-01-01", service="Office Visit", reason_code="N130", copay="$20.00")
    item_b = _line_item(service_date="2026-01-01", service="Office Visit", reason_code="N130", copay="$30.00")
    exp_claim = _claim(line_items=[item_a, item_b])
    act_claim = _claim(line_items=[item_a, item_b])
    rows = diff_eob(
        "f", "anthem", "text",
        _eob(claims=[exp_claim]), _eob(claims=[act_claim]),
        "anthem", 0.95, [],
    )
    li_rows = [r for r in rows if "line_items" in r["field"]]
    # All fields should match — no cross-pairing spurious mismatch.
    assert all(r["outcome"] == "match" for r in li_rows), (
        [r for r in li_rows if r["outcome"] != "match"]
    )


def test_diff_eob_spurious_claim():
    """Expected 1 claim, actual 2 claims → second claim emits spurious rows, no miss."""
    exp = _eob(claims=[_claim(claim_number="C001")])
    act = _eob(claims=[_claim(claim_number="C001"), _claim(claim_number="C002")])
    rows = diff_eob("f", "anthem", "text", exp, act, "anthem", 0.95, [])
    spurious = [r for r in rows if "spurious" in r["field"]]
    assert spurious, "extra actual claim must produce spurious rows"
    assert all(r["outcome"] == "spurious" for r in spurious)
    assert not any(r["outcome"] == "miss" for r in rows), "no miss expected"
    # Original claim still matches.
    first = next(r for r in rows if r["field"] == "claims[0].claim_number")
    assert first["outcome"] == "match"


def test_diff_eob_spurious_outcome_lowers_accuracy(tmp_path):
    """Round-trip: spurious rows count as non-match in the accuracy helpers."""
    from src.medical.eob.eval.report import accuracy_by_insurer_kind, load_results
    from src.medical.eob.eval.store import init_eval_db, insert_eval_row
    db = str(tmp_path / "eval.db")
    init_eval_db(db)
    # One match row + one spurious row for the same insurer/kind.
    insert_eval_row(db, _row(run_id="R1", field="issuer", outcome="match", confidence=0.9))
    insert_eval_row(db, _row(run_id="R1", field="claims[spurious:0].patient",
                              outcome="spurious", expected="", actual="JANE DOE", confidence=0.9))
    df = load_results(db, run_id="R1")
    acc = accuracy_by_insurer_kind(df)
    # Accuracy must be < 1.0 because the spurious row is non-match.
    assert acc.iloc[0]["accuracy"] < 1.0


def test_diff_eob_no_identity_shared_zeros():
    """Guard: actual item sharing only zero-valued monetary fields must NOT qualify as a match.

    E1 has identity (idX); A2 has identity (idY) but shares zero monetary values with E1.
    Expected: exactly 1 match (E1↔first actual) + 1 spurious (A2), NOT a matched pair of E1+A2.
    """
    e1 = _line_item(service_date="2026-01-01", service="Office Visit", reason_code="N130",
                    copay="$0.00", deductible="$0.00")
    a2 = _line_item(service_date="2026-02-15", service="Lab Work", reason_code="N999",
                    copay="$0.00", deductible="$0.00")
    exp_claim = _claim(line_items=[e1])
    act_claim = _claim(line_items=[e1, a2])
    rows = diff_eob(
        "f", "anthem", "text",
        _eob(claims=[exp_claim]), _eob(claims=[act_claim]),
        "anthem", 0.95, [],
    )
    li_rows = [r for r in rows if "line_items" in r["field"]]
    assert any(r["outcome"] == "spurious" for r in li_rows), "A2 must be spurious"
    assert not any(r["outcome"] == "miss" for r in li_rows), "E1 should match, no miss"


def test_diff_eob_same_patient_under_and_over():
    """Guard: patient identity must not pair a missed expected claim to a spurious actual one.

    Expected claims: [#C001, #C002] — same patient.
    Actual claims:   [#C001, #C009] — same patient, different second claim_number.
    Expected: C001 match, C002 miss, C009 spurious. C002 must NOT be paired with C009.
    """
    c001_e = _claim(claim_number="C001")
    c002_e = _claim(claim_number="C002")
    c001_a = _claim(claim_number="C001")
    c009_a = _claim(claim_number="C009")
    exp = _eob(claims=[c001_e, c002_e])
    act = _eob(claims=[c001_a, c009_a])
    rows = diff_eob("f", "anthem", "text", exp, act, "anthem", 0.95, [])
    # C002 (expected, no actual counterpart) → all miss.
    c002_rows = [r for r in rows if r["field"].startswith("claims[1].")]
    assert c002_rows, "C002 expected claim must be enumerated"
    assert all(r["outcome"] == "miss" for r in c002_rows), (
        [r for r in c002_rows if r["outcome"] != "miss"]
    )
    # C009 (actual, no expected counterpart) → all spurious.
    spurious_rows = [r for r in rows if "spurious" in r["field"]]
    assert spurious_rows, "C009 actual claim must produce spurious rows"
    assert all(r["outcome"] == "spurious" for r in spurious_rows)
    # C001 → match.
    c001_num = next(r for r in rows if r["field"] == "claims[0].claim_number")
    assert c001_num["outcome"] == "match"


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
