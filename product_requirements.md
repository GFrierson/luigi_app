# Product Requirements: Health Tracker (Luigi)
---

# VPS Infrastructure & Deployment — Architecture Decision Record (ADR)

**Status:** Accepted  
**Date:** February 19, 2026

## Context

The original plan targeted a 2014 Raspberry Pi Model B+ (512MB RAM) as the production server. As the project scope expanded to include conversation storage, future RAG/extraction pipelines, and multi-agent deployment, the Pi's hardware became insufficient. We need an always-on server that supports remote development, can run multiple Python processes, and has room to grow — while keeping costs minimal.

## Decision

Migrate production deployment from Raspberry Pi to a **Hetzner Cloud VPS (CAX11)** and simplify the runtime from FastAPI webhook mode to **`python-telegram-bot` polling mode** as a single Python process managed by systemd. Use **Tailscale** for secure SSH access. Deploy via **git pull from main branch**.

## Technical Details

### Infrastructure

- **Provider:** Hetzner Cloud
- **Plan:** CAX11 (ARM64 Ampere, 2 vCPU, 4GB RAM, 40GB SSD)
- **Region:** Ashburn, VA (us-east)
- **Cost:** ~$4.50/month
- **OS:** Ubuntu 24.04 LTS (ARM64)

### Remote Access

- **Tailscale** mesh VPN installed on both MacBook (dev) and VPS (prod)
- SSH restricted to Tailscale network (port 22 firewalled from public internet via Hetzner firewall rules)

### Deployment Workflow

- **Dev:** Local MacBook Air M4, develop on local/feature branches with Aider
- **Prod:** VPS pulls from `main` branch on GitHub
- **Deploy command:** `ssh vps "cd ~/health-tracker && git pull && sudo systemctl restart luigi"`
- **Process manager:** systemd (auto-restart on crash, start on boot)

### Runtime Architecture Change

- **Removed from stack:** FastAPI, Uvicorn, ngrok, Cloudflare Tunnel
- **Retained:** `python-telegram-bot` (polling mode), APScheduler, SQLite, OpenAI SDK (OpenRouter)
- **Process model:** Single Python process runs both the Telegram polling loop and APScheduler in-process
- **Entrypoint:** `src/main.py` calls `Application.run_polling()` (no ASGI server)

### Changes to `tech_stack.md`

|Section|Old|New|
|---|---|---|
|Target OS (Prod)|Raspberry Pi OS / Debian (ARM64)|Ubuntu 24.04 LTS (ARM64) — Hetzner CAX11|
|Web Framework|FastAPI + Uvicorn|**Removed** (not needed for polling mode)|
|Messaging Transport|Webhook mode (prod) / polling (dev)|Polling mode (both dev and prod)|
|Local Development|ngrok for webhook exposure|No ngrok needed|
|API Endpoints|`/webhook/telegram`, `/health`|**Removed** (no HTTP server in v1)|
|Deployment Notes|Cloudflare Tunnel + webhook|systemd + git pull|
|`requirements.txt`|Includes `fastapi`, `uvicorn`, `httpx`|Remove `fastapi`, `uvicorn`, `httpx`|

### New Files to Add

- `deploy/luigi.service` — systemd unit file
- `deploy/setup.sh` — one-time VPS provisioning script (optional)

### Project Structure Update

```
health-tracker/
├── src/
│   ├── __init__.py
│   ├── main.py              # Polling entrypoint (no FastAPI)
│   ├── agent.py             # LLM conversation logic
│   ├── database.py          # SQLite connection + queries
│   ├── scheduler.py         # APScheduler setup + jobs
│   ├── telegram_handler.py  # Telegram send/receive helpers
│   └── config.py            # Environment variable loading
├── tests/
├── deploy/
│   └── luigi.service         # systemd unit file
├── data/
├── .env.example
├── requirements.txt
└── README.md
```

## Rationale

**Why Hetzner over DigitalOcean/Contabo:** Best price-to-performance at this tier. ARM64 architecture matches the original Pi deployment target, maintaining consistency. US-East datacenter minimizes latency to Telegram's API and OpenRouter. DigitalOcean costs 2-3x more for equivalent specs. Contabo has reliability concerns.

**Why polling over webhook:** Webhook requires a web server (FastAPI/Uvicorn), SSL certificates, and a public URL — infrastructure complexity that provides no meaningful benefit for a single-user Telegram bot. Polling is operationally simpler (one process, no HTTP server, no cert management) with negligible latency difference. The migration to webhook mode later is trivial (~30 min) if a future agent needs HTTP endpoints.

**Why Tailscale over direct SSH:** The VPS has a public IP, but restricting SSH to Tailscale means port 22 is never exposed to the internet. This eliminates brute-force SSH attempts entirely and provides a consistent access pattern whether the server is a VPS, a Pi, or any future device.

**Why not Docker:** Adds memory overhead and operational complexity that isn't justified for a single Python application on a resource-conscious VPS. systemd provides process management, auto-restart, and logging natively.

**Upgrade path:** When the project outgrows this setup (multiple agents, web dashboard, webhook-based integrations), the natural next step is adding FastAPI back as an HTTP layer alongside the polling bot, or upgrading to the CAX21 (4 vCPU, 8GB, ~$8/month). No architectural dead-ends.

---
# Health Tracker v1 - Architecture Decision Record (ADR)

**Status:** Accepted  
**Date:** February 2, 2026

## Context

We are building a conversational health tracker for a user (Shanelle) with chronic illnesses. The core need is a low-friction way for her to log symptoms, medications, and general wellbeing through natural conversation. The system must be simple to use, reliable, and preserve privacy by keeping data local.

**Initial approach (SMS via Twilio) was blocked:** Twilio now requires A2P 10DLC business verification, including a registered business entity and website. This is incompatible with personal/family use cases.

**Pivot decision:** Use Telegram Bot API instead. Requires one-time app installation but eliminates all verification requirements and ongoing costs.

## Decision

Build a Telegram-based conversational agent named "Luigi" that:
1. Responds to inbound messages with contextual, empathetic replies
2. Sends scheduled check-in prompts (morning and evening)
3. Stores all conversation history locally in SQLite

The system will run locally on developer hardware (MacBook Air for testing, Raspberry Pi for production) with Telegram handling message transport.

## Technical Details

- **Runtime:** Python 3.12+
- **Web Framework:** FastAPI (webhook receiver)
- **Database:** SQLite (single file, portable)
- **Scheduler:** APScheduler (in-process cron)
- **LLM:** GPT-4o-mini via OpenRouter API
- **Messaging:** Telegram Bot API via `python-telegram-bot`
- **Dev Mode:** Polling (no ngrok required)
- **Prod Mode:** Webhook behind Cloudflare Tunnel or similar

**Target Environments:**
- Development: macOS (Apple Silicon)
- Production: Raspberry Pi (ARM64, Debian-based)

**Schema (v1):**
```sql
-- All conversations in a single table
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    body TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    telegram_message_id INTEGER
);

CREATE INDEX idx_messages_timestamp ON messages(timestamp);

-- Scheduled prompts
CREATE TABLE IF NOT EXISTS schedules (
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

### Agent Behavior Rules

**Primary role:** Record and acknowledge health information. Luigi is a tracker, not an advisor.

**Confirmation pattern:** Luigi restates what it understood from each message to confirm accuracy. This is conversational confirmation, not database verification (structured extraction deferred to v2).

**Follow-up questions:** Minimal by default. Only ask when information is genuinely ambiguous (e.g., "pain" without location). If user requests fewer questions, comply.

**Malleability:** Luigi adapts to user preferences expressed in conversation:

- "Ask me less" → Reduce follow-ups
- "Check in about my water intake" → Add to tracking focus

**Hard constraints:**

- Never give health advice or suggestions
- Never recommend treatments, remedies, or lifestyle changes
- Never suggest when to see a doctor
- If asked for advice, redirect to recording

**Scheduled check-ins:**

- Fetch last 24 hours of messages for context
- LLM generates contextual greeting referencing recent symptoms if relevant
- Falls back to generic "How are you feeling today?" if no recent context

**Data collection:** All messages stored in `messages` table. This builds a dataset for structured extraction in v2.

---

## Constraints

- No external database services; SQLite only
- No web UI in v1; all interaction via Telegram
- Code must run identically on macOS (ARM64) and Raspberry Pi (ARM64/Debian)
- Telegram bot token stored in environment variables, never in code
- Single-user only in v1; `TELEGRAM_CHAT_ID` hardcoded after initial setup
- All dependencies must be pip-installable (no compiled binaries that break cross-platform)

## User Setup (One-Time)

1. Shanelle installs Telegram on her phone (if not already installed)
2. She searches for the bot by username (e.g., `luigi_health_bot`)
3. She taps **Start** and sends any message
4. Developer captures her `chat_id` from logs and adds to `.env`
5. From then on, she messages the bot like any other contact

## Rationale

**Telegram over SMS:**
- Twilio SMS requires business verification; Telegram requires nothing
- Telegram is free; Twilio costs ~$5/month minimum
- Telegram supports rich messages (buttons, images) for future features
- One-time app install is acceptable tradeoff for zero bureaucracy

**GPT-4o-mini via OpenRouter:**
- Best balance of cost ($0.15/$0.60 per 1M tokens), latency (~400ms), and conversational quality
- OpenRouter provides unified API with easy model switching if needed
- Using OpenAI SDK with custom base URL keeps code portable

**SQLite over Postgres/Supabase:**
- Portability is paramount. SQLite runs anywhere, requires no server
- Database file can be copied directly to Raspberry Pi or bundled into future mobile app
- At this scale (dozens of messages/day), SQLite handles everything

**FastAPI over Flask:**
- Async-native, better type hints, automatic OpenAPI docs
- Telegram webhook handling benefits from async

**APScheduler over cron:**
- In-process scheduling simplifies deployment
- No system-level cron configuration needed on the Pi
- Schedules live in the database, not in crontab files

**Polling mode for development:**
- No ngrok or public URL required
- Bot polls Telegram servers; simpler local setup
- Switch to webhook mode for production (more efficient)

**Local-first architecture:**
- Keeps health data private
- No cloud accounts, no third-party data storage
- Future migration to a local-first mobile app remains straightforward

## v1 Scope Summary

**In scope:**
- Telegram bot receives messages, responds via LLM
- Scheduled morning (10am) and evening (8pm) check-ins
- All messages saved to SQLite with timestamps
- Runs locally on Mac (dev) and Raspberry Pi (prod)

**Out of scope (future versions):**
- Structured extraction of symptoms/medications
- Natural language schedule management ("remind me at 2pm")
- Multi-user support
- Web dashboard
- Rich Telegram features (buttons, inline keyboards)

---
