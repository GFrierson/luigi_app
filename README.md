# Health Tracker v1.1 - Luigi

A conversational Telegram-based health tracker for chronic illness management. Users log symptoms, medications, and general wellbeing through natural conversation via Telegram. Supports multiple users, each with an isolated database.

## Overview

Luigi is a personal health assistant that:
- Responds to inbound Telegram messages with contextual, empathetic replies
- Sends scheduled morning and evening check-in prompts (per user)
- Stores all conversation history locally in per-user SQLite databases for privacy
- Uses GPT-4o-mini via OpenRouter for natural language understanding
- Runs in polling mode — no HTTP server or public webhook required

## Architecture Decision Record (ADR)

This project follows a local-first architecture with these key decisions:
- **Telegram over SMS**: Rich bot API, no per-message cost, polling removes need for a public webhook
- **Polling over webhook**: Simplifies deployment — no ingress, no ngrok, no port forwarding
- **GPT-4o-mini**: Best balance of cost, latency, and conversational quality
- **SQLite (per-user)**: Each user gets an isolated `data/{chat_id}.db`; portable, no server required
- **APScheduler**: In-process scheduling simplifies deployment
- **Hetzner VPS**: Low-cost always-on Linux host; managed via systemd
- **Local-first**: Keeps health data private, no cloud storage

## Project Structure

```
luigi_app/
├── src/
│   ├── __init__.py
│   ├── main.py              # Polling entrypoint — builds Application, starts scheduler
│   ├── agent.py             # LLM conversation logic + name extraction
│   ├── database.py          # SQLite connection + per-user queries
│   ├── scheduler.py         # APScheduler setup + per-user check-in jobs
│   ├── telegram_handler.py  # Telegram send/receive helpers + message dispatch
│   └── config.py            # Environment variable loading
├── tests/
│   ├── __init__.py
│   ├── test_agent.py
│   ├── test_database.py
│   └── test_telegram.py
├── data/                    # Per-user SQLite databases (gitignored)
│   └── {chat_id}.db         # One file per Telegram user
├── deploy/
│   └── luigi.service        # systemd unit file for VPS deployment
├── .env                     # Local environment variables (gitignored)
├── .env.example             # Template for required env vars
├── requirements.txt         # Python dependencies
└── README.md               # This file
```

## Agent Personality

**Name**: Luigi
**Tone**: Calm, polite, empathetic, concise, straightforward
**Function**: Records symptoms, medications, and wellbeing — does not give health advice
**Communication**: Asks for the user's name on first contact; uses it occasionally thereafter

Example first message: "Hi, I'm Luigi — your health tracking assistant. What's your name?"

Example follow-up: "Got it — migraine starting around 3pm, ibuprofen taken. Let me know how you're feeling later."

## Technical Implementation

### Core Components

1. **Configuration** (`src/config.py`)
   - Loads environment variables using python-dotenv
   - Validates all required settings on startup; raises `ValueError` if any are missing
   - Provides global settings access via `get_settings()`

2. **Database** (`src/database.py`)
   - Per-user SQLite databases at `data/{chat_id}.db`
   - Three tables per database: `messages`, `schedules`, `user_profile`
   - Auto-seeds default check-ins (10:00 AM and 8:00 PM) on first user contact
   - `get_user_db_path()` and `get_all_user_databases()` for multi-user routing

3. **Agent** (`src/agent.py`)
   - LLM integration with OpenAI GPT-4o-mini via OpenRouter
   - Conversation context limited to the lesser of: last 24 hours or last 5 messages
   - `extract_name_from_message()` detects user name from natural language
   - Error handling with graceful fallback messages

4. **Telegram Handler** (`src/telegram_handler.py`)
   - `send_message()`: sends outbound messages via the Bot API
   - `handle_message()`: core dispatch — per-user DB init, inbound logging, stop-command, name extraction, LLM response, outbound logging
   - `_on_message()`: python-telegram-bot handler that delegates to `handle_message()`
   - `start_command()`: handles `/start`, logs new chat_id

5. **Scheduler** (`src/scheduler.py`)
   - APScheduler `AsyncIOScheduler` for timed check-ins
   - Timezone-aware scheduling (configured via `TIMEZONE` env var)
   - Iterates all user databases to register per-user cron jobs
   - Scheduled messages use the LLM for contextual check-ins; falls back to static template on failure

6. **Application** (`src/main.py`)
   - Builds the `python-telegram-bot` Application with polling
   - Starts/stops the scheduler via `post_init` / `post_shutdown` hooks
   - Registers `/start` command handler and free-text message handler

### Database Schema

Each user gets their own `data/{chat_id}.db` file with the following tables:

```sql
-- Conversation history
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    body TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    message_id INTEGER
);

-- Scheduled check-ins
CREATE TABLE schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour INTEGER NOT NULL CHECK (hour >= 0 AND hour <= 23),
    minute INTEGER NOT NULL CHECK (minute >= 0 AND minute <= 59),
    message_template TEXT NOT NULL,
    active BOOLEAN DEFAULT TRUE
);

-- User profile (name stored here after extraction)
CREATE TABLE user_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT
);
```

### Default Schedules

Per-user defaults seeded on first contact:
- **10:00 AM**: "Good morning! How are you feeling today?"
- **8:00 PM**: "Evening check-in: How was your day? Any symptoms or notes to share?"

Users can opt out by sending `stop`.

## Environment Setup

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Fill in your credentials:
   ```bash
   # Telegram
   TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather

   # OpenRouter
   OPENROUTER_API_KEY=your_openrouter_api_key
   OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
   LLM_MODEL=openai/gpt-4o-mini

   # App Config
   TIMEZONE=America/New_York
   DATABASE_DIR=data/
   LOG_LEVEL=INFO
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Running the Application

### Local Development
```bash
python -m src.main
```

No ngrok or port forwarding needed — Luigi uses Telegram's polling API.

### Production (Hetzner VPS via systemd)

1. Clone the repository and configure the environment:
   ```bash
   git clone https://github.com/your-username/luigi_app.git /opt/luigi_app
   cd /opt/luigi_app
   cp .env.example .env
   # Edit .env with your credentials
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. Install and enable the systemd service:
   ```bash
   cp deploy/luigi.service /etc/systemd/system/luigi.service
   systemctl daemon-reload
   systemctl enable luigi
   systemctl start luigi
   ```

4. Check logs:
   ```bash
   journalctl -u luigi -f
   ```

## Testing

Run the full test suite:
```bash
pytest tests/ -v
```

Test coverage:
- ✅ Database layer: table creation, per-user DB routing, message insertion, scheduling, name get/set
- ✅ Agent layer: LLM conversation building, name extraction, fallback handling
- ✅ Telegram layer: message handling, stop command, new user init, handler wiring

All 57 tests currently passing.

## Error Handling

- **LLM failures**: Returns "The LLM call is failing, I'll try again soon."
- **Scheduled message failures**: Falls back to static template; logs error if fallback also fails
- **Telegram API errors**: Logs exception details and re-raises
- **Database errors**: Logged with full exception information
- **Missing environment variables**: Raises `ValueError` on startup

## Logging

All modules use Python's `logging` library with configurable levels:
- `INFO`: Message received/sent, new user registration, scheduler jobs, startup/shutdown
- `DEBUG`: LLM prompts/responses, database queries, schedule routing
- `ERROR`: API failures, exceptions (with `exc_info=True`)

## Development Status

✅ **Phase 1**: Project scaffolding
✅ **Phase 2**: Configuration & database layer
✅ **Phase 3**: Agent (LLM) layer
✅ **Phase 4**: Telegram handler (send/receive, multi-user dispatch)
✅ **Phase 5**: Scheduler layer (per-user cron jobs)
✅ **Phase 6**: Polling entrypoint
✅ **Phase 7**: Integration verification + VPS deployment

**Current Status**: Complete v1.1 implementation — Telegram polling, multi-user, all 57 tests passing

## Future Considerations

- **Structured extraction**: Add `health_events` table for parsed symptoms/medications
- **Natural language scheduling**: LLM extracts intent → inserts into `schedules`
- **Web dashboard**: Read-only view of conversation history and trends per user
- **Admin commands**: Bot owner commands to inspect user count, DB sizes, or force check-ins
- **Webhook mode**: Optional switch to webhook deployment if VPS gets a stable domain + TLS

## License

This project is for personal health tracking use. Ensure compliance with healthcare data regulations in your jurisdiction.

## Acknowledgments

- Built with python-telegram-bot, SQLite, APScheduler, and OpenAI GPT-4o-mini
- Telegram transport via python-telegram-bot (polling mode)
- LLM access via OpenRouter
- Designed for local-first privacy and simplicity
