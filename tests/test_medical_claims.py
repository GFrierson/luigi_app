"""Tests for src/medical/claims.py — claims & adjudication lifecycle (Phase 2)."""

import pytest

from src.database import init_db
from src.medical.claims import (
    add_external_id,
    adjudicate_claim,
    create_claim,
    find_by_external_id,
    find_by_match_key,
    get_claim_events,
)
from src.medical.entities import create_practice


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


# ---------------------------------------------------------------------------
# create_claim
# ---------------------------------------------------------------------------

def test_create_claim_returns_dict(db_path, practice_id):
    result = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    assert result is not None
    assert result["service_date"] == "2025-09-23"
    assert result["billing_practice_id"] == practice_id
    assert result["billed_amount"] == 250.00
    assert result["current_status"] == "submitted"


def test_create_claim_rejects_duplicate_match_key(db_path, practice_id):
    first = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    assert first is not None

    second = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    assert second is None


def test_create_claim_appends_created_event(db_path, practice_id):
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    events = get_claim_events(db_path, claim["id"])
    assert len(events) == 1
    assert events[0]["event_type"] == "created"


# ---------------------------------------------------------------------------
# add_external_id
# ---------------------------------------------------------------------------

def test_add_external_id_returns_dict(db_path, practice_id):
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    result = add_external_id(db_path, claim["id"], "aetna", "AETNA-123")
    assert result is not None
    assert result["claim_id"] == claim["id"]
    assert result["system"] == "aetna"
    assert result["external_id"] == "AETNA-123"


def test_add_external_id_rejects_duplicate_system_id(db_path, practice_id):
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    first = add_external_id(db_path, claim["id"], "aetna", "AETNA-123")
    assert first is not None

    second = add_external_id(db_path, claim["id"], "aetna", "AETNA-123")
    assert second is None


# ---------------------------------------------------------------------------
# find_by_external_id
# ---------------------------------------------------------------------------

def test_find_by_external_id_returns_claim(db_path, practice_id):
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    add_external_id(db_path, claim["id"], "aetna", "AETNA-123")

    found = find_by_external_id(db_path, "aetna", "AETNA-123")
    assert found is not None
    assert found["id"] == claim["id"]
    assert found["service_date"] == "2025-09-23"


def test_find_by_external_id_returns_none_when_missing(db_path, practice_id):
    create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    assert find_by_external_id(db_path, "aetna", "DOES-NOT-EXIST") is None


# ---------------------------------------------------------------------------
# find_by_match_key
# ---------------------------------------------------------------------------

def test_find_by_match_key_returns_claim(db_path, practice_id):
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    found = find_by_match_key(db_path, "2025-09-23", practice_id, 250.00)
    assert found is not None
    assert found["id"] == claim["id"]


def test_find_by_match_key_returns_none_for_wrong_amount(db_path, practice_id):
    create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    found = find_by_match_key(db_path, "2025-09-23", practice_id, 251.00)
    assert found is None


# ---------------------------------------------------------------------------
# adjudicate_claim
# ---------------------------------------------------------------------------

def test_adjudicate_claim_sets_status_adjudicated(db_path, practice_id):
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    adj = adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2025-10-01",
        allowed_amount=200.00,
        plan_paid=160.00,
        member_owed=40.00,
    )
    assert adj is not None
    assert adj["revision"] == 1
    assert adj["superseded_by"] is None

    # Re-fetch claim and assert status
    found = find_by_match_key(db_path, "2025-09-23", practice_id, 250.00)
    assert found is not None
    assert found["current_status"] == "adjudicated"


def test_readjudication_marks_prior_superseded(db_path, practice_id):
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    adj1 = adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2025-10-01",
        allowed_amount=200.00,
        plan_paid=160.00,
        member_owed=40.00,
    )
    adj2 = adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2025-10-15",
        allowed_amount=210.00,
        plan_paid=170.00,
        member_owed=40.00,
    )
    assert adj2 is not None
    assert adj2["revision"] == 2

    # Verify prior row is superseded
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, revision, superseded_by FROM adjudications WHERE id = ?",
        (adj1["id"],),
    )
    prior = dict(cursor.fetchone())
    conn.close()
    assert prior["superseded_by"] == adj2["id"]

    # Claim status updated
    found = find_by_match_key(db_path, "2025-09-23", practice_id, 250.00)
    assert found["current_status"] == "readjudicated"


def test_three_adjudications_chain_superseded(db_path, practice_id):
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    adj1 = adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2025-10-01",
        allowed_amount=200.00,
        plan_paid=160.00,
        member_owed=40.00,
    )
    adj2 = adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2025-10-15",
        allowed_amount=210.00,
        plan_paid=170.00,
        member_owed=40.00,
    )
    adj3 = adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2025-10-30",
        allowed_amount=220.00,
        plan_paid=180.00,
        member_owed=40.00,
    )
    assert adj3["revision"] == 3

    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, revision, superseded_by FROM adjudications WHERE claim_id = ? ORDER BY revision ASC",
        (claim["id"],),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    assert len(rows) == 3
    # rev1.superseded_by == rev2.id
    assert rows[0]["superseded_by"] == adj2["id"]
    # rev2.superseded_by == rev3.id
    assert rows[1]["superseded_by"] == adj3["id"]
    # rev3.superseded_by IS NULL
    assert rows[2]["superseded_by"] is None


def test_readjudication_appends_events_in_order(db_path, practice_id):
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2025-10-01",
        allowed_amount=200.00,
        plan_paid=160.00,
        member_owed=40.00,
    )
    adjudicate_claim(
        db_path,
        claim_id=claim["id"],
        adjudication_date="2025-10-15",
        allowed_amount=210.00,
        plan_paid=170.00,
        member_owed=40.00,
    )

    events = get_claim_events(db_path, claim["id"])
    types = [e["event_type"] for e in events]
    assert types == ["created", "adjudicated", "readjudicated"]


# ---------------------------------------------------------------------------
# get_claim_events
# ---------------------------------------------------------------------------

def test_get_claim_events_ordered_by_id(db_path, practice_id):
    claim = create_claim(
        db_path,
        service_date="2025-09-23",
        billing_practice_id=practice_id,
        billed_amount=250.00,
    )
    add_external_id(db_path, claim["id"], "aetna", "AETNA-1")
    add_external_id(db_path, claim["id"], "aetna", "AETNA-2")

    events = get_claim_events(db_path, claim["id"])
    # Insertion order: created, external_id_added (AETNA-1), external_id_added (AETNA-2)
    ids = [e["id"] for e in events]
    assert ids == sorted(ids)
    types = [e["event_type"] for e in events]
    assert types == ["created", "external_id_added", "external_id_added"]
