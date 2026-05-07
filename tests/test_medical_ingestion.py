"""Tests for src/medical/ingestion.py — commit_ingestion encounter stub behavior."""
import sqlite3

import pytest

from src.database import init_db
from src.medical.claims import create_claim, find_by_match_key
from src.medical.entities import (
    create_encounter,
    create_practice,
    find_encounter_by_date_and_practice,
)
from src.medical.extraction import (
    ExtractedClaim,
    ExtractedPractice,
    ExtractionResult,
)
from src.medical.ingestion import _pending_confirmations, commit_ingestion


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


@pytest.fixture(autouse=True)
def clear_pending():
    _pending_confirmations.clear()
    yield
    _pending_confirmations.clear()


def _make_pending(
    practice_id: int,
    practice_name: str = "Test Practice",
    service_date: str = "2025-09-23",
    billed_amount: float = 250.0,
    claim_matched: bool = False,
    claim_id: int | None = None,
) -> dict:
    """Build a minimal pending confirmation dict matching the real shape."""
    extracted_claim = ExtractedClaim(
        service_date=service_date,
        billed_amount=billed_amount,
        practice_name=practice_name,
    )
    extraction = ExtractionResult(
        doc_type="eob",
        practices=[ExtractedPractice(name=practice_name)],
        providers=[],
        claims=[extracted_claim],
        adjudications=[],
    )
    match_results = {
        "practices": [
            {"name": practice_name, "matched": True, "practice_id": practice_id},
        ],
        "claims": [
            {
                "service_date": service_date,
                "matched": claim_matched,
                "claim_id": claim_id,
            },
        ],
    }
    return {
        "extraction": extraction,
        "match_results": match_results,
        "document_id": None,
        "practice_id_by_name": {practice_name: practice_id},
    }


@pytest.mark.asyncio
async def test_commit_ingestion_new_eob_creates_encounter_stub_and_linked_claim(db_path):
    practice = create_practice(db_path, "Test Practice")
    pending = _make_pending(practice["id"])

    await commit_ingestion(db_path, 12345, pending)

    encounter = find_encounter_by_date_and_practice(db_path, "2025-09-23", practice["id"])
    assert encounter is not None

    claim = find_by_match_key(db_path, "2025-09-23", practice["id"], 250.0)
    assert claim is not None
    assert claim["encounter_id"] == encounter["id"]


@pytest.mark.asyncio
async def test_commit_ingestion_second_eob_reuses_existing_encounter(db_path):
    practice = create_practice(db_path, "Test Practice")

    pending1 = _make_pending(practice["id"], billed_amount=250.0)
    pending2 = _make_pending(practice["id"], billed_amount=300.0)

    await commit_ingestion(db_path, 12345, pending1)
    await commit_ingestion(db_path, 12345, pending2)

    # Only one encounter should exist for that date+practice.
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM encounters WHERE service_date=? AND practice_id=?",
        ("2025-09-23", practice["id"]),
    ).fetchone()[0]
    conn.close()
    assert count == 1

    enc = find_encounter_by_date_and_practice(db_path, "2025-09-23", practice["id"])
    claim1 = find_by_match_key(db_path, "2025-09-23", practice["id"], 250.0)
    claim2 = find_by_match_key(db_path, "2025-09-23", practice["id"], 300.0)
    assert claim1["encounter_id"] == enc["id"]
    assert claim2["encounter_id"] == enc["id"]


@pytest.mark.asyncio
async def test_commit_ingestion_matched_claim_encounter_not_overwritten(db_path):
    practice = create_practice(db_path, "Test Practice")
    enc = create_encounter(db_path, "2025-09-23", practice["id"], None, None)
    existing_claim = create_claim(db_path, "2025-09-23", practice["id"], 250.0, enc["id"])
    assert existing_claim is not None

    pending = _make_pending(
        practice["id"],
        claim_matched=True,
        claim_id=existing_claim["id"],
    )

    await commit_ingestion(db_path, 12345, pending)

    # The matched-claim branch must not overwrite encounter_id.
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT encounter_id FROM claims WHERE id=?", (existing_claim["id"],)
    ).fetchone()
    conn.close()
    assert row[0] == enc["id"]
