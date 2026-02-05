import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, Response, Request
from src.config import get_settings

# Configure logging at module load time
config = get_settings()
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
from src.database import init_db, seed_default_schedules, insert_message, get_recent_messages, deactivate_all_schedules
from src.scheduler import create_scheduler, schedule_check_ins
from src.agent import generate_response
from src.sms import send_sms, parse_inbound_sms

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

@app.post("/test/webhook")
async def test_webhook(request: Request):
    """Test endpoint to see what data Twilio sends."""
    form_data = await request.form()
    return {
        "headers": dict(request.headers),
        "form_data": dict(form_data),
        "body": await request.body()
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
        logger.info(f"Received form data values: {dict(form_data)}")
        
        # Extract fields using parse_inbound_sms for consistency
        parsed_data = parse_inbound_sms(dict(form_data))
        Body = parsed_data['body']
        From = parsed_data['from_number']
        MessageSid = parsed_data['twilio_sid']
        
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
