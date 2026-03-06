import logging
from datetime import date
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from src.config import get_settings
from src.database import (
    init_db, get_active_schedules, get_all_user_databases, insert_message, get_recent_messages,
    get_display_name, get_all_schedules, get_all_active_medication_groups,
    get_medications_by_group,
)
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

def add_user_job(scheduler: AsyncIOScheduler, chat_id: int, db_path: str, hour: int, minute: int, message_template: str) -> None:
    """Register a single check-in job for a user."""
    config = get_settings()
    trigger = CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(config.TIMEZONE))
    scheduler.add_job(
        send_scheduled_message,
        trigger,
        args=[message_template, chat_id, db_path],
        id=f"checkin_{chat_id}_{hour:02d}_{minute:02d}",
        name=f"Check-in for {chat_id} at {hour:02d}:{minute:02d}",
        replace_existing=True
    )
    logger.info(f"Added job for user {chat_id} at {hour:02d}:{minute:02d}")


def remove_user_job(scheduler: AsyncIOScheduler, chat_id: int, hour: int, minute: int) -> bool:
    """Remove a specific check-in job for a user. Returns True if removed."""
    job_id = f"checkin_{chat_id}_{hour:02d}_{minute:02d}"
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Removed job {job_id}")
        return True
    except Exception:
        logger.debug(f"Job {job_id} not found, nothing to remove")
        return False


def remove_all_user_jobs(scheduler: AsyncIOScheduler, chat_id: int) -> int:
    """Remove all check-in jobs for a user. Returns count removed."""
    jobs_to_remove = [
        job.id for job in scheduler.get_jobs()
        if job.id.startswith(f"checkin_{chat_id}_")
    ]
    for job_id in jobs_to_remove:
        scheduler.remove_job(job_id)
    logger.info(f"Removed {len(jobs_to_remove)} jobs for user {chat_id}")
    return len(jobs_to_remove)


def schedule_user_check_ins(scheduler: AsyncIOScheduler, chat_id: int, db_path: str) -> int:
    """Register all active check-in jobs for a single user. Returns count registered."""
    schedules = get_active_schedules(db_path)
    for schedule in schedules:
        add_user_job(scheduler, chat_id, db_path, schedule['hour'], schedule['minute'], schedule['message_template'])
    logger.info(f"Registered {len(schedules)} jobs for user {chat_id}")
    return len(schedules)


def register_medication_reminder_job(
    scheduler: AsyncIOScheduler,
    chat_id: int,
    db_path: str,
    group_id: int,
    group_name: str,
    hour: int,
    minute: int,
    interval_days: int = 1,
    start_date: str | None = None,
) -> None:
    """Register a medication reminder cron job for a single group."""
    config = get_settings()
    job_id = f"med_reminder_{chat_id}_{group_id}"
    trigger = CronTrigger(
        hour=hour,
        minute=minute,
        timezone=ZoneInfo(config.TIMEZONE),
    )
    scheduler.add_job(
        send_medication_reminder,
        trigger,
        args=[group_name, chat_id, db_path, group_id, interval_days, start_date],
        id=job_id,
        name=f"Med reminder for {chat_id}: {group_name}",
        replace_existing=True,
    )
    logger.info(f"Registered med reminder job '{job_id}' for '{group_name}'")


async def send_medication_reminder(group_name: str, chat_id: int, db_path: str, group_id: int, interval_days: int, start_date: str | None) -> None:
    """
    Send a medication reminder for a group.
    Checks the interval: only fires if (today - start_date) % interval_days == 0.
    """
    try:
        # Interval check
        if start_date and interval_days > 1:
            try:
                start = date.fromisoformat(start_date)
                delta = (date.today() - start).days
                if delta % interval_days != 0:
                    logger.debug(f"Skipping med reminder for '{group_name}' (day {delta}, interval {interval_days})")
                    return
            except ValueError:
                logger.warning(f"Invalid start_date '{start_date}' for group '{group_name}', sending anyway")

        meds = get_medications_by_group(db_path, group_id)
        med_list = ", ".join(m['name'] for m in meds) if meds else "your medications"
        message = f"Checking in — did you take your {group_name} ({med_list})?"

        msg_id = await send_message(chat_id, message)
        insert_message(db_path, 'outbound', message, msg_id)
        logger.info(f"Sent medication reminder for '{group_name}' to chat {chat_id}")

    except Exception:
        logger.error(f"Failed to send medication reminder for '{group_name}' to {chat_id}", exc_info=True)


def schedule_check_ins(scheduler: AsyncIOScheduler) -> None:
    """
    Schedule check-ins for ALL users.

    Args:
        scheduler: AsyncIOScheduler instance
    """
    config = get_settings()
    user_databases = get_all_user_databases(config.DATABASE_DIR)

    total_jobs = 0
    total_med_jobs = 0
    for chat_id, db_path in user_databases:
        init_db(db_path)  # ensure medication tables exist for pre-migration DBs
        total_jobs += schedule_user_check_ins(scheduler, chat_id, db_path)

        # Register medication reminder jobs for each active group
        groups = get_all_active_medication_groups(db_path)
        for group in groups:
            if group.get('schedule_hour') is None or group.get('schedule_minute') is None:
                continue
            register_medication_reminder_job(
                scheduler, chat_id, db_path, group['id'], group['name'],
                group['schedule_hour'], group['schedule_minute'],
                group.get('interval_days', 1), group.get('start_date'),
            )
            total_med_jobs += 1

    logger.info(f"Total scheduled check-ins: {total_jobs} for {len(user_databases)} users")
    logger.info(f"Total medication reminder jobs: {total_med_jobs}")

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
        # Include current local time so the LLM can generate a time-appropriate greeting
        from datetime import datetime
        config = get_settings()
        now = datetime.now(ZoneInfo(config.TIMEZONE))
        time_str = now.strftime("%I:%M %p")
        checkin_prompt = [{"direction": "inbound", "body": f"This is a scheduled check-in at {time_str}. Generate a brief, contextual greeting."}]

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
