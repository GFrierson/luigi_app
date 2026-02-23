import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from telegram import Update, Message, Chat
from telegram.ext import Application

from src.telegram_handler import send_message, start_command, handle_message, _on_message, create_application
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
    mock_message = MagicMock()
    mock_message.chat_id = 555666777
    mock_message.text = "Hello Luigi"
    mock_message.message_id = 99

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.bot_data = {"scheduler": None}

    with patch('src.telegram_handler.handle_message', new_callable=AsyncMock) as mock_handle:
        await _on_message(mock_update, mock_context)

    mock_handle.assert_called_once_with(555666777, "Hello Luigi", 99, scheduler=None)


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

    mock_message = MagicMock()
    mock_message.chat_id = 111
    mock_message.text = "hi"
    mock_message.message_id = 1

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.bot_data = {"scheduler": mock_scheduler}

    with patch('src.telegram_handler.handle_message', new_callable=AsyncMock) as mock_handle:
        await _on_message(mock_update, mock_context)

    mock_handle.assert_called_once_with(111, "hi", 1, scheduler=mock_scheduler)


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
