import pytest
from unittest.mock import patch, MagicMock
from src.agent import build_messages, generate_response, get_system_prompt, prepare_conversation_history, format_messages_for_context, format_schedule_for_prompt
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

class TestFormatMessagesForContext:
    """Tests for format_messages_for_context function."""

    def test_formats_empty_list(self):
        assert format_messages_for_context([]) == ""

    def test_formats_single_inbound_message(self):
        messages = [{"direction": "inbound", "body": "I have a headache"}]
        result = format_messages_for_context(messages)
        assert result == "User: I have a headache"

    def test_formats_single_outbound_message(self):
        messages = [{"direction": "outbound", "body": "Got it, headache noted."}]
        result = format_messages_for_context(messages)
        assert result == "Luigi: Got it, headache noted."

    def test_formats_mixed_conversation(self):
        messages = [
            {"direction": "inbound", "body": "I have a migraine"},
            {"direction": "outbound", "body": "Got it, migraine noted."},
            {"direction": "inbound", "body": "Took ibuprofen"},
        ]
        result = format_messages_for_context(messages)
        expected = "User: I have a migraine\nLuigi: Got it, migraine noted.\nUser: Took ibuprofen"
        assert result == expected


def test_prepare_conversation_history():
    """Test that prepare_conversation_history limits to at most 5 messages."""
    # Create 10 message history
    history = [
        {"direction": "inbound", "body": f"Message {i}", "timestamp": "2024-01-01 10:00:00"}
        for i in range(10)
    ]
    
    filtered = prepare_conversation_history(history)
    assert len(filtered) == 5
    # Should take the last 5 messages
    assert filtered[0]["body"] == "Message 5"
    assert filtered[-1]["body"] == "Message 9"
    
    # Test with fewer than 5 messages
    short_history = history[:3]
    filtered = prepare_conversation_history(short_history)
    assert len(filtered) == 3
    assert filtered[0]["body"] == "Message 0"
    assert filtered[-1]["body"] == "Message 2"

def test_build_messages_includes_system_prompt():
    """Test that build_messages includes the system prompt."""
    conversation_history = []
    messages = build_messages(conversation_history)

    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == get_system_prompt(None)
    # No longer asks for name; greets warmly instead
    assert "What's your name?" not in messages[0]["content"]


def test_build_messages_with_user_name():
    """Test that build_messages personalizes prompt with user name."""
    conversation_history = []
    messages = build_messages(conversation_history, user_name="Alice")

    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert "Alice" in messages[0]["content"]
    assert "What's your name?" not in messages[0]["content"]


def test_system_prompt_includes_preferred_name_instruction():
    """System prompt should instruct Luigi to emit [PREFERRED_NAME: X] tag."""
    prompt = get_system_prompt(None)
    assert "PREFERRED_NAME" in prompt


def test_system_prompt_no_name_does_not_ask_for_name():
    """When no name is known, prompt should not ask for user's name."""
    prompt = get_system_prompt(None)
    assert "What's your name?" not in prompt


def test_build_messages_with_recent_context():
    """Test that build_messages includes recent context in system prompt."""
    conversation_history = []
    recent_context = "User: I have a migraine\nLuigi: Got it, migraine noted."
    messages = build_messages(conversation_history, user_name="Alice", recent_messages=recent_context)

    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert "migraine" in messages[0]["content"]
    assert "Recent Conversation Context" in messages[0]["content"]

def test_build_messages_maps_directions_correctly():
    """Test that build_messages correctly maps directions to roles."""
    conversation_history = [
        {"direction": "inbound", "body": "Hello", "timestamp": "2024-01-01 10:00:00"},
        {"direction": "outbound", "body": "Hi there!", "timestamp": "2024-01-01 10:01:00"},
        {"direction": "inbound", "body": "How are you?", "timestamp": "2024-01-01 10:02:00"},
    ]
    
    messages = build_messages(conversation_history)
    
    # Should have system prompt + 3 conversation messages (all 3 are <=5)
    assert len(messages) == 4
    
    # Check role mapping
    assert messages[1]["role"] == "user"  # inbound -> user
    assert messages[1]["content"] == "Hello"
    
    assert messages[2]["role"] == "assistant"  # outbound -> assistant
    assert messages[2]["content"] == "Hi there!"
    
    assert messages[3]["role"] == "user"  # inbound -> user
    assert messages[3]["content"] == "How are you?"

def test_build_messages_limits_to_5_messages():
    """Test that build_messages limits to at most 5 conversation messages."""
    # Create 7 messages
    conversation_history = [
        {"direction": "inbound", "body": f"Message {i}", "timestamp": "2024-01-01 10:00:00"}
        for i in range(7)
    ]
    
    messages = build_messages(conversation_history)
    
    # Should have system prompt + 5 messages (last 5 of 7)
    assert len(messages) == 6  # 1 system + 5 user messages
    
    # The messages should be from Message 2 to Message 6
    assert messages[1]["content"] == "Message 2"
    assert messages[5]["content"] == "Message 6"

def test_build_messages_handles_unknown_direction(caplog):
    """Test that build_messages handles unknown direction with warning."""
    conversation_history = [
        {"direction": "unknown", "body": "Test", "timestamp": "2024-01-01 10:00:00"},
    ]
    
    with caplog.at_level("WARNING"):
        messages = build_messages(conversation_history)
    
    # Should default to 'user' role
    assert messages[1]["role"] == "user"
    assert "Unknown direction: unknown" in caplog.text

def test_generate_response_returns_llm_content(mock_settings):
    """Test that generate_response returns LLM content on success."""
    conversation_history = [
        {"direction": "inbound", "body": "Hello", "timestamp": "2024-01-01 10:00:00"},
    ]
    
    # Mock the OpenAI client and response
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_message = MagicMock()
    mock_message.content = "Hello! How can I help you today?"
    mock_choice.message = mock_message
    mock_response.choices = [mock_choice]
    
    with patch('src.agent.OpenAI') as mock_openai_class, \
         patch('src.agent.get_settings', return_value=mock_settings):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        response = generate_response(conversation_history)
        
        # Verify the response
        assert response == "Hello! How can I help you today?"
        
        # Verify the API was called with correct parameters
        mock_openai_class.assert_called_once_with(
            api_key=mock_settings.OPENROUTER_API_KEY,
            base_url=mock_settings.OPENROUTER_BASE_URL
        )
        mock_client.chat.completions.create.assert_called_once_with(
            model=mock_settings.LLM_MODEL,
            messages=build_messages(conversation_history, None),
            max_tokens=300
        )

def test_generate_response_returns_fallback_on_api_error(mock_settings):
    """Test that generate_response returns fallback message on API error."""
    conversation_history = [
        {"direction": "inbound", "body": "Hello", "timestamp": "2024-01-01 10:00:00"},
    ]
    
    with patch('src.agent.OpenAI') as mock_openai_class, \
         patch('src.agent.get_settings', return_value=mock_settings):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mock_openai_class.return_value = mock_client
        
        response = generate_response(conversation_history)
        
        # Should return fallback message
        assert response == "The LLM call is failing, I'll try again soon."

def test_generate_response_handles_empty_response(mock_settings):
    """Test that generate_response handles empty LLM response."""
    conversation_history = [
        {"direction": "inbound", "body": "Hello", "timestamp": "2024-01-01 10:00:00"},
    ]
    
    # Mock empty response
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_message = MagicMock()
    mock_message.content = None  # Empty response
    mock_choice.message = mock_message
    mock_response.choices = [mock_choice]
    
    with patch('src.agent.OpenAI') as mock_openai_class, \
         patch('src.agent.get_settings', return_value=mock_settings):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        response = generate_response(conversation_history)
        
        # Should return fallback for empty response
        assert response == "I'm sorry, I didn't get a response. Could you try again?"

class TestFormatScheduleForPrompt:
    """Tests for format_schedule_for_prompt."""

    def test_empty_schedule_list(self):
        result = format_schedule_for_prompt([])
        assert "no check-ins" in result.lower()

    def test_single_active_schedule(self):
        schedules = [{"hour": 10, "minute": 0, "active": True}]
        result = format_schedule_for_prompt(schedules)
        assert "1 check-in" in result
        assert "active" in result
        assert "10:00 AM" in result

    def test_multiple_schedules_with_mixed_status(self):
        schedules = [
            {"hour": 10, "minute": 0, "active": True},
            {"hour": 20, "minute": 0, "active": False},
        ]
        result = format_schedule_for_prompt(schedules)
        assert "2 check-in" in result
        assert "active" in result
        assert "paused" in result

    def test_pm_time_formatting(self):
        schedules = [{"hour": 20, "minute": 0, "active": True}]
        result = format_schedule_for_prompt(schedules)
        assert "PM" in result

    def test_midnight_edge_case(self):
        schedules = [{"hour": 0, "minute": 0, "active": True}]
        result = format_schedule_for_prompt(schedules)
        assert "12:00 AM" in result

    def test_noon_edge_case(self):
        schedules = [{"hour": 12, "minute": 0, "active": True}]
        result = format_schedule_for_prompt(schedules)
        assert "12:00 PM" in result


def test_system_prompt_includes_schedule_info():
    """System prompt includes schedule context when schedule_info is provided."""
    schedule_info = "The user has 2 check-in(s) configured:\n- 10:00 AM (active)\n- 8:00 PM (active)"
    prompt = get_system_prompt(None, schedule_info=schedule_info)
    assert "10:00 AM" in prompt
    assert "Current Check-in Schedule" in prompt


def test_system_prompt_omits_schedule_block_when_none():
    """System prompt omits schedule context block when schedule_info is None."""
    prompt = get_system_prompt(None, schedule_info=None)
    assert "Current Check-in Schedule" not in prompt


def test_system_prompt_includes_schedule_management_instructions():
    """System prompt always includes schedule management tag instructions."""
    prompt = get_system_prompt(None)
    assert "SCHEDULE_ADD" in prompt
    assert "SCHEDULE_REMOVE" in prompt
    assert "SCHEDULE_UPDATE" in prompt
    assert "SCHEDULE_PAUSE" in prompt
    assert "SCHEDULE_RESUME" in prompt


def test_build_messages_passes_schedule_info():
    """build_messages includes schedule_info in the system prompt."""
    schedule_info = "The user has 1 check-in(s) configured:\n- 10:00 AM (active)"
    messages = build_messages([], schedule_info=schedule_info)
    assert schedule_info in messages[0]["content"]


def test_generate_response_logs_error_on_failure(mock_settings, caplog):
    """Test that generate_response logs errors on failure."""
    conversation_history = [
        {"direction": "inbound", "body": "Hello", "timestamp": "2024-01-01 10:00:00"},
    ]

    with patch('src.agent.OpenAI') as mock_openai_class, \
         patch('src.agent.get_settings', return_value=mock_settings):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("Test error")
        mock_openai_class.return_value = mock_client

        with caplog.at_level("ERROR"):
            response = generate_response(conversation_history)

        # Should log the error
        assert "LLM API call failed" in caplog.text
        # Should return fallback message
        assert response == "The LLM call is failing, I'll try again soon."


