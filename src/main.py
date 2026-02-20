import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from src.config import get_settings

# Configure logging at module load time
config = get_settings()
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
from src.database import (
    init_db, seed_default_schedules, insert_message, get_recent_messages,
    deactivate_all_schedules, get_user_db_path, get_all_user_databases,
    get_user_name, set_user_name
)
from src.scheduler import create_scheduler, schedule_check_ins
from src.agent import generate_response, extract_name_from_message
from src.telegram_handler import send_message, parse_update

logger = logging.getLogger(__name__)


async def handle_message(chat_id: int, text: str, message_id: int, scheduler=None) -> str:
    """
    Process an incoming message and generate a response.

    This is the core message handling logic shared by both webhook and polling modes.

    Args:
        chat_id: Telegram chat ID
        text: Message text from user
        message_id: Telegram message ID
        scheduler: Optional APScheduler instance for managing scheduled jobs

    Returns:
        The response text that was sent to the user
    """
    config = get_settings()

    # Get/create user database
    db_path = get_user_db_path(config.DATABASE_DIR, chat_id)
    is_new_user = not os.path.exists(db_path)

    if is_new_user:
        init_db(db_path)
        seed_default_schedules(db_path)
        logger.info(f"Created new database for user {chat_id}")
        # Re-schedule to include new user if scheduler provided
        if scheduler:
            schedule_check_ins(scheduler)

    # Store inbound message
    insert_message(db_path, 'inbound', text, message_id)

    # Check if the message is "stop" (case-insensitive)
    if text.strip().lower() == "stop":
        # Deactivate all schedules in the database
        await asyncio.to_thread(deactivate_all_schedules, db_path)

        # Remove this user's scheduler jobs if scheduler provided
        if scheduler:
            jobs_to_remove = [
                job.id for job in scheduler.get_jobs()
                if job.id.startswith(f"checkin_{chat_id}_")
            ]
            for job_id in jobs_to_remove:
                scheduler.remove_job(job_id)
            logger.info(f"Removed {len(jobs_to_remove)} jobs for user {chat_id}")

        # Send confirmation message
        response_text = "All scheduled check-ins have been stopped. You can restart them by sending any message."
        sent_id = await send_message(chat_id, response_text)

        # Store the outbound message
        insert_message(db_path, 'outbound', response_text, sent_id)

        logger.info(f"User {chat_id} requested to stop schedules")

    else:
        # Get user's name from profile
        user_name = get_user_name(db_path)

        # If we don't know the name, try to extract it from this message
        if not user_name:
            extracted_name = extract_name_from_message(text)
            if extracted_name:
                set_user_name(db_path, extracted_name)
                user_name = extracted_name
                logger.info(f"Extracted and saved user name: {user_name}")

        # Get conversation history
        history = get_recent_messages(db_path, limit=5, hours=24)

        # Generate response using LLM
        response_text = generate_response(history, user_name)

        # Send response
        sent_id = await send_message(chat_id, response_text)

        # Store outbound message
        insert_message(db_path, 'outbound', response_text, sent_id)

        logger.info(f"Sent response to chat {chat_id} with message ID: {sent_id}")

    return response_text

# Global scheduler instance
_scheduler = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler for startup/shutdown."""
    global _scheduler

    # Startup
    config = get_settings()

    # Ensure database directory exists
    os.makedirs(config.DATABASE_DIR, exist_ok=True)
    logger.info(f"Database directory: {config.DATABASE_DIR}")

    # Create and start scheduler
    _scheduler = create_scheduler()

    # Schedule check-ins for all existing users
    schedule_check_ins(_scheduler)
    _scheduler.start()
    logger.info(f"Scheduler started with {len(_scheduler.get_jobs())} jobs")

    logger.info("Health Tracker started successfully")

    yield

    # Shutdown
    if _scheduler:
        _scheduler.shutdown()
        logger.info("Scheduler stopped")

    logger.info("Health Tracker stopped")

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "Health Tracker v1",
        "agent": "Luigi"
    }

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request) -> dict:
    """
    Handle inbound messages from Telegram webhook.

    Returns:
        {"ok": True} to acknowledge receipt
    """
    global _scheduler

    try:
        # Get JSON data from Telegram
        update_data = await request.json()
        logger.info(f"Received Telegram update: {update_data}")

        # Parse the update
        parsed = parse_update(update_data)
        chat_id = parsed['chat_id']
        text = parsed['text']
        message_id = parsed['message_id']

        if not chat_id or not text:
            logger.warning(f"Missing required fields. chat_id: {chat_id}, text: {text}")
            return {"ok": True}

        logger.info(f"Received message from chat {chat_id}: {text}")

        # Handle the message using shared logic
        await handle_message(chat_id, text, message_id, scheduler=_scheduler)

    except Exception as e:
        logger.error(f"Error processing Telegram update: {e}", exc_info=True)
        # Still return ok to prevent Telegram from retrying

    return {"ok": True}
