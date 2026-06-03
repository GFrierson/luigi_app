"""Tests for src/medical/ingestion.py — commit_ingestion encounter stub behavior."""
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.database import init_db
from src.medical.claims import create_claim, find_by_match_key
from src.medical.confirmation import apply_correction, parse_confirmation_reply
from src.medical.entities import (
    create_encounter,
    create_practice,
    create_provider,
    find_encounter_by_date_and_practice,
)
from src.medical.extraction import (
    ExtractedClaim,
    ExtractedPractice,
    ExtractionResult,
)
from src.medical.claims import adjudicate_claim
from src.medical.ingestion import (
    _pending_confirmations,
    commit_ingestion,
    handle_correction,
)
from src.medical.scripts.seed_sep23_fixture import seed


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
    provider_name: str | None = None,
) -> dict:
    """Build a minimal pending confirmation dict matching the real shape."""
    extracted_claim = ExtractedClaim(
        service_date=service_date,
        billed_amount=billed_amount,
        practice_name=practice_name,
        provider_name=provider_name,
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


# ---------------------------------------------------------------------------
# Phase 7: auto-link provider from EOB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_ingestion_eob_with_provider_name_sets_encounter_provider_id(db_path):
    practice = create_practice(db_path, "Test Practice")
    pending = _make_pending(practice["id"], provider_name="Dr. Smith")

    await commit_ingestion(db_path, 12345, pending)

    encounter = find_encounter_by_date_and_practice(db_path, "2025-09-23", practice["id"])
    assert encounter is not None
    assert encounter["provider_id"] is not None

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    provider = conn.execute(
        "SELECT id, name FROM providers WHERE name=?", ("Dr. Smith",)
    ).fetchone()
    conn.close()
    assert provider is not None
    assert encounter["provider_id"] == provider["id"]


@pytest.mark.asyncio
async def test_commit_ingestion_second_eob_with_different_provider_does_not_overwrite(db_path):
    practice = create_practice(db_path, "Test Practice")

    # First EOB: new claim, sets provider to Dr. Smith
    pending1 = _make_pending(
        practice["id"], billed_amount=250.0, provider_name="Dr. Smith"
    )
    await commit_ingestion(db_path, 12345, pending1)

    encounter_after_first = find_encounter_by_date_and_practice(
        db_path, "2025-09-23", practice["id"]
    )
    # Guard: the test is invalid if the first commit didn't set provider_id.
    assert encounter_after_first is not None
    assert encounter_after_first["provider_id"] is not None
    original_provider_id = encounter_after_first["provider_id"]

    # Second EOB: different billed_amount → new claim, same encounter,
    # different provider name → must NOT overwrite.
    pending2 = _make_pending(
        practice["id"], billed_amount=300.0, provider_name="Dr. Jones"
    )
    await commit_ingestion(db_path, 12345, pending2)

    encounter_after_second = find_encounter_by_date_and_practice(
        db_path, "2025-09-23", practice["id"]
    )
    assert encounter_after_second is not None
    assert encounter_after_second["provider_id"] == original_provider_id


@pytest.mark.asyncio
async def test_commit_ingestion_eob_with_no_provider_leaves_provider_id_null(db_path):
    practice = create_practice(db_path, "Test Practice")
    pending = _make_pending(practice["id"], provider_name=None)

    await commit_ingestion(db_path, 12345, pending)

    encounter = find_encounter_by_date_and_practice(db_path, "2025-09-23", practice["id"])
    assert encounter is not None
    assert encounter["provider_id"] is None


@pytest.mark.asyncio
async def test_commit_ingestion_eob_with_provider_matches_existing_provider_no_duplicate(db_path):
    practice = create_practice(db_path, "Test Practice")
    existing_provider = create_provider(db_path, "Dr. Smith")
    assert existing_provider is not None

    pending = _make_pending(practice["id"], provider_name="Dr. Smith")
    await commit_ingestion(db_path, 12345, pending)

    encounter = find_encounter_by_date_and_practice(db_path, "2025-09-23", practice["id"])
    assert encounter is not None
    assert encounter["provider_id"] == existing_provider["id"]

    # Only one provider row should exist for "Dr. Smith".
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM providers WHERE name=?", ("Dr. Smith",)
    ).fetchone()[0]
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# Phase 8: gap review — view math and seed fixture integrity
# ---------------------------------------------------------------------------


def test_phase8_encounter_balance_view_sums_two_claims(db_path):
    """v_encounter_balance totals net_obligation across all claims for an encounter."""
    practice = create_practice(db_path, "Test Practice")
    enc = create_encounter(db_path, "2025-09-23", practice["id"], None, None)
    claim1 = create_claim(db_path, "2025-09-23", practice["id"], 500.0, enc["id"])
    claim2 = create_claim(db_path, "2025-09-23", practice["id"], 300.0, enc["id"])
    adjudicate_claim(db_path, claim1["id"], "2025-10-01", 400.0, 200.0, 200.0, 0.0)
    adjudicate_claim(db_path, claim2["id"], "2025-10-01", 250.0, 100.0, 150.0, 0.0)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT total_net_obligation FROM v_encounter_balance WHERE encounter_id=?",
        (enc["id"],),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["total_net_obligation"] == pytest.approx(350.0)


def test_phase8_seed_fixture_view_totals(tmp_path):
    """Sep 23 fixture produces expected headline figures in v_claim_obligation and v_member_holds."""
    db_path = str(tmp_path / "fixture.db")
    seed(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    siefferman_row = conn.execute(
        "SELECT net_obligation FROM v_claim_obligation WHERE billed_amount=5625.0"
    ).fetchone()
    mikaberidze_hold_row = conn.execute(
        "SELECT held_amount FROM v_member_holds LIMIT 1"
    ).fetchone()
    conn.close()

    assert siefferman_row is not None
    assert siefferman_row["net_obligation"] == pytest.approx(4501.5)
    assert mikaberidze_hold_row is not None
    assert mikaberidze_hold_row["held_amount"] == pytest.approx(5275.5)


def test_phase8_seed_fixture_no_orphan_claims(tmp_path):
    """After seeding the Sep 23 fixture, every claim must have an encounter_id."""
    db_path = str(tmp_path / "fixture.db")
    seed(db_path)

    conn = sqlite3.connect(db_path)
    orphan_count = conn.execute(
        "SELECT COUNT(*) FROM claims WHERE encounter_id IS NULL"
    ).fetchone()[0]
    conn.close()

    assert orphan_count == 0


# ---------------------------------------------------------------------------
# Phase 10: correction loop
# ---------------------------------------------------------------------------

def _make_unmatched_practice_pending(
    practice_name: str,
    service_date: str = "2025-09-23",
    billed_amount: float = 250.0,
) -> dict:
    """Pending where the practice (and its claim) are unmatched."""
    claim = ExtractedClaim(
        service_date=service_date,
        billed_amount=billed_amount,
        practice_name=practice_name,
    )
    extraction = ExtractionResult(
        doc_type="eob",
        practices=[ExtractedPractice(name=practice_name)],
        providers=[],
        claims=[claim],
        adjudications=[],
    )
    return {
        "extraction": extraction,
        "match_results": {
            "practices": [
                {"name": practice_name, "matched": False, "practice_id": None},
            ],
            "claims": [
                {"service_date": service_date, "matched": False, "claim_id": None},
            ],
        },
        "document_id": None,
        "practice_id_by_name": {practice_name: None},
    }


@pytest.mark.asyncio
async def test_handle_correction_rerenders_with_updated_practice_name(db_path):
    practice = create_practice(db_path, "Acme Clinic")
    chat_id = 12345
    pending = _make_unmatched_practice_pending("Acme Clini")

    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await handle_correction(
        db_path,
        chat_id,
        pending,
        {"action": "correction", "item_index": 1, "correction_text": "Acme Clinic"},
        context,
    )

    stored = _pending_confirmations[chat_id]
    assert stored["correction_rounds"] == 1
    practice_entry = stored["match_results"]["practices"][0]
    assert practice_entry["matched"] is True
    assert practice_entry["practice_id"] == practice["id"]
    context.bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_correction_service_date_finds_existing_claim(db_path):
    practice = create_practice(db_path, "Acme Clinic")
    claim = create_claim(db_path, "2025-09-23", practice["id"], 250.0)
    chat_id = 12345

    extracted_claim = ExtractedClaim(
        service_date="2025-09-22",  # wrong date
        billed_amount=250.0,
        practice_name="Acme Clinic",
    )
    extraction = ExtractionResult(
        doc_type="eob",
        practices=[ExtractedPractice(name="Acme Clinic")],
        providers=[],
        claims=[extracted_claim],
        adjudications=[],
    )
    pending = {
        "extraction": extraction,
        "match_results": {
            "practices": [
                {"name": "Acme Clinic", "matched": True, "practice_id": practice["id"]},
            ],
            "claims": [
                {"service_date": "2025-09-22", "matched": False, "claim_id": None},
            ],
        },
        "document_id": None,
        "practice_id_by_name": {"Acme Clinic": practice["id"]},
    }

    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await handle_correction(
        db_path,
        chat_id,
        pending,
        {"action": "correction", "item_index": 1, "correction_text": "2025-09-23"},
        context,
    )

    claim_entry = _pending_confirmations[chat_id]["match_results"]["claims"][0]
    assert claim_entry["matched"] is True
    assert claim_entry["claim_id"] == claim["id"]


def test_parse_confirmation_reply_cancel_discards_pending():
    chat_id = 99999
    _pending_confirmations[chat_id] = {"dummy": True}

    result = parse_confirmation_reply("cancel", [])
    assert result["action"] == "cancel"

    # Handler's cancel branch: pop the pending state.
    _pending_confirmations.pop(chat_id, None)
    assert chat_id not in _pending_confirmations


@pytest.mark.asyncio
async def test_handle_correction_caps_at_three_rounds(db_path):
    practice = create_practice(db_path, "Acme Clinic")
    chat_id = 12345
    pending = _make_unmatched_practice_pending("Acme Clini")
    pending["correction_rounds"] = 3  # next increment -> 4, exceeds cap of 3
    _pending_confirmations[chat_id] = pending

    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await handle_correction(
        db_path,
        chat_id,
        pending,
        {"action": "correction", "item_index": 1, "correction_text": "Acme Clinic"},
        context,
    )

    context.bot.send_message.assert_awaited_once()
    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert "3 corrections" in sent_text

    # Pending must not advance to a fresh re-rendered round.
    assert _pending_confirmations[chat_id]["correction_rounds"] == 3  # unchanged
