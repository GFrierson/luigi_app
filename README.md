# Health Tracker v1 - Luigi

A conversational SMS-based health tracker for chronic illness management. This system allows a user (Shanelle) to log symptoms, medications, and general wellbeing through natural conversation via SMS.

## Overview

Luigi is a personal health assistant that:
- Responds to inbound SMS messages with contextual, empathetic replies
- Sends scheduled morning and evening check-in prompts
- Stores all conversation history locally in SQLite for privacy
- Uses GPT-4o-mini via OpenRouter for natural language understanding

## Architecture Decision Record (ADR)

This project follows a local-first architecture with these key decisions:
- **SMS over WhatsApp/App**: Zero setup for the user, just text a number
- **GPT-4o-mini**: Best balance of cost, latency, and conversational quality
- **SQLite**: Portable, requires no server, data stays local
- **FastAPI**: Async-native, modern Python web framework
- **APScheduler**: In-process scheduling simplifies deployment
- **Local-first**: Keeps health data private, no cloud storage

## Project Structure

```
health-tracker/
├── src/
│   ├── __init__.py
│   ├── main.py              # FastAPI app + Uvicorn entrypoint
│   ├── agent.py             # LLM conversation logic
│   ├── database.py          # SQLite connection + queries
│   ├── scheduler.py         # APScheduler setup + jobs
│   ├── sms.py               # Twilio send/receive helpers
│   └── config.py            # Environment variable loading
├── tests/
│   ├── __init__.py
│   ├── test_agent.py
│   ├── test_database.py
│   └── test_sms.py
├── data/
│   └── health_tracker.db    # SQLite database file (gitignored)
├── .env                     # Local environment variables (gitignored)
├── .env.example             # Template for required env vars
├── requirements.txt         # Python dependencies
└── README.md               # This file
```

## Agent Personality

**Name**: Luigi  
**Tone**: Calm, polite, empathetic, concise, straightforward  
**Function**: Helps Shanelle track symptoms, medications, and wellbeing  
**Communication**: Asks clarifying questions when uncertain, keeps responses brief for SMS

Example greeting: "Hello, this is Luigi - your personal health assistant. How are you feeling today Shanelle?"

## Technical Implementation

### Core Components

1. **Configuration** (`src/config.py`)
   - Loads environment variables using python-dotenv
   - Validates required settings on startup
   - Provides global settings access via `get_settings()`

2. **Database** (`src/database.py`)
   - SQLite with two tables: `messages` and `schedules`
   - Auto-seeds default check-ins (10:00 AM and 8:00 PM EST)
   - Manages conversation history and scheduled prompts

3. **Agent** (`src/agent.py`)
   - LLM integration with OpenAI GPT-4o-mini via OpenRouter
   - Conversation context limited to last 24 hours or 5 messages
   - Error handling with graceful fallback messages

4. **SMS** (`src/sms.py`)
   - Twilio integration for sending/receiving SMS
   - Webhook parsing for inbound messages
   - Comprehensive error logging

5. **Scheduler** (`src/scheduler.py`)
   - APScheduler for timed check-ins
   - Timezone-aware scheduling (America/New_York default)
   - Async message sending to avoid blocking

6. **Application** (`src/main.py`)
   - FastAPI web application with lifespan management
   - `/webhook/sms` endpoint for Twilio callbacks
   - `/health` endpoint for monitoring
   - Automatic database initialization and scheduler startup

### Database Schema

```sql
-- All conversations
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    body TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    twilio_sid TEXT
);

-- Scheduled prompts
CREATE TABLE schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour INTEGER NOT NULL CHECK (hour >= 0 AND hour <= 23),
    minute INTEGER NOT NULL CHECK (minute >= 0 AND minute <= 59),
    message_template TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE
);
```

### Default Schedules

- **10:00 AM EST**: "Good morning Shanelle! How are you feeling today?"
- **8:00 PM EST**: "Evening check-in: How was your day? Any symptoms or notes to share?"

## Environment Setup

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Fill in your credentials:
   ```bash
   # Twilio
   TWILIO_ACCOUNT_SID=your_account_sid
   TWILIO_AUTH_TOKEN=your_auth_token
   TWILIO_PHONE_NUMBER=+1234567890

   # Recipient (Shanelle's phone)
   USER_PHONE_NUMBER=+1234567890

   # OpenRouter
   OPENROUTER_API_KEY=your_openrouter_api_key
   OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
   LLM_MODEL=openai/gpt-4o-mini

   # App Config
   TIMEZONE=America/New_York
   DATABASE_PATH=data/health_tracker.db
   LOG_LEVEL=INFO
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Running the Application

### Local Development
```bash
# Start the FastAPI server
uvicorn src.main:app --reload --port 8000

# In another terminal, expose via ngrok for Twilio webhooks
ngrok http 8000
```

Configure Twilio webhook URL to `https://your-ngrok-url.ngrok.io/webhook/sms`

### Production (Raspberry Pi)
```bash
# Clone the repository
git clone https://github.com/your-username/health-tracker.git
cd health-tracker

# Set up environment
cp .env.example .env
# Edit .env with your credentials

# Install dependencies
pip install -r requirements.txt

# Create data directory
mkdir -p data

# Run with uvicorn
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

Configure router port forwarding or use Cloudflare Tunnel for external access.

## Testing

Run the full test suite:
```bash
pytest tests/ -v
```

Test coverage:
- ✅ Database layer: Table creation, message insertion, scheduling
- ✅ Agent layer: LLM conversation building, error handling
- ✅ SMS layer: Twilio client mocking, webhook parsing

All 25 tests currently passing.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check for monitoring |
| POST | `/webhook/sms` | Receive inbound SMS from Twilio |

## Error Handling

- **LLM failures**: Returns "The LLM call is failing, I'll try again soon."
- **Twilio API errors**: Logs exception details and re-raises
- **Database errors**: Logged with full exception information
- **Missing environment variables**: Raises `ValueError` on startup

## Logging

All modules use Python's `logging` library with configurable levels:
- `INFO`: Message received/sent, scheduled jobs, startup/shutdown
- `DEBUG`: LLM prompts/responses, database queries
- `ERROR`: API failures, exceptions (with `exc_info=True`)

## Development Status

✅ **Phase 1**: Project scaffolding  
✅ **Phase 2**: Configuration & database layer  
✅ **Phase 3**: Agent (LLM) layer  
✅ **Phase 4**: SMS layer  
✅ **Phase 5**: Scheduler layer  
✅ **Phase 6**: FastAPI application  
✅ **Phase 7**: Integration verification  

**Current Status**: Complete v1 implementation with all tests passing

## Future Considerations

- **Structured extraction**: Add `health_events` table for parsed symptoms/medications
- **Natural language scheduling**: LLM extracts intent → inserts into `schedules`
- **Web dashboard**: Read-only view of conversation history and trends
- **Mobile app**: Bundle SQLite + agent core into React Native or Flutter app
- **Twilio validation**: Add signature validation for production hardening

## License

This project is for personal health tracking use. Ensure compliance with healthcare data regulations in your jurisdiction.

## Acknowledgments

- Built with FastAPI, SQLite, APScheduler, and OpenAI GPT-4o-mini
- SMS transport via Twilio
- LLM access via OpenRouter
- Designed for local-first privacy and simplicity
```
