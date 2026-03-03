"""Tests for schedule tag parsing and action execution in telegram_handler."""
import pytest
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock, patch

from src.telegram_handler import (
    _extract_schedule_tag,
    _validate_schedule_time,
    _generate_template_for_time,
    _execute_schedule_action,
    ScheduleAction,
)
from src.database import init_db, add_schedule, get_active_schedules, get_all_schedules


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    init_db(db_path)
    yield db_path
    os.unlink(db_path)


# --- _validate_schedule_time ---

class TestValidateScheduleTime:
    def test_valid_times(self):
        assert _validate_schedule_time(0, 0) is True
        assert _validate_schedule_time(23, 59) is True
        assert _validate_schedule_time(12, 30) is True

    def test_invalid_hour(self):
        assert _validate_schedule_time(24, 0) is False
        assert _validate_schedule_time(-1, 0) is False

    def test_invalid_minute(self):
        assert _validate_schedule_time(12, 60) is False
        assert _validate_schedule_time(12, -1) is False


# --- _generate_template_for_time ---

class TestGenerateTemplateForTime:
    def test_morning(self):
        template = _generate_template_for_time(8, 0)
        assert "morning" in template.lower()

    def test_afternoon(self):
        template = _generate_template_for_time(14, 0)
        assert "afternoon" in template.lower()

    def test_evening(self):
        template = _generate_template_for_time(20, 0)
        assert "evening" in template.lower()

    def test_midnight_is_evening(self):
        template = _generate_template_for_time(0, 0)
        assert "evening" in template.lower()

    def test_noon_is_afternoon(self):
        template = _generate_template_for_time(12, 0)
        assert "afternoon" in template.lower()


# --- _extract_schedule_tag ---

class TestExtractScheduleTag:
    def test_add_tag(self):
        text = "Sure, I'll add a check-in at 2pm. [SCHEDULE_ADD: 14:00]"
        cleaned, action = _extract_schedule_tag(text)
        assert "[SCHEDULE_ADD" not in cleaned
        assert action is not None
        assert action.action == "ADD"
        assert action.hour == 14
        assert action.minute == 0

    def test_remove_tag(self):
        text = "Done, I've removed that check-in. [SCHEDULE_REMOVE: 20:00]"
        cleaned, action = _extract_schedule_tag(text)
        assert "[SCHEDULE_REMOVE" not in cleaned
        assert action.action == "REMOVE"
        assert action.hour == 20
        assert action.minute == 0

    def test_update_tag(self):
        text = "I've moved your morning check-in. [SCHEDULE_UPDATE: 10:00 > 08:00]"
        cleaned, action = _extract_schedule_tag(text)
        assert "[SCHEDULE_UPDATE" not in cleaned
        assert action.action == "UPDATE"
        assert action.hour == 10
        assert action.minute == 0
        assert action.new_hour == 8
        assert action.new_minute == 0

    def test_pause_tag(self):
        text = "All check-ins are now paused. [SCHEDULE_PAUSE]"
        cleaned, action = _extract_schedule_tag(text)
        assert "[SCHEDULE_PAUSE]" not in cleaned
        assert action.action == "PAUSE"

    def test_resume_tag(self):
        text = "Welcome back! Your check-ins have been resumed. [SCHEDULE_RESUME]"
        cleaned, action = _extract_schedule_tag(text)
        assert "[SCHEDULE_RESUME]" not in cleaned
        assert action.action == "RESUME"

    def test_no_tag(self):
        text = "I have a headache today."
        cleaned, action = _extract_schedule_tag(text)
        assert cleaned == text
        assert action is None

    def test_cleaned_text_is_stripped(self):
        text = "Check-in added. [SCHEDULE_ADD: 09:00]"
        cleaned, action = _extract_schedule_tag(text)
        assert cleaned == "Check-in added."

    def test_invalid_hour_in_add_returns_none_action(self):
        text = "Added. [SCHEDULE_ADD: 25:00]"
        cleaned, action = _extract_schedule_tag(text)
        assert action is None

    def test_invalid_minute_in_remove_returns_none_action(self):
        text = "Removed. [SCHEDULE_REMOVE: 10:60]"
        cleaned, action = _extract_schedule_tag(text)
        assert action is None

    def test_single_digit_hour(self):
        text = "Added. [SCHEDULE_ADD: 8:00]"
        cleaned, action = _extract_schedule_tag(text)
        assert action is not None
        assert action.hour == 8

    def test_update_tag_with_spaces(self):
        text = "Updated. [SCHEDULE_UPDATE: 10:00 > 09:30]"
        cleaned, action = _extract_schedule_tag(text)
        assert action is not None
        assert action.action == "UPDATE"
        assert action.new_hour == 9
        assert action.new_minute == 30


# --- _execute_schedule_action ---

class TestExecuteScheduleAction:
    @pytest.mark.asyncio
    async def test_add_action_inserts_schedule(self, temp_db):
        action = ScheduleAction(action="ADD", hour=14, minute=0)
        await _execute_schedule_action(action, chat_id=12345, db_path=temp_db, scheduler=None)

        schedules = get_all_schedules(temp_db)
        assert any(s['hour'] == 14 and s['minute'] == 0 for s in schedules)

    @pytest.mark.asyncio
    async def test_remove_action_deletes_schedule(self, temp_db):
        add_schedule(temp_db, 10, 0, "Morning")
        action = ScheduleAction(action="REMOVE", hour=10, minute=0)
        await _execute_schedule_action(action, chat_id=12345, db_path=temp_db, scheduler=None)

        schedules = get_all_schedules(temp_db)
        assert not any(s['hour'] == 10 and s['minute'] == 0 for s in schedules)

    @pytest.mark.asyncio
    async def test_update_action_changes_time(self, temp_db):
        add_schedule(temp_db, 10, 0, "Morning")
        action = ScheduleAction(action="UPDATE", hour=10, minute=0, new_hour=8, new_minute=0)
        await _execute_schedule_action(action, chat_id=12345, db_path=temp_db, scheduler=None)

        schedules = get_all_schedules(temp_db)
        assert any(s['hour'] == 8 and s['minute'] == 0 for s in schedules)
        assert not any(s['hour'] == 10 and s['minute'] == 0 for s in schedules)

    @pytest.mark.asyncio
    async def test_pause_action_deactivates_all(self, temp_db):
        add_schedule(temp_db, 10, 0, "Morning")
        add_schedule(temp_db, 20, 0, "Evening")
        action = ScheduleAction(action="PAUSE")
        await _execute_schedule_action(action, chat_id=12345, db_path=temp_db, scheduler=None)

        assert len(get_active_schedules(temp_db)) == 0

    @pytest.mark.asyncio
    async def test_resume_action_reactivates_all(self, temp_db):
        from src.database import deactivate_all_schedules
        add_schedule(temp_db, 10, 0, "Morning")
        add_schedule(temp_db, 20, 0, "Evening")
        deactivate_all_schedules(temp_db)

        action = ScheduleAction(action="RESUME")
        await _execute_schedule_action(action, chat_id=12345, db_path=temp_db, scheduler=None)

        assert len(get_active_schedules(temp_db)) == 2

    @pytest.mark.asyncio
    async def test_add_action_registers_scheduler_job(self, temp_db):
        mock_scheduler = MagicMock()
        action = ScheduleAction(action="ADD", hour=14, minute=0)

        with patch('src.scheduler.get_settings') as mock_settings:
            from src.config import Settings
            mock_settings.return_value = Settings(
                TELEGRAM_BOT_TOKEN="t",
                OPENROUTER_API_KEY="k",
                OPENROUTER_BASE_URL="https://test",
                LLM_MODEL="m",
                TIMEZONE="UTC",
                DATABASE_DIR="data/",
                LOG_LEVEL="INFO",
            )
            await _execute_schedule_action(action, chat_id=12345, db_path=temp_db, scheduler=mock_scheduler)

        mock_scheduler.add_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_action_removes_scheduler_job(self, temp_db):
        add_schedule(temp_db, 10, 0, "Morning")
        mock_scheduler = MagicMock()
        action = ScheduleAction(action="REMOVE", hour=10, minute=0)
        await _execute_schedule_action(action, chat_id=12345, db_path=temp_db, scheduler=mock_scheduler)

        mock_scheduler.remove_job.assert_called_once_with("checkin_12345_10_00")

    @pytest.mark.asyncio
    async def test_pause_action_removes_all_scheduler_jobs(self, temp_db):
        add_schedule(temp_db, 10, 0, "Morning")
        mock_scheduler = MagicMock()
        mock_scheduler.get_jobs.return_value = [
            MagicMock(id="checkin_12345_10_00"),
            MagicMock(id="checkin_12345_20_00"),
        ]
        action = ScheduleAction(action="PAUSE")
        await _execute_schedule_action(action, chat_id=12345, db_path=temp_db, scheduler=mock_scheduler)

        assert mock_scheduler.remove_job.call_count == 2
