import logging
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from src.config import get_settings
from src.database import get_active_schedules, get_all_user_databases, insert_message, get_recent_messages, get_display_name
from src.telegram_handler import send_message
from src.agent import generate_response, format_messages_for_context

logger = logging.getLogger(__name__)

def create_scheduler() -> AsyncIOScheduler:
    """
    Create and return an AsyncIOScheduler instance.

    Returns:
        AsyncIOScheduler instance configured with the app timezone
    """
    config = get_settings()
    timezone = ZoneInfo(config.TIMEZONE)

    scheduler = AsyncIOScheduler(timezone=timezone)
    logger.info(f"Scheduler created with timezone: {config.TIMEZONE}")
    return scheduler

def schedule_check_ins(scheduler: AsyncIOScheduler) -> None:
    """
    Schedule check-ins for ALL users.

    Args:
        scheduler: AsyncIOScheduler instance
    """
    config = get_settings()
    user_databases = get_all_user_databases(config.DATABASE_DIR)

    total_jobs = 0
    for chat_id, db_path in user_databases:
        schedules = get_active_schedules(db_path)

        for schedule in schedules:
            hour = schedule['hour']
            minute = schedule['minute']
            message_template = schedule['message_template']

            trigger = CronTrigger(hour=hour, minute=minute)

            scheduler.add_job(
                send_scheduled_message,
                trigger,
                args=[message_template, chat_id, db_path],
                id=f"checkin_{chat_id}_{hour:02d}_{minute:02d}",
                name=f"Check-in for {chat_id} at {hour:02d}:{minute:02d}",
                replace_existing=True
            )

            logger.info(f"Scheduled check-in for user {chat_id} at {hour:02d}:{minute:02d}")
            total_jobs += 1

    logger.info(f"Total scheduled check-ins: {total_jobs} for {len(user_databases)} users")

async def send_scheduled_message(message_template: str, chat_id: int, db_path: str) -> None:
    """
    Send a scheduled message to a specific user and log it to their database.

    Uses the LLM to generate a contextual check-in message based on recent
    conversation history. Falls back to the static template if LLM fails.

    Args:
        message_template: The fallback message template to send
        chat_id: Telegram chat ID of the recipient
        db_path: Path to the user's database
    """
    try:
        # Get user context for personalized check-in
        user_name = get_display_name(db_path)
        recent_history = get_recent_messages(db_path, limit=5, hours=24)
        recent_context = format_messages_for_context(recent_history)

        # Generate contextual check-in using LLM
        # Use a simple prompt as the "user message" to trigger check-in generation
        checkin_prompt = [{"direction": "inbound", "body": "This is a scheduled check-in. Generate a brief, contextual greeting."}]

        response = generate_response(checkin_prompt, user_name, recent_context)

        # Send the generated message
        msg_id = await send_message(chat_id, response)
        insert_message(db_path, 'outbound', response, msg_id)

        logger.info(f"Sent scheduled check-in to {chat_id}")

    except Exception as e:
        logger.error(f"Failed to send scheduled message to {chat_id}", exc_info=True)
        # Try to send a fallback message (use static template)
        try:
            fallback_id = await send_message(chat_id, message_template)
            insert_message(db_path, 'outbound', message_template, fallback_id)
            logger.info(f"Sent fallback message to {chat_id} with ID: {fallback_id}")
        except Exception as fallback_e:
            logger.error(f"Failed to send fallback message to {chat_id}: {fallback_e}", exc_info=True)
