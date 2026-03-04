"""Tests for medication reminder scheduling in src/scheduler.py."""
import pytest
import tempfile
import os
from datetime import date, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

from src.database import init_db, create_medication_group, create_medication
from src.scheduler import schedule_check_ins, send_medication_reminder
from src.config import Settings


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    return Settings(
        TELEGRAM_BOT_TOKEN="test_bot_token",
        OPENROUTER_API_KEY="test_api_key",
        OPENROUTER_BASE_URL="https://test.openrouter.ai/api/v1",
        LLM_MODEL="test-model",
        TIMEZONE="America/New_York",
        DATABASE_DIR="test_data/",
        LOG_LEVEL="INFO"
    )


@pytest.fixture
def temp_db():
    """Temporary initialized DB."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)


def test_medication_reminder_registered_for_group(mock_settings, temp_db):
    """A group with reminder_active=TRUE and a schedule gets a cron job registered."""
    create_medication_group(temp_db, "morning meds", None, 8, 0)

    mock_scheduler = MagicMock()
    mock_scheduler.get_jobs.return_value = []

    with patch('src.scheduler.get_settings', return_value=mock_settings), \
         patch('src.scheduler.get_all_user_databases', return_value=[(123, temp_db)]), \
         patch('src.scheduler.get_active_schedules', return_value=[]):
        schedule_check_ins(mock_scheduler)

    # add_job should have been called for the medication group
    job_calls = mock_scheduler.add_job.call_args_list
    job_ids = [call.kwargs.get('id', '') for call in job_calls]
    assert any("med_reminder_123" in jid for jid in job_ids), f"Expected med_reminder_123 in {job_ids}"


def test_medication_reminder_not_registered_when_inactive(mock_settings, temp_db):
    """A group with reminder_active=FALSE gets no job registered."""
    # Manually insert an inactive group
    from src.database import get_connection
    conn = get_connection(temp_db)
    conn.execute(
        "INSERT INTO medication_groups (name, schedule_hour, schedule_minute, reminder_active) VALUES (?, ?, ?, ?)",
        ("night meds", 22, 0, False)
    )
    conn.commit()
    conn.close()

    mock_scheduler = MagicMock()
    mock_scheduler.get_jobs.return_value = []

    with patch('src.scheduler.get_settings', return_value=mock_settings), \
         patch('src.scheduler.get_all_user_databases', return_value=[(123, temp_db)]), \
         patch('src.scheduler.get_active_schedules', return_value=[]):
        schedule_check_ins(mock_scheduler)

    job_calls = mock_scheduler.add_job.call_args_list
    job_ids = [call.kwargs.get('id', '') for call in job_calls]
    assert not any("med_reminder" in jid for jid in job_ids), f"Unexpected med_reminder job in {job_ids}"


@pytest.mark.asyncio
async def test_interval_check_fires_on_correct_day(mock_settings, temp_db):
    """send_medication_reminder fires on interval day and skips on off days."""
    create_medication_group(temp_db, "weekly meds", None, 8, 0, interval_days=7)

    # A start date 7 days ago → delta=7, 7%7=0 → should fire
    start_7_days_ago = (date.today() - timedelta(days=7)).isoformat()
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

    with patch('src.scheduler.send_message', new_callable=AsyncMock) as mock_send, \
         patch('src.scheduler.insert_message'):
        mock_send.return_value = 1
        # Should send (day 7)
        await send_medication_reminder("weekly meds", 123, temp_db, group_id=1, interval_days=7, start_date=start_7_days_ago)
        assert mock_send.called, "Expected reminder to fire on day 7"

    # A start date 3 days ago → delta=3, 3%7=3 → should NOT fire
    start_3_days_ago = (date.today() - timedelta(days=3)).isoformat()
    with patch('src.scheduler.send_message', new_callable=AsyncMock) as mock_send2, \
         patch('src.scheduler.insert_message'):
        mock_send2.return_value = 1
        await send_medication_reminder("weekly meds", 123, temp_db, group_id=1, interval_days=7, start_date=start_3_days_ago)
        assert not mock_send2.called, "Expected reminder NOT to fire on day 3"
