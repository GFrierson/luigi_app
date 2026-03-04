# Product Requirements: Health Tracker (Luigi)

## 1. Global Vision & Constraints
* **Core Goal:** Text-based health tracking for a user with chronic illness.
* **Tone:** Empathetic, warm, non-medical.
* **Privacy:** Data stays local/private where possible.
* **Tech Constraints:** MacBook M4 Host, Low Cost (OpenRouter), Python.

## 2. Active Feature Specs

*(No active specs — see Section 3 for completed features.)*

## 3. Completed Features Log

### [DONE] Medication Tracking & Management — ADR (Mar 2, 2026; implemented Mar 4, 2026)
**Summary:** Implemented full medication tracking: three new DB tables (`medication_groups`, `medications`, `medication_events`), a two-call LLM pipeline that extracts structured medication actions from every message, group-level APScheduler reminders via `CronTrigger` with internal interval checks, and a non-blocking dispatch layer in `handle_message()`. Deviations from the ADR: multi-turn confirmation for `add_medication`/`create_group` is staged but not yet surfaced to the user (confirmation loop is future work); the extraction model is hardcoded to `openai/gpt-4o-mini` rather than inheriting from the `LLM_MODEL` config setting.



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
