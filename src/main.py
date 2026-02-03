import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, Response
from src.config import get_settings
from src.database import init_db, seed_default_schedules, insert_message, get_recent_messages
from src.scheduler import create_scheduler, schedule_check_ins
from src.agent import generate_response
from src.sms import send_sms

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler for startup/shutdown."""
    global _scheduler
    
    # Startup
    config = get_settings()
    
    # Initialize database
    init_db(config.DATABASE_PATH)
    logger.info("Database initialized")
    
    # Seed default schedules if empty
    seed_default_schedules(config.DATABASE_PATH)
    logger.info("Default schedules seeded")
    
    # Create and start scheduler
    _scheduler = create_scheduler()
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

@app.post("/webhook/sms")
async def inbound_sms(
    Body: str = Form(...),
    From: str = Form(...),
    MessageSid: str = Form(...)
) -> Response:
    """
    Handle inbound SMS from Twilio webhook.
    
    Args:
        Body: SMS message content
        From: Sender phone number
        MessageSid: Twilio message SID
        
    Returns:
        Empty TwiML response
    """
    config = get_settings()
    
    # Log receipt
    logger.info(f"Received inbound SMS from {From}: {Body}")
    
    try:
        # 1. Store inbound message
        insert_message(config.DATABASE_PATH, 'inbound', Body, MessageSid)
        
        # 2. Get conversation history
        history = get_recent_messages(config.DATABASE_PATH, limit=5, hours=24)
        
        # 3. Generate response using LLM
        response_text = generate_response(history)
        
        # 4. Send response SMS (non-blocking)
        sid = await asyncio.to_thread(send_sms, response_text)
        
        # 5. Store outbound message
        insert_message(config.DATABASE_PATH, 'outbound', response_text, sid)
        
        logger.info(f"Sent response with SID: {sid}")
        
    except Exception as e:
        logger.error(f"Error processing inbound SMS: {e}", exc_info=True)
        # Still return empty response so Twilio doesn't retry
    
    # Return empty TwiML response
    return Response(content="<Response></Response>", media_type="application/xml")
