# Health Tracker v1 - Architecture Decision Record (ADR)

**Status:** Accepted  
**Date:** February 2, 2026

## Context

We are building a conversational health tracker for a user (Shanelle) with chronic illnesses. The core need is a low-friction way for her to log symptoms, medications, and general wellbeing through natural conversation. The system must be simple to use (just text a number), reliable, and preserve privacy by keeping data local.

## Decision

Build an SMS-based conversational agent named "Luigi" that:

1. Responds to inbound texts with contextual, empathetic replies
2. Sends scheduled check-in prompts (morning and evening)
3. Stores all conversation history locally in SQLite

The system will run locally on developer hardware (MacBook Air for testing, Raspberry Pi for production) with Twilio handling SMS transport.

## Technical Details

- **Runtime:** Python 3.12+
- **Web Framework:** FastAPI (webhook receiver)
- **Database:** SQLite (single file, portable)
- **Scheduler:** APScheduler (in-process cron)
- **LLM:** OpenAI GPT-4o-mini via OpenRouter API
- **SMS Provider:** Twilio (inbound webhook + outbound API)
- **Local Tunnel:** ngrok (development only)

**Target Environments:**

- Development: macOS (Apple Silicon)
- Production: Raspberry Pi (ARM64, Debian-based)

**Schema (v1):**

```sql
-- All conversations in a single table
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

**Default Schedules:**

- 10:00 AM EST — Morning check-in
- 8:00 PM EST — Evening check-in

**Agent Personality:**

- Name: Luigi
- Tone: Calm, polite, empathetic, concise, straightforward
- Asks clarifying questions when uncertain
- Example greeting: "Hello, this is Luigi - your personal health assistant. How are you feeling today Shanelle?"

## Constraints

- No external database services; SQLite only
- No web UI in v1; all interaction via SMS
- Code must run identically on macOS (ARM64) and Raspberry Pi (ARM64/Debian)
- Twilio credentials stored in environment variables, never in code
- All dependencies must be pip-installable (no compiled binaries that break cross-platform)

## Rationale

**SMS over WhatsApp/App:** Zero setup for the user. She just texts a number. WhatsApp requires Business API approval and app installation. A native app is future scope.

**GPT-4o-mini over alternatives:** Best balance of cost ($0.15/$0.60 per 1M tokens), latency (~400ms), and conversational quality. DeepSeek is cheaper but has availability concerns and China-based data residency. Haiku is excellent but slightly more expensive with no meaningful quality gain for this use case.

**SQLite over Postgres/Supabase:** Portability is paramount. SQLite runs anywhere, requires no server, and the database file can be copied directly to a Raspberry Pi or bundled into a future mobile app. At this scale (dozens of messages/day), SQLite handles everything.

**FastAPI over Flask:** Async-native, better type hints, automatic OpenAPI docs. Negligible difference at this scale, but FastAPI is the more modern choice.

**APScheduler over cron:** In-process scheduling simplifies deployment. No system-level cron configuration needed on the Pi. Schedules live in the database, not in crontab files.

**Local-first architecture:** Keeps health data private. No cloud accounts, no third-party data storage. Future migration to a local-first mobile app remains straightforward.

## Amendment - implmentation details
# Health Tracker v1 - ADR Amendment: Implementation Details

**Status:** Accepted  
**Date:** February 2, 2026

## Context

Clarifications needed before implementation on conversation context, prompt storage, initialization, validation, and error handling.

## Decisions

### 1. LLM Conversation Context

**Decision:** Feed the LLM the lesser of:

- Last 24 hours of messages, OR
- Last 5 messages

**Rationale:** Keeps token costs low while preserving enough context for coherent replies. 5 messages is ~2-3 exchanges, sufficient for continuity.

### 2. System Prompt Storage

**Decision:** Hardcode Luigi's system prompt in `src/agent.py`.

**Rationale:** Personality is stable for v1. Version control provides change history. No need for runtime editability yet.

### 3. Scheduler Auto-Seeding

**Decision:** On app startup, if `schedules` table is empty, auto-insert default check-ins:

- 10:00 AM EST — "Good morning Shanelle! How are you feeling today?"
- 8:00 PM EST — "Evening check-in: How was your day? Any symptoms or notes to share?"

**Rationale:** Zero-friction first run. No manual SQL required.

### 4. Twilio Webhook Validation

**Decision:** Skip signature validation in v1. Add to backlog for v4.

**Rationale:** Acceptable risk for MVP; ngrok URLs are ephemeral. Production hardening comes later.

### 5. LLM Error Handling

**Decision:** On OpenRouter failure, send fallback SMS: _"The LLM call is failing, I'll try again soon."_ Log the error with full exception details. Retry logic deferred to v2.

**Rationale:** User should never be left without acknowledgment. Silent failures are bad UX for health tracking.