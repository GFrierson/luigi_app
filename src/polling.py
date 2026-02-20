"""
Polling mode entry point for local development.

Run with: python -m src.polling

This mode polls Telegram for updates instead of using webhooks,
which is useful for local development without ngrok or a public URL.
"""
import logging
import os
from telegram import Update
from telegram.ext import Application, MessageHandler, filters

from src.config import get_settings
from src.main import handle_message
from src.scheduler import create_scheduler, schedule_check_ins

# Configure logging
config = get_settings()
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler = None


async def post_init(application: Application) -> None:
    """Called after Application.initialize(), within the event loop."""
    global _scheduler
    _scheduler.start()
    logger.info(f"Scheduler started with {len(_scheduler.get_jobs())} jobs")


async def post_shutdown(application: Application) -> None:
    """Called during Application shutdown."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        logger.info("Scheduler stopped")


async def message_handler(update: Update, context) -> None:
    """Handle incoming Telegram messages in polling mode."""
    global _scheduler

    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    text = update.message.text.strip()
    message_id = update.message.message_id

    if not text:
        return

    logger.info(f"Received message from chat {chat_id}: {text}")

    try:
        await handle_message(chat_id, text, message_id, scheduler=_scheduler)
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)


def main():
    """Run the bot in polling mode."""
    global _scheduler

    config = get_settings()

    # Ensure database directory exists
    os.makedirs(config.DATABASE_DIR, exist_ok=True)
    logger.info(f"Database directory: {config.DATABASE_DIR}")

    # Create scheduler (will be started in post_init after event loop is running)
    _scheduler = create_scheduler()
    schedule_check_ins(_scheduler)

    # Build the Application with lifecycle hooks
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Add message handler for text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Starting bot in polling mode...")
    logger.info("Press Ctrl+C to stop")

    # Run polling (scheduler cleanup handled by post_shutdown)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
