# Product Requirements: Health Tracker (Luigi)

## 1. Global Vision & Constraints
* **Core Goal:** Text-based health tracking for a user with chronic illness.
* **Tone:** Empathetic, warm, non-medical.
* **Privacy:** Data stays local/private where possible.
* **Tech Constraints:** MacBook M4 Host, Low Cost (OpenRouter), Python.

## 2. Active Feature Specs

### [NEWEST] Medication Tracking & Management — ADR (Mar 2, 2026)

**Status:** Accepted

**Context:** Luigi's core value proposition is health tracking, but it currently has no structured way to record medications, dosages, schedules, or adherence. Users with chronic illness often take multiple medications on complex schedules (daily, weekly, every 4 weeks) and need a system that understands grouping ("morning meds"), handles partial adherence (skips), and issues reminders — all through natural conversation.

**Decisions:**

- **Storage:** Three new tables (`medication_groups`, `medications`, `medication_events`) added to the existing per-user SQLite database (`data/{chat_id}.db`). No separate database.
- **Grouping:** All scheduled medications belong to a group, even solo meds (implicit group of one). Groups carry schedule info (hour, minute, interval, anchor date) and support comma-separated aliases. Reminders fire at the group level, not per-medication.
- **Scheduling:** Interval-based recurrence using `interval_days` + `start_date` anchor. Covers daily (1), weekly (7), biweekly (14), every-4-weeks (28), and any fixed cycle. Scheduler checks `(today - start_date) % interval_days == 0` to determine if a reminder fires.
- **Skip Handling:** `medication_events` logs every medication in a group when reported, with a `status` field (`taken` | `skipped`). Intentional skips are explicit records, not missing data.
- **LLM Architecture:** Two-call pipeline per message. Call 1 (Conversation): Luigi generates a natural response with no structured tags — personality prompt stays clean. Call 2 (Extraction): A separate LLM call receives the user message, Luigi's response, and current medication state, then returns structured JSON describing DB actions (`log_group`, `add_medication`, `create_group`, `log_single`, `modify_medication`, `none`).
- **Capture Flow:** Conversational capture with explicit user confirmation for medication setup (add/modify). Direct logging without confirmation for daily adherence ("took my morning meds"). Multi-turn confirmation prevents silent bad data on setup.
- **As-Needed Meds:** Not assigned to a group. Logged individually to `medication_events` when the user reports taking them.

**Rationale:**

- Per-user SQLite keeps the existing one-user-one-file pattern intact; no coordination overhead from a second database.
- Group-level reminders prevent notification fatigue — one reminder per time slot instead of N reminders per medication.
- Interval + anchor date covers the vast majority of real medication schedules without the complexity of cron expressions or RRULE parsing.
- Explicit skip tracking preserves clinically meaningful adherence data (skipped ≠ forgot ≠ no data).
- Two-call LLM separation keeps Luigi's conversational prompt focused on tone and personality while giving the extraction layer a dedicated prompt optimized for structured output. Models can be swapped independently. Cost impact is minimal at GPT-4o-mini pricing.
- Conversational capture with confirmation aligns with Luigi's personality as a recorder who confirms understanding, not an autonomous data extractor.

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
