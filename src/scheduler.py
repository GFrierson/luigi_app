import logging
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from src.config import get_settings
from src.database import get_active_schedules, insert_message
from src.sms import send_sms

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
    Schedule all active check-ins from the database.
    
    Args:
        scheduler: AsyncIOScheduler instance
    """
    config = get_settings()
    schedules = get_active_schedules(config.DATABASE_PATH)
    
    for schedule in schedules:
        hour = schedule['hour']
        minute = schedule['minute']
        message_template = schedule['message_template']
        
        trigger = CronTrigger(hour=hour, minute=minute)
        
        scheduler.add_job(
            send_scheduled_message,
            trigger,
            args=[message_template],
            id=f"checkin_{hour:02d}_{minute:02d}",
            name=f"Check-in at {hour:02d}:{minute:02d}",
            replace_existing=True
        )
        
        logger.info(f"Scheduled check-in at {hour:02d}:{minute:02d}: {message_template[:50]}...")
    
    logger.info(f"Total scheduled check-ins: {len(schedules)}")

async def send_scheduled_message(message_template: str) -> None:
    """
    Send a scheduled SMS message and log it to the database.
    
    Args:
        message_template: The message template to send
    """
    config = get_settings()
    
    try:
        # Send the SMS
        sid = send_sms(message_template)
        
        # Log to database
        insert_message(config.DATABASE_PATH, 'outbound', message_template, sid)
        
        logger.info(f"Sent scheduled message with SID: {sid}")
        
    except Exception as e:
        logger.error(f"Failed to send scheduled message: {message_template[:50]}...", exc_info=True)
