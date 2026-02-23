import asyncio
import logging
import os

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from src.config import get_settings
from src.database import (
    init_db, seed_default_schedules, insert_message, get_recent_messages,
    get_user_db_path, get_user_name, set_user_name, deactivate_all_schedules,
)
from src.agent import generate_response, extract_name_from_message

logger = logging.getLogger(__name__)


async def send_message(chat_id: int, text: str) -> int:
    """
    Send a message to a Telegram chat.

    Returns:
        The message_id of the sent message

    Raises:
        Exception: If the Telegram API call fails
    """
    config = get_settings()
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

    try:
        message = await bot.send_message(chat_id=chat_id, text=text)
        message_id = message.message_id
        logger.info(f"Telegram message sent successfully with ID: {message_id}")
        return message_id

    except Exception as e:
        logger.error(f"Failed to send Telegram message: {str(e)}", exc_info=True)
        raise


async def start_command(update: Update, context) -> None:
    """Handle the /start command — log chat_id and send welcome message."""
    chat_id = update.message.chat_id
    logger.info(f"New user started bot: chat_id={chat_id}")
    await update.message.reply_text(
        "Hi! I'm Luigi, your health tracker. Send me a message to get started!"
    )


async def handle_message(chat_id: int, text: str, message_id: int, scheduler=None) -> str:
    """
    Process an incoming message and generate a response.

    This is the core message handling logic: per-user DB init, inbound logging,
    stop-command handling, name extraction, LLM response, and outbound logging.

    Args:
        chat_id: Telegram chat ID
        text: Message text from user
        message_id: Telegram message ID
        scheduler: Optional AsyncIOScheduler for managing scheduled jobs

    Returns:
        The response text sent to the user
    """
    config = get_settings()

    db_path = get_user_db_path(config.DATABASE_DIR, chat_id)
    is_new_user = not os.path.exists(db_path)

    if is_new_user:
        init_db(db_path)
        seed_default_schedules(db_path)
        logger.info(f"Created new database for user {chat_id}")
        if scheduler:
            from src.scheduler import schedule_check_ins
            schedule_check_ins(scheduler)

    insert_message(db_path, 'inbound', text, message_id)

    if text.strip().lower() == "stop":
        await asyncio.to_thread(deactivate_all_schedules, db_path)

        if scheduler:
            jobs_to_remove = [
                job.id for job in scheduler.get_jobs()
                if job.id.startswith(f"checkin_{chat_id}_")
            ]
            for job_id in jobs_to_remove:
                scheduler.remove_job(job_id)
            logger.info(f"Removed {len(jobs_to_remove)} jobs for user {chat_id}")

        response_text = "All scheduled check-ins have been stopped. You can restart them by sending any message."
        sent_id = await send_message(chat_id, response_text)
        insert_message(db_path, 'outbound', response_text, sent_id)
        logger.info(f"User {chat_id} requested to stop schedules")

    else:
        user_name = get_user_name(db_path)

        if not user_name:
            extracted_name = extract_name_from_message(text)
            if extracted_name:
                set_user_name(db_path, extracted_name)
                user_name = extracted_name
                logger.info(f"Extracted and saved user name: {user_name}")

        history = get_recent_messages(db_path, limit=5, hours=24)
        response_text = generate_response(history, user_name)

        sent_id = await send_message(chat_id, response_text)
        insert_message(db_path, 'outbound', response_text, sent_id)
        logger.info(f"Sent response to chat {chat_id} with message ID: {sent_id}")

    return response_text


async def _on_message(update: Update, context) -> None:
    """python-telegram-bot handler: extracts fields and delegates to handle_message."""
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    text = update.message.text.strip()
    message_id = update.message.message_id

    if not text:
        return

    logger.info(f"Received message from chat {chat_id}: {text}")

    scheduler = context.bot_data.get("scheduler")
    try:
        await handle_message(chat_id, text, message_id, scheduler=scheduler)
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)


def create_application(token: str) -> Application:
    """Build and return an Application with all message handlers registered."""
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    return application
