"""
Tests for the Phase 4 EOB persistence + bridge layer.

Covers:
    src/medical/eob/persist.py   (persist_eob, get_latest_eob_claim, get_eob_claim_history)
    src/medical/eob/bridge.py    (bridge_eob_to_claims)

The database is never mocked — each test runs against a real SQLite file created
via tmp_path. No external services are involved.
"""

import json

import pytest

from src.database import get_connection, init_db
from src.medical.eob.bridge import bridge_eob_to_claims
from src.medical.eob.persist import (
    get_eob_claim_history,
    get_latest_eob_claim,
    persist_eob,
)
from src.medical.eob.types import Claim, EOBDocument, LineItem, PdfKind


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def _line_item(service_date: str = "2026-01-15") -> LineItem:
    return LineItem(
        service_date=service_date,
        service="Office visit",
        reason_code="",
        doctor_charges="250.00",
        discounts="50.00",
        allowed="200.00",
        anthem_paid="160.00",
        copay="20.00",
        deductible="0.00",
        coinsurance="20.00",
        not_covered="0.00",
        your_total="40.00",
    )


def _two_claim_eob() -> EOBDocument:
    claim1 = Claim(
        patient="John Doe",
        claim_number="CLM001",
        received_date="2026-01-20",
        provider="Dr. Smith",
        in_network=True,
        patient_owes="40.00",
        line_items=[_line_item()],
    )
    claim2 = Claim(
        patient="John Doe",
        claim_number="CLM002",
        received_date="2026-01-21",
        provider="Dr. Jones",
        in_network=True,
        patient_owes="56.00",
        line_items=[_line_item("2026-01-16")],
    )
    return EOBDocument(
        issuer="anthem",
        subtype="summary",
        subscriber="John Doe",
        claims=[claim1, claim2],
    )


# ---------------------------------------------------------------------------
# persist_eob
# ---------------------------------------------------------------------------

def test_persist_eob_returns_document_id(db_path):
    eob = _two_claim_eob()
    doc_id = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    assert isinstance(doc_id, int)
    assert doc_id > 0


def test_persist_eob_writes_one_document_row(db_path):
    eob = _two_claim_eob()
    persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS c FROM eob_documents")
        assert cursor.fetchone()["c"] == 1
        cursor.execute("SELECT issuer, subtype, subscriber FROM eob_documents")
        row = cursor.fetchone()
        assert row["issuer"] == "anthem"
        assert row["subtype"] == "summary"
        assert row["subscriber"] == "John Doe"
    finally:
        conn.close()


def test_persist_eob_writes_n_eob_claims(db_path):
    eob = _two_claim_eob()
    doc_id = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS c FROM eob_claims WHERE document_id = ?", (doc_id,)
        )
        assert cursor.fetchone()["c"] == 2
    finally:
        conn.close()


def test_persist_eob_claim_id_null_before_bridge(db_path):
    eob = _two_claim_eob()
    doc_id = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT claim_id FROM eob_claims WHERE document_id = ?", (doc_id,)
        )
        rows = cursor.fetchall()
        assert len(rows) == 2
        assert all(row["claim_id"] is None for row in rows)
    finally:
        conn.close()


def test_persist_eob_line_items_json_round_trip(db_path):
    eob = _two_claim_eob()
    persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT line_items_json FROM eob_claims WHERE claim_number = ?", ("CLM001",)
        )
        row = cursor.fetchone()
        line_items = json.loads(row["line_items_json"])
        assert line_items[0]["service_date"] == "2026-01-15"
        assert line_items[0]["doctor_charges"] == "250.00"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# bridge_eob_to_claims
# ---------------------------------------------------------------------------

def test_bridge_creates_claims_and_adjudications(db_path):
    eob = _two_claim_eob()
    doc_id = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    claim_ids = bridge_eob_to_claims(eob, db_path, doc_id)
    assert len(claim_ids) == 2
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS c FROM claims")
        assert cursor.fetchone()["c"] == 2
        cursor.execute("SELECT COUNT(*) AS c FROM adjudications")
        assert cursor.fetchone()["c"] == 2
    finally:
        conn.close()


def test_bridge_backfills_eob_claim_id(db_path):
    eob = _two_claim_eob()
    doc_id = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    bridge_eob_to_claims(eob, db_path, doc_id)
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT claim_id FROM eob_claims WHERE document_id = ?", (doc_id,)
        )
        rows = cursor.fetchall()
        assert len(rows) == 2
        assert all(row["claim_id"] is not None for row in rows)
    finally:
        conn.close()


def test_bridge_same_claim_number_twice_produces_two_rows(db_path):
    eob = _two_claim_eob()
    doc_id1 = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    bridge_eob_to_claims(eob, db_path, doc_id1)
    doc_id2 = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    bridge_eob_to_claims(eob, db_path, doc_id2)
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS c FROM eob_claims")
        assert cursor.fetchone()["c"] == 4
        cursor.execute("SELECT COUNT(*) AS c FROM claims")
        assert cursor.fetchone()["c"] == 4
    finally:
        conn.close()


def test_v_claim_obligation_reflects_bridged_amounts(db_path):
    eob = _two_claim_eob()
    doc_id = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    claim_ids = bridge_eob_to_claims(eob, db_path, doc_id)
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT member_owed FROM v_claim_obligation WHERE claim_id = ?",
            (claim_ids[0],),
        )
        row = cursor.fetchone()
        assert row is not None
        assert abs(row["member_owed"] - 40.00) < 0.005
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# query helpers
# ---------------------------------------------------------------------------

def test_get_latest_eob_claim_returns_newest(db_path):
    eob = _two_claim_eob()
    doc_id1 = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    bridge_eob_to_claims(eob, db_path, doc_id1)
    doc_id2 = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    bridge_eob_to_claims(eob, db_path, doc_id2)

    latest = get_latest_eob_claim(db_path, "CLM001")
    history = get_eob_claim_history(db_path, "CLM001")
    assert latest is not None
    assert len(history) == 2
    assert latest["id"] == history[-1]["id"]
    assert latest["id"] == max(row["id"] for row in history)


def test_get_eob_claim_history_returns_all_versions(db_path):
    eob = _two_claim_eob()
    doc_id1 = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    bridge_eob_to_claims(eob, db_path, doc_id1)
    doc_id2 = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    bridge_eob_to_claims(eob, db_path, doc_id2)

    history = get_eob_claim_history(db_path, "CLM001")
    assert len(history) == 2
    assert history[0]["id"] < history[1]["id"]
    assert all(row["claim_number"] == "CLM001" for row in history)


def test_get_latest_eob_claim_includes_subtype_and_subscriber(db_path):
    eob = _two_claim_eob()
    persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    latest = get_latest_eob_claim(db_path, "CLM001")
    assert latest is not None
    assert latest["subtype"] == "summary"
    assert latest["subscriber"] == "John Doe"


def test_bridge_creates_placeholder_practice_for_unknown_provider(db_path):
    claim = Claim(
        patient="John Doe",
        claim_number="CLM900",
        received_date="2026-02-01",
        provider="New Unknown Doctor",
        in_network=True,
        patient_owes="40.00",
        line_items=[_line_item("2026-02-01")],
    )
    eob = EOBDocument(
        issuer="anthem",
        subtype="summary",
        subscriber="John Doe",
        claims=[claim],
    )
    doc_id = persist_eob(eob, PdfKind.TEXT, "anthem", None, db_path)
    claim_ids = bridge_eob_to_claims(eob, db_path, doc_id)
    assert len(claim_ids) == 1
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS c FROM practices WHERE name = ?", ("New Unknown Doctor",)
        )
        assert cursor.fetchone()["c"] == 1
    finally:
        conn.close()
