# Health Tracker v1 - Technical Architecture

## Overview
SMS-based conversational health tracker for chronic illness management. Local-first architecture with SQLite storage, FastAPI webhooks, and GPT-4o-mini via OpenRouter for natural language processing.

## 1. High-Level Data Flow

### Inbound SMS Flow
```
Twilio Webhook → FastAPI (/webhook/sms) → Database (inbound) → LLM Agent → SMS Response → Database (outbound)
```

1. **Webhook Reception**: Twilio POSTs to `/webhook/sms` with `Body`, `From`, `MessageSid`
2. **Message Storage**: `insert_message(db_path, 'inbound', body, sid)` 
3. **Context Retrieval**: `get_recent_messages(db_path, limit=5, hours=24)` 
4. **LLM Processing**: `generate_response(history)` → GPT-4o-mini via OpenRouter
5. **Response Dispatch**: `send_sms(response_text)` → Twilio API
6. **Response Logging**: `insert_message(db_path, 'outbound', response_text, sid)`

### Scheduled Check-in Flow
```
APScheduler (Cron) → send_scheduled_message(template) → SMS → Database (outbound)
```

1. **Scheduler Trigger**: APScheduler fires at configured times (10:00, 20:00 EST)
2. **Template Retrieval**: `get_active_schedules(db_path)` 
3. **Message Dispatch**: `send_sms(message_template)` 
4. **Delivery Logging**: `insert_message(db_path, 'outbound', message_template, sid)`

### Application Lifecycle
```
Startup: init_db() → seed_default_schedules() → create_scheduler() → schedule_check_ins()
Runtime: Webhook handling + Scheduled jobs
Shutdown: scheduler.shutdown()
```

## 2. Key Class/Function Signatures

### Configuration Layer (`src/config.py`)
```python
@dataclass
class Settings:
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str
    USER_PHONE_NUMBER: str
    OPENROUTER_API_KEY: str
    OPENROUTER_BASE_URL: str
    LLM_MODEL: str
    TIMEZONE: str
    DATABASE_PATH: str
    LOG_LEVEL: str
    
    @classmethod
    def load() -> 'Settings'  # Loads from .env, validates required vars

def get_settings() -> Settings  # Singleton access to settings
```

### Database Layer (`src/database.py`)
```python
def init_db(db_path: str) -> None  # Creates messages/schedules tables
def insert_message(db_path: str, direction: str, body: str, twilio_sid: str | None = None) -> int
def get_recent_messages(db_path: str, limit: int = 5, hours: int = 24) -> list[dict]
def get_active_schedules(db_path: str) -> list[dict]
def seed_default_schedules(db_path: str) -> None  # Inserts 10:00/20:00 defaults
```

### Agent Layer (`src/agent.py`)
```python
SYSTEM_PROMPT = """You are Luigi..."""  # Hardcoded personality

def prepare_conversation_history(conversation_history: list[dict]) -> list[dict]
    # Returns lesser of: last 24h messages OR last 5 messages (ADR decision)

def build_messages(conversation_history: list[dict]) -> list[dict]
    # Converts to OpenAI format: system + user/assistant messages

def generate_response(conversation_history: list[dict]) -> str
    # Calls GPT-4o-mini via OpenRouter, returns fallback on error
```

### SMS Layer (`src/sms.py`)
```python
def get_twilio_client(config: Settings) -> Client
def send_sms(body: str) -> str  # Returns Twilio SID, raises on failure
def parse_inbound_sms(form_data: dict) -> dict[str, str]  # Extracts Body, From, MessageSid
```

### Scheduler Layer (`src/scheduler.py`)
```python
def create_scheduler() -> AsyncIOScheduler  # Timezone-aware scheduler
def schedule_check_ins(scheduler: AsyncIOScheduler) -> None  # Reads from schedules table
async def send_scheduled_message(message_template: str) -> None  # Async job handler
```

### Application Layer (`src/main.py`)
```python
@asynccontextmanager
async def lifespan(app: FastAPI)  # Startup/shutdown lifecycle

app = FastAPI(lifespan=lifespan)

@app.get("/health") -> dict  # Health check endpoint
@app.post("/webhook/sms") -> Response  # Twilio webhook handler
```

## 3. Database Schema

### `messages` Table
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    body TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    twilio_sid TEXT
);

CREATE INDEX idx_messages_timestamp ON messages(timestamp);
```

**Purpose**: Stores all SMS conversation history  
**Volume**: ~dozens of messages/day (chronic illness tracking)  
**Access Patterns**: Recent message retrieval (time-windowed + limited), sequential insertion

### `schedules` Table
```sql
CREATE TABLE schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour INTEGER NOT NULL CHECK (hour >= 0 AND hour <= 23),
    minute INTEGER NOT NULL CHECK (minute >= 0 AND minute <= 59),
    message_template TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE
);
```

**Purpose**: Configurable scheduled check-ins  
**Default Data**: (10, 0, "Good morning...", TRUE), (20, 0, "Evening check-in...", TRUE)  
**Access Patterns**: Read-all-active at startup, infrequent updates

## 4. Current External Dependencies

### Core Runtime (`requirements.txt`)
```
fastapi>=0.109.0        # Async web framework for Twilio webhooks
uvicorn>=0.27.0         # ASGI server
openai>=1.10.0          # OpenRouter API client (GPT-4o-mini)
twilio>=8.10.0          # SMS send/receive
apscheduler>=3.10.0     # In-process cron scheduling
python-dotenv>=1.0.0    # Environment variable loading
httpx>=0.26.0           # HTTP client (transitive from openai)
```

### Testing
```
pytest>=8.0.0           # Test framework
pytest-asyncio>=0.23.0  # Async test support
```

### Development Tools
- **ngrok**: Local tunnel for Twilio webhooks (development only)
- **zoneinfo**: Timezone handling (Python 3.12 standard library)

### External Services
1. **Twilio**: SMS transport (inbound webhooks + outbound API)
2. **OpenRouter**: LLM gateway to GPT-4o-mini
3. **ngrok**: Development tunneling (not required for production)

## Architecture Constraints & Decisions

### ADR-Compliant Implementation
- **LLM Context**: Last 24h OR last 5 messages (whichever is less)
- **Error Handling**: Fallback SMS on LLM failure, exceptions logged
- **Scheduler**: In-process APScheduler (no system cron)
- **Database**: SQLite single-file (portable, no server)
- **Configuration**: Environment variables only, no hardcoded secrets

### Async Patterns
- **Webhook Handler**: Synchronous (FastAPI handles async)
- **Scheduled Jobs**: Async (`send_scheduled_message` uses `asyncio.to_thread`)
- **SMS Sending**: Thread-pool execution for blocking Twilio calls

### Error Resilience
1. **LLM Failures**: Returns "The LLM call is failing, I'll try again soon."
2. **Twilio Failures**: Exceptions raised, logged with `exc_info=True`
3. **Database Issues**: SQLite file operations wrapped in try/except
4. **Scheduler Failures**: Fallback message attempt on scheduled job failure

## Current State & Next Feature Planning

### ✅ Completed v1 Implementation
- All 25 tests passing (database, agent, SMS layers)
- Full data flow: inbound → LLM → outbound
- Scheduled check-ins (10:00 AM, 8:00 PM EST)
- Local-first SQLite storage
- Production-ready error handling

### 🔄 Runtime Characteristics
- **Memory**: Lightweight (SQLite in-memory cache, small message history)
- **CPU**: LLM calls dominant (~400ms latency per ADR)
- **I/O**: SMS API calls (Twilio), LLM API calls (OpenRouter)
- **Storage**: SQLite file grows with conversation history

### 📋 Next Feature Considerations
1. **Structured Data Extraction**: `health_events` table for symptoms/medications
2. **Natural Language Scheduling**: LLM intent → `schedules` table insertion  
3. **Twilio Signature Validation**: Production security hardening
4. **Web Dashboard**: Read-only conversation history view
5. **Mobile App**: Bundle SQLite + agent into React Native/Flutter

### Technical Debt Notes
- No Twilio webhook signature validation (ADR v4 backlog)
- Hardcoded system prompt (version-controlled, not runtime-editable)
- Single-user design (Shanelle's phone number configured in `.env`)
- No message retry logic (fail-fast on Twilio/OpenRouter errors)
