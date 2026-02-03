import pytest
from unittest.mock import patch, MagicMock
from src.agent import build_messages, generate_response, SYSTEM_PROMPT, prepare_conversation_history
from src.config import Settings

@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    return Settings(
        TWILIO_ACCOUNT_SID="test_sid",
        TWILIO_AUTH_TOKEN="test_token",
        TWILIO_PHONE_NUMBER="+1234567890",
        USER_PHONE_NUMBER="+0987654321",
        OPENROUTER_API_KEY="test_api_key",
        OPENROUTER_BASE_URL="https://test.openrouter.ai/api/v1",
        LLM_MODEL="test-model",
        TIMEZONE="America/New_York",
        DATABASE_PATH="test.db",
        LOG_LEVEL="INFO"
    )

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
    assert messages[0]["content"] == SYSTEM_PROMPT

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
            messages=build_messages(conversation_history),
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
