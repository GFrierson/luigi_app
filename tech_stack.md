# Health Tracker - Technical Stack

**Version:** 1.0  
**Last Updated:** February 2, 2026

---

## Runtime Environment

|Component|Specification|
|---|---|
|Language|Python 3.12+|
|Target OS (Dev)|macOS Sonoma (Apple Silicon / ARM64)|
|Target OS (Prod)|Raspberry Pi OS / Debian (ARM64)|
|Architecture|Must run identically on both environments|

---

## Core Dependencies

### Web Framework

- **FastAPI** — Async webhook receiver for Twilio callbacks
- **Uvicorn** — ASGI server to run FastAPI

### Database

- **SQLite 3** — Single-file relational database (bundled with Python)
- No ORM in v1; raw SQL via Python's `sqlite3` standard library

### Scheduler

- **APScheduler** — In-process job scheduling for timed check-ins
- Timezone handling via `pytz` or `zoneinfo` (standard library in 3.12)

### LLM Integration
	
- **OpenAI Python SDK** (`openai`) — Used as client for OpenRouter (compatible API)
- Router: OpenRouter (https://openrouter.ai)
- Model: `openai/gpt-4o-mini` (OpenRouter model string)
- No LangChain or orchestration framework in v1

### SMS Transport

- **Twilio Python SDK** (`twilio`) — Send outbound SMS, validate inbound webhooks

### Local Development

- **ngrok** — Expose local FastAPI server to public internet for Twilio webhooks
- Not a Python dependency; installed separately

---

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
├── requirements.txt         # Pinned dependencies
├── README.md
└── todo.md                  # Aider execution plan
```

---

## Environment Variables

All secrets and configuration loaded from environment variables. Never hardcoded.

```bash
# .env.example

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

---

## Dependencies (requirements.txt)

```
fastapi>=0.109.0
uvicorn>=0.27.0
openai>=1.10.0
twilio>=8.10.0
apscheduler>=3.10.0
python-dotenv>=1.0.0
httpx>=0.26.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

No version caps on patch level; pin minor versions for stability.

---

## Database Schema

### `messages` table

Stores all SMS traffic (inbound and outbound).

```sql
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    body TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    twilio_sid TEXT
);

CREATE INDEX idx_messages_timestamp ON messages(timestamp);
```

### `schedules` table

Stores scheduled prompt times.

```sql
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour INTEGER NOT NULL CHECK (hour >= 0 AND hour <= 23),
    minute INTEGER NOT NULL CHECK (minute >= 0 AND minute <= 59),
    message_template TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE
);
```

---

## API Endpoints

|Method|Path|Purpose|
|---|---|---|
|POST|`/webhook/sms`|Receive inbound SMS from Twilio|
|GET|`/health`|Health check (for monitoring)|

No authentication on `/webhook/sms` in v1; Twilio request validation via signature header.

---

## Logging Standard

All modules use Python's `logging` library. No print statements.

```python
import logging
logger = logging.getLogger(__name__)
```

Log levels:

- `INFO` — Message received, message sent, scheduled job fired
- `DEBUG` — LLM prompt/response, database queries
- `ERROR` — API failures, exceptions (with `exc_info=True`)

---

## Testing Strategy

- Framework: `pytest` + `pytest-asyncio`
- Each module in `src/` has a corresponding test file in `tests/`
- Tests use SQLite in-memory database (`:memory:`)
- Twilio and OpenAI calls mocked; no live API calls in tests

---

## Deployment Notes

### Local Development (macOS)

```bash
# Terminal 1: Run the app
uvicorn src.main:app --reload --port 8000

# Terminal 2: Expose via ngrok
ngrok http 8000
```

Configure Twilio webhook URL to ngrok's HTTPS URL + `/webhook/sms`.

### Production (Raspberry Pi)

- Clone repo, create `.env`, install dependencies in venv
- Run via `systemd` service for auto-restart
- No ngrok; configure router port forwarding or use Cloudflare Tunnel
- SQLite file stored in `/home/pi/health-tracker/data/`

---
## Implementation Note

The OpenAI Python SDK works with OpenRouter by overriding the base URL:

python

```python
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url=os.getenv("OPENROUTER_BASE_URL"),
)
```

## Future Considerations (Not in v1)

- **Structured extraction:** Add `health_events` table for parsed symptoms/medications
- **Natural language scheduling:** LLM extracts intent → inserts into `schedules`
- **Web dashboard:** Read-only view of conversation history and trends
- **Local-first mobile app:** Bundle SQLite + agent core into React Native or Flutter app

---

Copy this into `tech_stack.md` in your project knowledge. When ready, say **"Ready for todo"** and I'll generate the Aider implementation plan.