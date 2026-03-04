import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock, AsyncMock
from telegram import Update, Message, Chat
from telegram.ext import Application

from src.telegram_handler import send_message, start_command, handle_message, _on_message, create_application, _extract_preferred_name_tag, _process_medication_action
from src.config import Settings
from src.database import init_db, create_medication_group, create_medication


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


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_returns_message_id(mock_settings):
    """send_message returns the message_id on success."""
    expected_msg_id = 42
    mock_message = MagicMock()
    mock_message.message_id = expected_msg_id

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=mock_message)

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.Bot', return_value=mock_bot):
        message_id = await send_message(123456789, "Test message")

    assert message_id == expected_msg_id
    mock_bot.send_message.assert_called_once_with(chat_id=123456789, text="Test message")


@pytest.mark.asyncio
async def test_send_message_logs_on_success(mock_settings, caplog):
    """send_message logs a success message."""
    mock_message = MagicMock()
    mock_message.message_id = 42

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=mock_message)

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.Bot', return_value=mock_bot), \
         caplog.at_level("INFO"):
        await send_message(123456789, "Test message")

    assert "Telegram message sent successfully" in caplog.text
    assert "42" in caplog.text


@pytest.mark.asyncio
async def test_send_message_raises_on_failure(mock_settings):
    """send_message propagates Telegram API exceptions."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=Exception("Telegram API Error"))

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.Bot', return_value=mock_bot):
        with pytest.raises(Exception, match="Telegram API Error"):
            await send_message(123456789, "Test message")


@pytest.mark.asyncio
async def test_send_message_logs_error_on_failure(mock_settings, caplog):
    """send_message logs an error on failure."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=Exception("Telegram API Error"))

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.Bot', return_value=mock_bot), \
         caplog.at_level("ERROR"):
        try:
            await send_message(123456789, "Test message")
        except Exception:
            pass

    assert "Failed to send Telegram message" in caplog.text
    assert "Telegram API Error" in caplog.text


# ---------------------------------------------------------------------------
# start_command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_command_sends_welcome():
    """start_command replies with a welcome message."""
    mock_message = MagicMock()
    mock_message.chat_id = 111222333
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()

    await start_command(mock_update, mock_context)

    mock_message.reply_text.assert_called_once()
    reply_text = mock_message.reply_text.call_args[0][0]
    assert "Luigi" in reply_text


@pytest.mark.asyncio
async def test_start_command_logs_chat_id(caplog):
    """start_command logs the new user's chat_id."""
    mock_message = MagicMock()
    mock_message.chat_id = 987654321
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    with caplog.at_level("INFO"):
        await start_command(mock_update, MagicMock())

    assert "987654321" in caplog.text


# ---------------------------------------------------------------------------
# _on_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_message_calls_handle_message():
    """_on_message extracts fields from update and delegates to handle_message."""
    mock_from_user = MagicMock()
    mock_from_user.first_name = "Alice"

    mock_message = MagicMock()
    mock_message.chat_id = 555666777
    mock_message.text = "Hello Luigi"
    mock_message.message_id = 99
    mock_message.from_user = mock_from_user

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.bot_data = {"scheduler": None}

    with patch('src.telegram_handler.handle_message', new_callable=AsyncMock) as mock_handle:
        await _on_message(mock_update, mock_context)

    mock_handle.assert_called_once_with(555666777, "Hello Luigi", 99, scheduler=None, telegram_first_name="Alice")


@pytest.mark.asyncio
async def test_on_message_ignores_empty_text():
    """_on_message does nothing when the message has no text."""
    mock_message = MagicMock()
    mock_message.chat_id = 555666777
    mock_message.text = ""

    mock_update = MagicMock()
    mock_update.message = mock_message

    with patch('src.telegram_handler.handle_message', new_callable=AsyncMock) as mock_handle:
        await _on_message(mock_update, MagicMock())

    mock_handle.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_ignores_no_message():
    """_on_message does nothing when update has no message."""
    mock_update = MagicMock()
    mock_update.message = None

    with patch('src.telegram_handler.handle_message', new_callable=AsyncMock) as mock_handle:
        await _on_message(mock_update, MagicMock())

    mock_handle.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_passes_scheduler_from_bot_data():
    """_on_message passes the scheduler from context.bot_data to handle_message."""
    mock_scheduler = MagicMock()

    mock_from_user = MagicMock()
    mock_from_user.first_name = "Bob"

    mock_message = MagicMock()
    mock_message.chat_id = 111
    mock_message.text = "hi"
    mock_message.message_id = 1
    mock_message.from_user = mock_from_user

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.bot_data = {"scheduler": mock_scheduler}

    with patch('src.telegram_handler.handle_message', new_callable=AsyncMock) as mock_handle:
        await _on_message(mock_update, mock_context)

    mock_handle.assert_called_once_with(111, "hi", 1, scheduler=mock_scheduler, telegram_first_name="Bob")


# ---------------------------------------------------------------------------
# create_application
# ---------------------------------------------------------------------------

def test_create_application_returns_application():
    """create_application returns a built Application instance."""
    with patch('src.telegram_handler.Application') as mock_app_class:
        mock_builder = MagicMock()
        mock_app_class.builder.return_value = mock_builder
        mock_builder.token.return_value = mock_builder
        mock_built = MagicMock()
        mock_builder.build.return_value = mock_built

        result = create_application("fake_token")

    mock_app_class.builder.assert_called_once()
    mock_builder.token.assert_called_once_with("fake_token")
    assert result is mock_built


# ---------------------------------------------------------------------------
# _extract_preferred_name_tag
# ---------------------------------------------------------------------------

def test_extract_preferred_name_tag_finds_tag():
    """_extract_preferred_name_tag extracts name and strips the tag."""
    text = "Of course, I'll call you Nel from now on. [PREFERRED_NAME: Nel]"
    cleaned, name = _extract_preferred_name_tag(text)
    assert name == "Nel"
    assert "[PREFERRED_NAME" not in cleaned
    assert "Of course" in cleaned


def test_extract_preferred_name_tag_returns_none_when_absent():
    """_extract_preferred_name_tag returns None name when no tag present."""
    text = "Got it, headache noted."
    cleaned, name = _extract_preferred_name_tag(text)
    assert name is None
    assert cleaned == text


def test_extract_preferred_name_tag_handles_whitespace():
    """_extract_preferred_name_tag handles whitespace around name."""
    text = "Sure! [PREFERRED_NAME:   Sam  ]"
    cleaned, name = _extract_preferred_name_tag(text)
    assert name == "Sam"
    assert "[PREFERRED_NAME" not in cleaned


# ---------------------------------------------------------------------------
# Medication flow integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db_path():
    """Create a temporary DB, init it, and yield its path."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_message_triggers_extraction_call(mock_settings, temp_db_path):
    """After generate_response(), extract_medication_action() is called."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.get_user_db_path', return_value=temp_db_path), \
         patch('src.telegram_handler.Bot', return_value=mock_bot), \
         patch('src.telegram_handler.generate_response', return_value="Noted."), \
         patch('src.telegram_handler.extract_medication_action', return_value={"action": "none"}) as mock_extract:
        await handle_message(123, "I have a headache", 1)

    mock_extract.assert_called_once()


@pytest.mark.asyncio
async def test_log_group_action_creates_events(mock_settings, temp_db_path):
    """When extraction returns log_group, log_group_events() is called with correct IDs."""
    group_id = create_medication_group(temp_db_path, "morning meds", None, 8, 0)
    med_id = create_medication(temp_db_path, "metformin", "500mg", "scheduled", group_id)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=2))

    extraction_result = {"action": "log_group", "group_name": "morning meds", "taken": "all", "skipped": []}

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.get_user_db_path', return_value=temp_db_path), \
         patch('src.telegram_handler.Bot', return_value=mock_bot), \
         patch('src.telegram_handler.generate_response', return_value="Got it."), \
         patch('src.telegram_handler.extract_medication_action', return_value=extraction_result), \
         patch('src.telegram_handler.log_group_events') as mock_log_group:
        await handle_message(123, "took my morning meds", 2)

    mock_log_group.assert_called_once_with(temp_db_path, group_id, [med_id], [])


@pytest.mark.asyncio
async def test_none_action_skips_db_writes(mock_settings, temp_db_path):
    """When extraction returns none, no medication DB writes occur."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=3))

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.get_user_db_path', return_value=temp_db_path), \
         patch('src.telegram_handler.Bot', return_value=mock_bot), \
         patch('src.telegram_handler.generate_response', return_value="Noted."), \
         patch('src.telegram_handler.extract_medication_action', return_value={"action": "none"}), \
         patch('src.telegram_handler.log_group_events') as mock_log_group, \
         patch('src.telegram_handler.log_medication_event') as mock_log_single:
        await handle_message(123, "I have a headache", 3)

    mock_log_group.assert_not_called()
    mock_log_single.assert_not_called()


@pytest.mark.asyncio
async def test_add_medication_action_stages_pending(mock_settings, temp_db_path, caplog):
    """When extraction returns add_medication, it is staged (not committed), logged."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=4))

    extraction_result = {
        "action": "add_medication",
        "name": "metformin",
        "dosage": "500mg",
        "type": "scheduled",
        "group_name": None,
        "schedule_hour": 8,
        "schedule_minute": 0,
    }

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.get_user_db_path', return_value=temp_db_path), \
         patch('src.telegram_handler.Bot', return_value=mock_bot), \
         patch('src.telegram_handler.generate_response', return_value="Got it."), \
         patch('src.telegram_handler.extract_medication_action', return_value=extraction_result), \
         patch('src.telegram_handler.log_group_events') as mock_log_group, \
         caplog.at_level("INFO"):
        await handle_message(123, "I take metformin 500mg every morning at 8", 4)

    mock_log_group.assert_not_called()
    assert "staged" in caplog.text


@pytest.mark.asyncio
async def test_extraction_failure_does_not_block_response(mock_settings, temp_db_path):
    """If extraction throws an exception, the user still receives Luigi's response."""
    mock_bot = MagicMock()
    mock_sent_message = MagicMock()
    mock_sent_message.message_id = 5
    mock_bot.send_message = AsyncMock(return_value=mock_sent_message)

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.get_user_db_path', return_value=temp_db_path), \
         patch('src.telegram_handler.Bot', return_value=mock_bot), \
         patch('src.telegram_handler.generate_response', return_value="Noted."), \
         patch('src.telegram_handler.extract_medication_action', side_effect=Exception("boom")):
        response = await handle_message(123, "anything", 5)

    assert response == "Noted."
    mock_bot.send_message.assert_called_once()
