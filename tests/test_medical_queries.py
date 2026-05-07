"""Tests for src/medical/queries.py, src/medical/alerts.py, and Phase 5 commands."""

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.database import get_connection, init_db
from src.medical.alerts import send_member_holds_nudge
from src.medical.claims import adjudicate_claim, create_claim
from src.medical.entities import create_practice
from src.medical.payments import apply_payment, record_payment
from src.medical.queries import (
    get_claim_obligation,
    get_global_obligations,
    get_member_holds_overdue,
    get_obligations_by_practice,
    get_readjudicated_claims,
    get_recent_readjudication_events,
)
from src.config import Settings
from src.telegram_handler import balance_command


@pytest.fixture
def mock_settings():
    """Mock Settings instance for telegram handler tests."""
    return Settings(
        TELEGRAM_BOT_TOKEN="test_bot_token",
        OPENROUTER_API_KEY="test_api_key",
        OPENROUTER_BASE_URL="https://test.openrouter.ai/api/v1",
        LLM_MODEL="test-model",
        TIMEZONE="America/New_York",
        DATABASE_DIR="test_data/",
        DOCUMENTS_DIR="test_data/documents/",
        LOG_LEVEL="INFO",
    )


@pytest.fixture
def db_path(tmp_path):
    """Create an isolated SQLite DB initialized with init_db()."""
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


@pytest.fixture
def practice_id(db_path):
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    assert practice is not None
    return practice["id"]


# ---------------------------------------------------------------------------
# get_claim_obligation
# ---------------------------------------------------------------------------

def test_get_claim_obligation_no_adjudication(db_path, practice_id):
    """A claim with no adjudication has billed_amount but member_owed=0, net=0."""
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    row = get_claim_obligation(db_path, claim["id"])
    assert row is not None
    assert row["claim_id"] == claim["id"]
    assert row["billed_amount"] == 250.00
    assert row["member_owed"] == 0.0
    assert row["payments_applied"] == 0.0
    assert row["net_obligation"] == 0.0
    assert row["practice_name"] == "Manhattan Pain Medicine"


def test_get_claim_obligation_after_adjudication(db_path, practice_id):
    """An adjudicated claim with no payments has net_obligation == member_owed."""
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    adjudicate_claim(
        db_path, claim["id"], "2025-11-06",
        allowed_amount=200.00, plan_paid=120.00, member_owed=80.00,
    )
    row = get_claim_obligation(db_path, claim["id"])
    assert row is not None
    assert row["member_owed"] == 80.00
    assert row["payments_applied"] == 0.0
    assert row["net_obligation"] == 80.00


def test_get_claim_obligation_with_payment(db_path, practice_id):
    """An adjudicated claim with a partial payment shows reduced net_obligation."""
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    adjudicate_claim(
        db_path, claim["id"], "2025-11-06",
        allowed_amount=200.00, plan_paid=120.00, member_owed=80.00,
    )
    payment = record_payment(
        db_path, "2025-11-15", 30.00, "member", "practice", None, None, None,
    )
    apply_payment(db_path, payment["id"], claim["id"], 30.00)

    row = get_claim_obligation(db_path, claim["id"])
    assert row is not None
    assert row["member_owed"] == 80.00
    assert row["payments_applied"] == 30.00
    assert row["net_obligation"] == 50.00


def test_get_claim_obligation_readjudicated_uses_latest(db_path, practice_id):
    """After a re-adjudication, get_claim_obligation uses the current revision."""
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    adjudicate_claim(
        db_path, claim["id"], "2025-11-06",
        allowed_amount=200.00, plan_paid=100.00, member_owed=100.00,
    )
    adjudicate_claim(
        db_path, claim["id"], "2026-01-15",
        allowed_amount=200.00, plan_paid=140.00, member_owed=60.00,
    )
    row = get_claim_obligation(db_path, claim["id"])
    assert row is not None
    assert row["member_owed"] == 60.00
    assert row["net_obligation"] == 60.00


# ---------------------------------------------------------------------------
# get_obligations_by_practice / get_global_obligations
# ---------------------------------------------------------------------------

def test_get_obligations_by_practice_filters(db_path, practice_id):
    """Only the queried practice's claims appear."""
    other = create_practice(db_path, "Sunny Internal Medicine")
    create_claim(db_path, "2025-09-23", practice_id, 250.00)
    create_claim(db_path, "2025-09-23", other["id"], 175.00)

    rows = get_obligations_by_practice(db_path, practice_id)
    assert len(rows) == 1
    assert rows[0]["billing_practice_id"] == practice_id
    assert rows[0]["practice_name"] == "Manhattan Pain Medicine"


def test_get_global_obligations_returns_all(db_path, practice_id):
    """All claims from all practices appear in global obligations."""
    other = create_practice(db_path, "Sunny Internal Medicine")
    create_claim(db_path, "2025-09-23", practice_id, 250.00)
    create_claim(db_path, "2025-10-01", other["id"], 175.00)

    rows = get_global_obligations(db_path)
    assert len(rows) == 2
    practice_ids = {r["billing_practice_id"] for r in rows}
    assert practice_ids == {practice_id, other["id"]}


# ---------------------------------------------------------------------------
# get_member_holds_overdue
# ---------------------------------------------------------------------------

def test_get_member_holds_overdue_threshold(db_path, practice_id):
    """A payment dated 2026-01-01 returns under threshold=7, hides under threshold=9999."""
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    # Hardcoded past date — well over 7 days ago, well under 9999.
    payment = record_payment(
        db_path, "2026-01-01", 100.00, "insurer", "member", "check", "REF-1", None,
    )
    apply_payment(db_path, payment["id"], claim["id"], 100.00)

    visible = get_member_holds_overdue(db_path, 7)
    assert len(visible) == 1
    assert visible[0]["payment_id"] == payment["id"]
    assert visible[0]["practice_name"] == "Manhattan Pain Medicine"
    assert visible[0]["held_amount"] == 100.00

    hidden = get_member_holds_overdue(db_path, 9999)
    assert hidden == []


def test_get_member_holds_overdue_empty(db_path):
    """No payments at all returns []."""
    assert get_member_holds_overdue(db_path, 7) == []


# ---------------------------------------------------------------------------
# get_readjudicated_claims
# ---------------------------------------------------------------------------

def test_get_readjudicated_claims_empty(db_path, practice_id):
    """A claim with only one adjudication is NOT considered re-adjudicated."""
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    adjudicate_claim(
        db_path, claim["id"], "2025-11-06",
        allowed_amount=200.00, plan_paid=120.00, member_owed=80.00,
    )
    assert get_readjudicated_claims(db_path) == []


def test_get_readjudicated_claims_revision_2(db_path, practice_id):
    """A twice-adjudicated claim appears with revision=2 and practice_name."""
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    adjudicate_claim(
        db_path, claim["id"], "2025-11-06",
        allowed_amount=200.00, plan_paid=100.00, member_owed=100.00,
    )
    adjudicate_claim(
        db_path, claim["id"], "2026-01-15",
        allowed_amount=200.00, plan_paid=140.00, member_owed=60.00,
    )
    rows = get_readjudicated_claims(db_path)
    assert len(rows) == 1
    assert rows[0]["claim_id"] == claim["id"]
    assert rows[0]["revision"] == 2
    assert rows[0]["practice_name"] == "Manhattan Pain Medicine"
    assert rows[0]["service_date"] == "2025-09-23"


# ---------------------------------------------------------------------------
# get_recent_readjudication_events
# ---------------------------------------------------------------------------

def test_get_recent_readjudication_events_returns_todays(db_path, practice_id):
    """A 'readjudicated' claim_event with default occurred_at (now) is returned."""
    claim = create_claim(db_path, "2025-09-23", practice_id, 250.00)
    # Insert event directly so we control the event_type and let occurred_at default.
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO claim_events (claim_id, event_type, payload)
            VALUES (?, 'readjudicated', ?)
            """,
            (claim["id"], '{"revision": 2}'),
        )
        conn.commit()
    finally:
        conn.close()

    events = get_recent_readjudication_events(db_path)
    assert len(events) == 1
    assert events[0]["claim_id"] == claim["id"]
    assert events[0]["event_type"] == "readjudicated"


# ---------------------------------------------------------------------------
# send_member_holds_nudge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_member_holds_nudge_sends_when_overdue(db_path):
    """When holds exist, send_message is called once with a nudge."""
    fake_holds = [
        {"practice_name": "A", "held_amount": 100.0, "days_held": 30},
        {"practice_name": "B", "held_amount": 50.0, "days_held": 10},
    ]
    with patch(
        "src.medical.alerts.get_member_holds_overdue", return_value=fake_holds,
    ), patch(
        "src.medical.alerts.send_message", new_callable=AsyncMock,
    ) as mock_send:
        await send_member_holds_nudge(db_path, 12345)

    mock_send.assert_called_once()
    call_args = mock_send.call_args
    assert call_args.args[0] == 12345
    assert "2 payment" in call_args.args[1]
    assert "/pending" in call_args.args[1]


@pytest.mark.asyncio
async def test_send_member_holds_nudge_silent_when_empty(db_path):
    """When no holds, send_message is NOT called."""
    with patch(
        "src.medical.alerts.get_member_holds_overdue", return_value=[],
    ), patch(
        "src.medical.alerts.send_message", new_callable=AsyncMock,
    ) as mock_send:
        await send_member_holds_nudge(db_path, 12345)

    mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# balance_command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_balance_command_no_obligations(tmp_path, mock_settings):
    """When global obligations is empty, replies with 'No outstanding balance.'"""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()
    mock_chat = MagicMock()
    mock_chat.id = 12345

    mock_update = MagicMock()
    mock_update.effective_chat = mock_chat
    mock_update.message = mock_message

    with patch(
        "src.telegram_handler.get_settings", return_value=mock_settings,
    ), patch(
        "src.telegram_handler.get_user_db_path", return_value=db_path,
    ), patch(
        "src.telegram_handler.get_global_obligations", return_value=[],
    ):
        await balance_command(mock_update, MagicMock())

    mock_message.reply_text.assert_called_once_with("No outstanding balance.")


@pytest.mark.asyncio
async def test_balance_command_formats_output(tmp_path, mock_settings):
    """When obligations exist, reply contains practice name and dollar amount."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    fake_rows = [
        {
            "claim_id": 1,
            "billing_practice_id": 7,
            "practice_name": "Manhattan Pain Medicine",
            "net_obligation": 4501.50,
        },
    ]

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()
    mock_chat = MagicMock()
    mock_chat.id = 12345

    mock_update = MagicMock()
    mock_update.effective_chat = mock_chat
    mock_update.message = mock_message

    with patch(
        "src.telegram_handler.get_settings", return_value=mock_settings,
    ), patch(
        "src.telegram_handler.get_user_db_path", return_value=db_path,
    ), patch(
        "src.telegram_handler.get_global_obligations", return_value=fake_rows,
    ):
        await balance_command(mock_update, MagicMock())

    mock_message.reply_text.assert_called_once()
    reply = mock_message.reply_text.call_args.args[0]
    assert "Manhattan Pain Medicine" in reply
    assert "$4501.50" in reply
