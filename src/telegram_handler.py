import logging
from telegram import Bot
from src.config import get_settings

logger = logging.getLogger(__name__)


def get_bot() -> Bot:
    """Create and return a Telegram bot instance."""
    config = get_settings()
    return Bot(token=config.TELEGRAM_BOT_TOKEN)


async def send_message(chat_id: int, text: str) -> int:
    """
    Send a message to a Telegram chat.

    Args:
        chat_id: The Telegram chat ID to send the message to
        text: The message content to send

    Returns:
        The message_id of the sent message

    Raises:
        Exception: If Telegram API call fails
    """
    bot = get_bot()

    try:
        message = await bot.send_message(chat_id=chat_id, text=text)
        message_id = message.message_id
        logger.info(f"Telegram message sent successfully with ID: {message_id}")
        return message_id

    except Exception as e:
        logger.error(f"Failed to send Telegram message: {str(e)}", exc_info=True)
        raise


def parse_update(update_data: dict) -> dict:
    """
    Parse incoming Telegram update webhook data.

    Args:
        update_data: Telegram webhook update JSON data

    Returns:
        Dict with 'chat_id', 'text', 'message_id'
    """
    message = update_data.get('message', {})
    chat = message.get('chat', {})

    chat_id = chat.get('id')
    text = message.get('text', '').strip()
    message_id = message.get('message_id')

    if not chat_id:
        logger.warning("Telegram update missing chat_id")
    if not text:
        logger.warning("Telegram update has empty text")
    if not message_id:
        logger.warning("Telegram update missing message_id")

    return {
        'chat_id': chat_id,
        'text': text,
        'message_id': message_id
    }
