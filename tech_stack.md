# Health Tracker - Technical Stack

**Version:** 1.1  
**Last Updated:** February 5, 2026

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

- **FastAPI** — Async webhook receiver for Telegram updates
- **Uvicorn** — ASGI server to run FastAPI

### Database

- **SQLite 3** — Single-file relational database (bundled with Python)
- No ORM in v1; raw SQL via Python's `sqlite3` standard library

### Scheduler

- **APScheduler** — In-process job scheduling for timed check-ins
- Timezone handling via `zoneinfo` (standard library in 3.12)

### LLM Integration

- **OpenAI Python SDK** (`openai`) — Used as client for OpenRouter (compatible API)
- Router: OpenRouter (https://openrouter.ai)
- Model: `openai/gpt-4o-mini` (OpenRouter model string)
- No LangChain or orchestration framework in v1

### Messaging Transport

- **python-telegram-bot** (`python-telegram-bot`) — Telegram Bot API wrapper
- Webhook mode (production) or polling mode (development)

### Local Development

- **ngrok** — Expose local FastAPI server for Telegram webhooks (optional; polling works without it)

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
│   ├── telegram_handler.py  # Telegram send/receive helpers
│   └── config.py            # Environment variable loading
├── tests/
│   ├── __init__.py
│   ├── test_agent.py
│   ├── test_database.py
│   └── test_telegram.py
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

```bash
# .env.example

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_CHAT_ID=shanelles_chat_id

# OpenRouter
OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=openai/gpt-4o-mini

# App Config
TIMEZONE=America/New_York
DATABASE_PATH=data/health_tracker.db
LOG_LEVEL=INFO
```

**Note on TELEGRAM_CHAT_ID:** You'll obtain this after Shanelle messages the bot for the first time. The bot can log incoming chat IDs, and you hardcode hers for outbound scheduled messages.

---

## Dependencies (requirements.txt)

```
fastapi>=0.109.0
uvicorn>=0.27.0
openai>=1.10.0
python-telegram-bot>=21.0
apscheduler>=3.10.0
python-dotenv>=1.0.0
httpx>=0.26.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

---

## Database Schema

No changes from original. Same `messages` and `schedules` tables.

### `messages` table

```sql
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    body TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    telegram_message_id INTEGER
);

CREATE INDEX idx_messages_timestamp ON messages(timestamp);
```

### `schedules` table

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
|POST|`/webhook/telegram`|Receive updates from Telegram|
|GET|`/health`|Health check (for monitoring)|

---

## Telegram Bot Setup (One-Time)

1. Open Telegram, search for **@BotFather**
2. Send `/newbot`
3. Choose a name: `Luigi Health Tracker`
4. Choose a username: `luigi_health_bot` (must end in `bot`)
5. Copy the token → put in `.env` as `TELEGRAM_BOT_TOKEN`
6. Have Shanelle open Telegram, search for your bot, tap **Start**
7. She sends any message; your bot logs her `chat_id`
8. Put her `chat_id` in `.env` as `TELEGRAM_CHAT_ID`

---

## Deployment Notes

### Local Development (macOS) — Polling Mode

```bash
# No ngrok needed; bot polls Telegram servers
uvicorn src.main:app --reload --port 8000
```

### Production (Raspberry Pi) — Webhook Mode

- Set webhook URL via Telegram API: `https://yourdomain.com/webhook/telegram`
- Run behind Cloudflare Tunnel or similar for HTTPS
- Webhook is more efficient than polling for always-on deployment

---

## Future Considerations (Not in v1)

- **Multi-user support:** Currently hardcoded to Shanelle's chat_id
- **Rich messages:** Telegram supports buttons, inline keyboards, images
- **Structured extraction:** Add `health_events` table for parsed symptoms/medications
- **Local-first mobile app:** Bundle SQLite + agent core into React Native or Flutter

---
