"""Tests for src/medical/documents.py, src/medical/payments.py, and Phase 3 SQL views."""

import os
import sqlite3

import pytest

from src.database import init_db
from src.medical.claims import adjudicate_claim, create_claim
from src.medical.documents import (
    attach_document,
    list_documents_for_entity,
    save_document,
)
from src.medical.entities import create_encounter, create_practice
from src.medical.payments import (
    apply_payment,
    get_payment_applications,
    record_payment,
)


@pytest.fixture
def db_path(tmp_path):
    """Create an isolated SQLite DB initialized with init_db()."""
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


@pytest.fixture
def practice_id(db_path):
    """Create a practice for FK satisfaction. Returns its id."""
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    assert practice is not None
    return practice["id"]


@pytest.fixture
def claim_id(db_path, practice_id):
    """Create a basic claim for FK satisfaction in payment/document tests."""
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    assert claim is not None
    return claim["id"]


# ---------------------------------------------------------------------------
# Schema: tables and views
# ---------------------------------------------------------------------------

def test_init_db_creates_phase3_tables(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT name, type FROM sqlite_master
        WHERE name IN ('documents', 'document_links', 'payments', 'payment_applications',
                       'v_claim_obligation', 'v_member_holds', 'v_encounter_balance')
        """
    )
    by_name = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()

    assert by_name.get("documents") == "table"
    assert by_name.get("document_links") == "table"
    assert by_name.get("payments") == "table"
    assert by_name.get("payment_applications") == "table"
    assert by_name.get("v_claim_obligation") == "view"
    assert by_name.get("v_member_holds") == "view"
    assert by_name.get("v_encounter_balance") == "view"


# ---------------------------------------------------------------------------
# save_document
# ---------------------------------------------------------------------------

def test_save_document_writes_file_and_returns_row(tmp_path, db_path):
    documents_dir = str(tmp_path / "store")
    result = save_document(
        documents_dir=documents_dir,
        chat_id=12345,
        db_path=db_path,
        file_bytes=b"PDF",
        original_name="eob.pdf",
        mime_type="application/pdf",
        doc_type="eob",
        document_date="2025-11-06",
        notes="Sep 23 EOB",
    )
    assert result is not None
    assert result["chat_id"] == 12345
    assert result["doc_type"] == "eob"
    assert result["original_name"] == "eob.pdf"
    assert result["mime_type"] == "application/pdf"
    assert os.path.exists(result["file_path"])
    with open(result["file_path"], "rb") as f:
        assert f.read() == b"PDF"


def test_save_document_creates_yyyy_mm_directories(tmp_path, db_path):
    documents_dir = str(tmp_path / "store")
    result = save_document(
        documents_dir=documents_dir,
        chat_id=12345,
        db_path=db_path,
        file_bytes=b"PDF",
        original_name="eob.pdf",
        mime_type="application/pdf",
        doc_type="eob",
        document_date="2025-11-06",
        notes=None,
    )
    assert result is not None
    expected_dir = os.path.join(documents_dir, "12345", "documents", "2025", "11")
    assert os.path.isdir(expected_dir)
    assert result["file_path"].startswith(expected_dir + os.sep)


def test_save_document_no_collision_same_name_same_month(tmp_path, db_path):
    documents_dir = str(tmp_path / "store")
    first = save_document(
        documents_dir=documents_dir,
        chat_id=12345,
        db_path=db_path,
        file_bytes=b"FIRST",
        original_name="eob.pdf",
        mime_type="application/pdf",
        doc_type="eob",
        document_date="2025-11-06",
        notes=None,
    )
    second = save_document(
        documents_dir=documents_dir,
        chat_id=12345,
        db_path=db_path,
        file_bytes=b"SECOND",
        original_name="eob.pdf",
        mime_type="application/pdf",
        doc_type="eob",
        document_date="2025-11-06",
        notes=None,
    )
    assert first is not None and second is not None
    assert first["file_path"] != second["file_path"]
    assert os.path.exists(first["file_path"])
    assert os.path.exists(second["file_path"])
    with open(first["file_path"], "rb") as f:
        assert f.read() == b"FIRST"
    with open(second["file_path"], "rb") as f:
        assert f.read() == b"SECOND"


# ---------------------------------------------------------------------------
# attach_document / list_documents_for_entity
# ---------------------------------------------------------------------------

def test_attach_document_returns_link_row(tmp_path, db_path, claim_id):
    doc = save_document(
        documents_dir=str(tmp_path / "store"),
        chat_id=12345,
        db_path=db_path,
        file_bytes=b"PDF",
        original_name="eob.pdf",
        mime_type="application/pdf",
        doc_type="eob",
        document_date="2025-11-06",
        notes=None,
    )
    link = attach_document(db_path, doc["id"], "claim", claim_id)
    assert link is not None
    assert link["document_id"] == doc["id"]
    assert link["entity_type"] == "claim"
    assert link["entity_id"] == claim_id


def test_attach_document_rejects_duplicate(tmp_path, db_path, claim_id):
    doc = save_document(
        documents_dir=str(tmp_path / "store"),
        chat_id=12345,
        db_path=db_path,
        file_bytes=b"PDF",
        original_name="eob.pdf",
        mime_type="application/pdf",
        doc_type="eob",
        document_date="2025-11-06",
        notes=None,
    )
    first = attach_document(db_path, doc["id"], "claim", claim_id)
    assert first is not None
    second = attach_document(db_path, doc["id"], "claim", claim_id)
    assert second is None


def test_list_documents_for_entity_returns_linked(tmp_path, db_path, claim_id):
    documents_dir = str(tmp_path / "store")
    doc1 = save_document(
        documents_dir, 12345, db_path, b"A", "a.pdf", "application/pdf", "eob",
        "2025-11-06", None,
    )
    doc2 = save_document(
        documents_dir, 12345, db_path, b"B", "b.pdf", "application/pdf", "statement",
        "2025-11-06", None,
    )
    attach_document(db_path, doc1["id"], "claim", claim_id)
    attach_document(db_path, doc2["id"], "claim", claim_id)

    rows = list_documents_for_entity(db_path, "claim", claim_id)
    assert len(rows) == 2
    doc_types = sorted(r["doc_type"] for r in rows)
    assert doc_types == ["eob", "statement"]
    # Each row exposes link_id from the join table
    assert all(r.get("link_id") is not None for r in rows)


def test_list_documents_for_entity_empty_for_unknown(db_path):
    assert list_documents_for_entity(db_path, "claim", 999) == []


# ---------------------------------------------------------------------------
# record_payment / apply_payment / get_payment_applications
# ---------------------------------------------------------------------------

def test_record_payment_returns_dict(db_path):
    result = record_payment(
        db_path,
        payment_date="2025-11-15",
        amount=40.00,
        from_party="member",
        to_party="practice",
        method="check",
        reference="CHK-001",
        notes="copay",
    )
    assert result is not None
    assert result["payment_date"] == "2025-11-15"
    assert result["amount"] == 40.00
    assert result["from_party"] == "member"
    assert result["to_party"] == "practice"
    assert result["method"] == "check"


def test_apply_payment_returns_application_row(db_path, claim_id):
    payment = record_payment(
        db_path, "2025-11-15", 40.00, "member", "practice", "check", "CHK-1", None,
    )
    result = apply_payment(db_path, payment["id"], claim_id, 40.00)
    assert result is not None
    assert result["payment_id"] == payment["id"]
    assert result["claim_id"] == claim_id
    assert result["applied_amount"] == 40.00


def test_apply_payment_rejects_duplicate(db_path, claim_id):
    payment = record_payment(
        db_path, "2025-11-15", 40.00, "member", "practice", None, None, None,
    )
    first = apply_payment(db_path, payment["id"], claim_id, 40.00)
    assert first is not None
    second = apply_payment(db_path, payment["id"], claim_id, 40.00)
    assert second is None


def test_get_payment_applications_returns_all(db_path, claim_id):
    p1 = record_payment(db_path, "2025-11-15", 25.00, "member", "practice", None, None, None)
    p2 = record_payment(db_path, "2025-11-20", 15.00, "member", "practice", None, None, None)
    apply_payment(db_path, p1["id"], claim_id, 25.00)
    apply_payment(db_path, p2["id"], claim_id, 15.00)

    rows = get_payment_applications(db_path, claim_id)
    assert len(rows) == 2
    amounts = sorted(r["applied_amount"] for r in rows)
    assert amounts == [15.00, 25.00]
    # Joined columns from payments are present
    assert all(r["from_party"] == "member" for r in rows)
    assert all(r["to_party"] == "practice" for r in rows)


# ---------------------------------------------------------------------------
# v_claim_obligation
# ---------------------------------------------------------------------------

def _query_view(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    """Helper: open a connection, run a SELECT, return list[dict]."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return rows


def test_v_claim_obligation_reflects_net_owed(db_path, practice_id):
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2025-11-06",
        allowed_amount=200.00,
        plan_paid=100.00,
        member_owed=100.00,
    )
    payment = record_payment(
        db_path, "2025-11-15", 40.00, "member", "practice", None, None, None,
    )
    apply_payment(db_path, payment["id"], claim["id"], 40.00)

    rows = _query_view(
        db_path,
        "SELECT * FROM v_claim_obligation WHERE claim_id = ?",
        (claim["id"],),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["member_owed"] == 100.00
    assert row["payments_applied"] == 40.00
    assert row["net_obligation"] == 60.00


def test_v_claim_obligation_includes_submitted_claim(db_path, practice_id):
    """Un-adjudicated claims must still appear in v_claim_obligation
    with member_owed=0 and net_obligation=0 (LEFT JOIN, COALESCE)."""
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    rows = _query_view(
        db_path,
        "SELECT * FROM v_claim_obligation WHERE claim_id = ?",
        (claim["id"],),
    )
    assert len(rows) == 1
    assert rows[0]["member_owed"] == 0.0
    assert rows[0]["payments_applied"] == 0.0
    assert rows[0]["net_obligation"] == 0.0


def test_v_claim_obligation_uses_current_adjudication_only(db_path, practice_id):
    """Re-adjudication must not inflate net_obligation by GROUP BY multiplication.
    Filter on a.superseded_by IS NULL ensures only the current revision counts."""
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2025-11-06",
        allowed_amount=200.00,
        plan_paid=100.00,
        member_owed=100.00,
    )
    # Re-adjudicate to a different member_owed
    adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2026-01-15",
        allowed_amount=200.00,
        plan_paid=120.00,
        member_owed=80.00,
    )
    payment = record_payment(
        db_path, "2026-02-01", 20.00, "member", "practice", None, None, None,
    )
    apply_payment(db_path, payment["id"], claim["id"], 20.00)

    rows = _query_view(
        db_path,
        "SELECT * FROM v_claim_obligation WHERE claim_id = ?",
        (claim["id"],),
    )
    assert len(rows) == 1
    row = rows[0]
    # current adjudication is the second one (member_owed=80), payment of 20 applied
    assert row["member_owed"] == 80.00
    assert row["payments_applied"] == 20.00
    assert row["net_obligation"] == 60.00


# ---------------------------------------------------------------------------
# v_member_holds
# ---------------------------------------------------------------------------

def test_v_member_holds_captures_insurer_to_member_payment(db_path, practice_id):
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    payment = record_payment(
        db_path, "2025-11-20", 100.00, "insurer", "member", "check", "REF-1", None,
    )
    apply_payment(db_path, payment["id"], claim["id"], 100.00)

    rows = _query_view(
        db_path,
        "SELECT * FROM v_member_holds WHERE claim_id = ?",
        (claim["id"],),
    )
    assert len(rows) == 1
    assert rows[0]["payment_id"] == payment["id"]
    assert rows[0]["held_amount"] == 100.00
    assert rows[0]["payment_date"] == "2025-11-20"


# ---------------------------------------------------------------------------
# v_encounter_balance
# ---------------------------------------------------------------------------

def test_v_encounter_balance_sums_across_claims(db_path, practice_id):
    encounter = create_encounter(db_path, "2025-09-23", practice_id, None, None)
    enc_id = encounter["id"]

    claim1 = create_claim(
        db_path, "2025-09-23", practice_id, 250.00, encounter_id=enc_id,
    )
    claim2 = create_claim(
        db_path, "2025-09-23", practice_id, 400.00, encounter_id=enc_id,
    )
    adjudicate_claim(
        db_path, claim1["id"], "2025-11-06",
        allowed_amount=200.00, plan_paid=100.00, member_owed=100.00,
    )
    adjudicate_claim(
        db_path, claim2["id"], "2025-11-06",
        allowed_amount=350.00, plan_paid=200.00, member_owed=150.00,
    )

    rows = _query_view(
        db_path,
        "SELECT * FROM v_encounter_balance WHERE encounter_id = ?",
        (enc_id,),
    )
    assert len(rows) == 1
    # 100 + 150 = 250
    assert rows[0]["total_net_obligation"] == 250.00
