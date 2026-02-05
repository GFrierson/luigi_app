import logging
from typing import Dict
from twilio.rest import Client
from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

def get_twilio_client(config: Settings) -> Client:
    """
    Create and return a Twilio client instance.
    
    Args:
        config: Application settings
        
    Returns:
        Twilio client instance
    """
    return Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)

def send_sms(body: str, to: str = None) -> str:
    """
    Send an SMS message to a recipient.

    Args:
        body: Message content to send
        to: Recipient phone number (defaults to USER_PHONE_NUMBER from config)

    Returns:
        Message SID from Twilio

    Raises:
        Exception: If Twilio API call fails
    """
    config = get_settings()
    client = get_twilio_client(config)
    recipient = to or config.USER_PHONE_NUMBER

    try:
        message = client.messages.create(
            body=body,
            from_=config.TWILIO_PHONE_NUMBER,
            to=recipient
        )
        
        message_sid = message.sid
        logger.info(f"SMS sent successfully with SID: {message_sid}")
        return message_sid
        
    except Exception as e:
        logger.error(f"Failed to send SMS: {str(e)}", exc_info=True)
        raise

def parse_inbound_sms(form_data: Dict) -> Dict[str, str]:
    """
    Parse incoming SMS webhook data from Twilio.
    
    Args:
        form_data: Twilio webhook form data
        
    Returns:
        Dict with 'body', 'from_number', 'twilio_sid'
    """
    body = form_data.get('Body', '').strip()
    from_number = form_data.get('From', '').strip()
    twilio_sid = form_data.get('MessageSid', '').strip()
    
    if not body:
        logger.warning("Inbound SMS has empty body")
    if not from_number:
        logger.warning("Inbound SMS missing 'From' field")
    if not twilio_sid:
        logger.warning("Inbound SMS missing 'MessageSid' field")
    
    return {
        'body': body,
        'from_number': from_number,
        'twilio_sid': twilio_sid
    }
