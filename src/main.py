import logging
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from src.config import get_settings
from src.scheduler import create_scheduler, schedule_check_ins
from src.telegram_handler import start_command, _on_message

# Configure logging at module load time
config = get_settings()
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """Run Luigi in polling mode."""
    config = get_settings()

    os.makedirs(config.DATABASE_DIR, exist_ok=True)
    logger.info(f"Database directory: {config.DATABASE_DIR}")

    scheduler = create_scheduler()
    schedule_check_ins(scheduler)

    async def post_init(app: Application) -> None:
        scheduler.start()
        logger.info(f"Scheduler started with {len(scheduler.get_jobs())} jobs")

    async def post_shutdown(app: Application) -> None:
        if scheduler.running:
            scheduler.shutdown()
        logger.info("Scheduler stopped")

    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.bot_data["scheduler"] = scheduler

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

    logger.info("Starting Luigi in polling mode...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
