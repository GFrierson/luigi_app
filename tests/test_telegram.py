import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from src.telegram_handler import get_bot, send_message, parse_update
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


def test_get_bot_creates_bot_with_token(mock_settings):
    """Test that get_bot creates a Bot with the correct token."""
    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.Bot') as mock_bot_class:
        get_bot()
        mock_bot_class.assert_called_once_with(token=mock_settings.TELEGRAM_BOT_TOKEN)


@pytest.mark.asyncio
async def test_send_message_returns_message_id(mock_settings):
    """Test that send_message returns the message_id on success."""
    test_chat_id = 123456789
    test_text = "Test message"
    expected_msg_id = 42

    # Mock the bot's send_message response
    mock_message = MagicMock()
    mock_message.message_id = expected_msg_id

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=mock_message)

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.get_bot', return_value=mock_bot):

        message_id = await send_message(test_chat_id, test_text)

        assert message_id == expected_msg_id
        mock_bot.send_message.assert_called_once_with(
            chat_id=test_chat_id,
            text=test_text
        )


@pytest.mark.asyncio
async def test_send_message_logs_on_success(mock_settings, caplog):
    """Test that send_message logs success message."""
    test_chat_id = 123456789
    test_text = "Test message"
    expected_msg_id = 42

    mock_message = MagicMock()
    mock_message.message_id = expected_msg_id

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(return_value=mock_message)

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.get_bot', return_value=mock_bot), \
         caplog.at_level("INFO"):

        await send_message(test_chat_id, test_text)

        assert "Telegram message sent successfully" in caplog.text
        assert str(expected_msg_id) in caplog.text


@pytest.mark.asyncio
async def test_send_message_raises_on_failure(mock_settings):
    """Test that send_message raises exception on Telegram API failure."""
    test_chat_id = 123456789
    test_text = "Test message"

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=Exception("Telegram API Error"))

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.get_bot', return_value=mock_bot):

        with pytest.raises(Exception, match="Telegram API Error"):
            await send_message(test_chat_id, test_text)


@pytest.mark.asyncio
async def test_send_message_logs_error_on_failure(mock_settings, caplog):
    """Test that send_message logs error on failure."""
    test_chat_id = 123456789
    test_text = "Test message"

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=Exception("Telegram API Error"))

    with patch('src.telegram_handler.get_settings', return_value=mock_settings), \
         patch('src.telegram_handler.get_bot', return_value=mock_bot), \
         caplog.at_level("ERROR"):

        try:
            await send_message(test_chat_id, test_text)
        except Exception:
            pass  # Expected to raise

        assert "Failed to send Telegram message" in caplog.text
        assert "Telegram API Error" in caplog.text


def test_parse_update_extracts_fields():
    """Test that parse_update correctly extracts fields from update data."""
    update_data = {
        'update_id': 123456,
        'message': {
            'message_id': 42,
            'chat': {
                'id': 123456789,
                'type': 'private'
            },
            'text': 'Hello Luigi!'
        }
    }

    result = parse_update(update_data)

    assert result['chat_id'] == 123456789
    assert result['text'] == 'Hello Luigi!'
    assert result['message_id'] == 42


def test_parse_update_handles_missing_message(caplog):
    """Test that parse_update handles missing message field."""
    update_data = {
        'update_id': 123456
    }

    with caplog.at_level("WARNING"):
        result = parse_update(update_data)

    assert result['chat_id'] is None
    assert result['text'] == ''
    assert result['message_id'] is None
    assert "missing chat_id" in caplog.text
    assert "empty text" in caplog.text
    assert "missing message_id" in caplog.text


def test_parse_update_handles_empty_text(caplog):
    """Test that parse_update handles empty text."""
    update_data = {
        'update_id': 123456,
        'message': {
            'message_id': 42,
            'chat': {
                'id': 123456789
            },
            'text': ''
        }
    }

    with caplog.at_level("WARNING"):
        result = parse_update(update_data)

    assert result['chat_id'] == 123456789
    assert result['text'] == ''
    assert "empty text" in caplog.text


def test_parse_update_strips_whitespace():
    """Test that parse_update strips whitespace from text."""
    update_data = {
        'update_id': 123456,
        'message': {
            'message_id': 42,
            'chat': {
                'id': 123456789
            },
            'text': '  Hello there!  \n'
        }
    }

    result = parse_update(update_data)

    assert result['text'] == 'Hello there!'


def test_parse_update_handles_photo_message(caplog):
    """Test that parse_update handles messages without text (e.g., photos)."""
    update_data = {
        'update_id': 123456,
        'message': {
            'message_id': 42,
            'chat': {
                'id': 123456789
            },
            'photo': [{'file_id': 'some_file_id'}]
            # No 'text' field
        }
    }

    with caplog.at_level("WARNING"):
        result = parse_update(update_data)

    assert result['chat_id'] == 123456789
    assert result['text'] == ''
    assert result['message_id'] == 42
    assert "empty text" in caplog.text
