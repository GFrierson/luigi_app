# Product Requirements: Health Tracker (Luigi)

## 1. Global Vision & Constraints
* **Core Goal:** Text-based health tracking for a user with chronic illness.
* **Tone:** Empathetic, warm, non-medical.
* **Privacy:** Data stays local/private where possible.
* **Tech Constraints:** MacBook M4 Host, Low Cost (OpenRouter), Python.

## 2. Active Feature Specs

### [NEWEST] VPS Infrastructure & Deployment — ADR (Feb 19, 2026)
**Status:** Accepted
**Context:** Migrating from a Raspberry Pi target to an always-on VPS to support growth and remote development.

**Decisions:**
* **Provider:** Hetzner Cloud VPS (CAX11, ARM64, 2 vCPU, 4GB RAM) in US-East.
* **Messaging Transport:** Simplified from FastAPI webhooks to `python-telegram-bot` **polling mode** as a single systemd process.
* **Security:** Tailscale VPN for restricted SSH access; port 22 firewalled from public internet.
* **Deployment:** Git-based workflow (`git pull` on VPS) with systemd for process management and auto-restarts.

**Rationale:**
* Hetzner provides the best price-to-performance; ARM64 maintains consistency with the original Pi target.
* Polling mode eliminates the need for HTTP servers (FastAPI), SSL certificates, and public URL tunnels (ngrok), reducing operational complexity for a single-user bot.

---

### Feature: Check-in Scheduler
* **User Story:** "As Shanelle, I want to receive a text at 9 AM asking how I feel..."
* **Requirements:**
    * Must handle timezones.
    * Must allow user to reply "Stop" to cancel.

## 3. Completed Features Log

### [DONE] v1.1 Telegram Migration — Polling Mode & Multi-User Architecture (Feb 23, 2026)
**Summary:** Completed migration from FastAPI webhook + Twilio SMS to a clean `python-telegram-bot` polling process. Preserved and formalized the multi-user architecture introduced during migration.
* **Polling entrypoint:** `src/main.py` rewritten as a single `main()` function; `src/polling.py` deleted (merged).
* **Multi-user architecture:** Each user gets their own SQLite DB keyed by `chat_id` under `DATABASE_DIR`; no hardcoded `TELEGRAM_CHAT_ID`.
* **Handler consolidation:** All Telegram logic (`handle_message`, `_on_message`, `start_command`, `create_application`) lives in `src/telegram_handler.py`.
* **Dependencies pruned:** Removed `fastapi`, `uvicorn`, `httpx`, `twilio` from `requirements.txt`.
* **Deployment:** Added `deploy/luigi.service` systemd unit for VPS (Hetzner CAX11).
* **Tests:** 57/57 passing.

---

### [DONE] Feature 1: The Telegram Pivot — ADR (Feb 2, 2026)
**Summary:** Pivoted from Twilio/SMS to the Telegram Bot API to bypass A2P business verification requirements and eliminate messaging costs.
* **Core Implementation:** Built "Luigi," a Telegram agent using `python-telegram-bot`, SQLite, and OpenRouter (GPT-4o-mini).
* **Behavior:** Luigi records and restates health information for confirmation; he is strictly a tracker and is forbidden from giving medical advice.
* **Persistence:** All conversation history and schedules are stored in a local-first SQLite database (`messages` and `schedules` tables).
* **Foundation:** Established the empathetic, concise personality and the logic for cost-efficient conversation context.
