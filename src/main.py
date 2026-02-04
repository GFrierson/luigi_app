import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, Response, Request
from src.config import get_settings
from src.database import init_db, seed_default_schedules, insert_message, get_recent_messages, deactivate_all_schedules
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
async def inbound_sms(request: Request) -> Response:
    """
    Handle inbound SMS from Twilio webhook.
    
    Returns:
        Empty TwiML response
    """
    config = get_settings()
    
    try:
        # Get form data
        form_data = await request.form()
        logger.info(f"Received form data keys: {list(form_data.keys())}")
        
        # Extract fields
        Body = form_data.get("Body")
        From = form_data.get("From")
        MessageSid = form_data.get("MessageSid")
        
        if not Body or not From or not MessageSid:
            logger.error(f"Missing required fields. Body: {Body}, From: {From}, MessageSid: {MessageSid}")
            # Still return empty TwiML response
            return Response(content="<Response></Response>", media_type="application/xml")
        
        # Log receipt
        logger.info(f"Received inbound SMS from {From}: {Body}")
        
        # 1. Store inbound message
        insert_message(config.DATABASE_PATH, 'inbound', Body, MessageSid)
        
        # Check if the message is "Stop" (case-insensitive)
        if Body.strip().lower() == "stop":
            # Deactivate all schedules in the database
            await asyncio.to_thread(deactivate_all_schedules, config.DATABASE_PATH)
            
            # Remove all jobs from the scheduler
            global _scheduler
            if _scheduler:
                _scheduler.remove_all_jobs()
                logger.info("Removed all jobs from scheduler")
            
            # Send confirmation message
            stop_response = "All scheduled check-ins have been stopped. You can restart them by restarting the application."
            sid = await asyncio.to_thread(send_sms, stop_response)
            
            # Store the outbound message
            insert_message(config.DATABASE_PATH, 'outbound', stop_response, sid)
            
            logger.info(f"User requested to stop schedules. Sent confirmation with SID: {sid}")
            
        else:
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
