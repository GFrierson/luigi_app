import pytest
from unittest.mock import patch, MagicMock
from src.sms import get_twilio_client, send_sms, parse_inbound_sms
from src.config import Settings
from twilio.rest import Client

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

def test_get_twilio_client(mock_settings):
    """Test that get_twilio_client creates a client with correct credentials."""
    with patch('src.sms.Client') as mock_twilio_class:
        client = get_twilio_client(mock_settings)
        
        mock_twilio_class.assert_called_once_with(
            mock_settings.TWILIO_ACCOUNT_SID,
            mock_settings.TWILIO_AUTH_TOKEN
        )

def test_send_sms_returns_message_sid(mock_settings):
    """Test that send_sms returns the message SID on success."""
    test_body = "Test message"
    expected_sid = "SM1234567890abcdef"
    
    # Mock the Twilio message response
    mock_message = MagicMock()
    mock_message.sid = expected_sid
    
    # Mock the Twilio client
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message
    
    with patch('src.sms.get_settings', return_value=mock_settings), \
         patch('src.sms.get_twilio_client', return_value=mock_client):
        
        message_sid = send_sms(test_body)
        
        # Verify the response
        assert message_sid == expected_sid
        
        # Verify the API was called with correct parameters
        mock_client.messages.create.assert_called_once_with(
            body=test_body,
            from_=mock_settings.TWILIO_PHONE_NUMBER,
            to=mock_settings.USER_PHONE_NUMBER
        )

def test_send_sms_logs_on_success(mock_settings, caplog):
    """Test that send_sms logs success message."""
    test_body = "Test message"
    expected_sid = "SM1234567890abcdef"
    
    # Mock the Twilio message response
    mock_message = MagicMock()
    mock_message.sid = expected_sid
    
    # Mock the Twilio client
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message
    
    with patch('src.sms.get_settings', return_value=mock_settings), \
         patch('src.sms.get_twilio_client', return_value=mock_client), \
         caplog.at_level("INFO"):
        
        send_sms(test_body)
        
        # Verify log message
        assert "SMS sent successfully" in caplog.text
        assert expected_sid in caplog.text

def test_send_sms_raises_on_failure(mock_settings):
    """Test that send_sms raises exception on Twilio API failure."""
    test_body = "Test message"
    
    # Mock the Twilio client to raise an exception
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("Twilio API Error")
    
    with patch('src.sms.get_settings', return_value=mock_settings), \
         patch('src.sms.get_twilio_client', return_value=mock_client):
        
        with pytest.raises(Exception, match="Twilio API Error"):
            send_sms(test_body)

def test_send_sms_logs_error_on_failure(mock_settings, caplog):
    """Test that send_sms logs error on failure."""
    test_body = "Test message"
    
    # Mock the Twilio client to raise an exception
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("Twilio API Error")
    
    with patch('src.sms.get_settings', return_value=mock_settings), \
         patch('src.sms.get_twilio_client', return_value=mock_client), \
         caplog.at_level("ERROR"):
        
        try:
            send_sms(test_body)
        except Exception:
            pass  # Expected to raise
        
        # Verify error was logged
        assert "Failed to send SMS" in caplog.text
        assert "Twilio API Error" in caplog.text

def test_parse_inbound_sms_extracts_fields():
    """Test that parse_inbound_sms correctly extracts fields from form data."""
    form_data = {
        'Body': 'Hello Luigi, how are you?',
        'From': '+1234567890',
        'MessageSid': 'SM1234567890abcdef'
    }
    
    result = parse_inbound_sms(form_data)
    
    assert result['body'] == 'Hello Luigi, how are you?'
    assert result['from_number'] == '+1234567890'
    assert result['twilio_sid'] == 'SM1234567890abcdef'

def test_parse_inbound_sms_handles_missing_fields(caplog):
    """Test that parse_inbound_sms handles missing fields with warnings."""
    form_data = {
        'Body': '',  # Empty body
        # Missing 'From' field
        'MessageSid': 'SM1234567890abcdef'
    }
    
    with caplog.at_level("WARNING"):
        result = parse_inbound_sms(form_data)
    
    # Should still return dict with empty strings
    assert result['body'] == ''
    assert result['from_number'] == ''
    assert result['twilio_sid'] == 'SM1234567890abcdef'
    
    # Should log warnings
    assert "Inbound SMS has empty body" in caplog.text
    assert "Inbound SMS missing 'From' field" in caplog.text

def test_parse_inbound_sms_strips_whitespace():
    """Test that parse_inbound_sms strips whitespace from fields."""
    form_data = {
        'Body': '  Hello there!  \n',
        'From': '  +1234567890  ',
        'MessageSid': '  SM1234567890abcdef  '
    }
    
    result = parse_inbound_sms(form_data)
    
    assert result['body'] == 'Hello there!'
    assert result['from_number'] == '+1234567890'
    assert result['twilio_sid'] == 'SM1234567890abcdef'
