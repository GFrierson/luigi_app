# Product Requirements: Health Tracker (Luigi)

## 1. Global Vision & Constraints
*(These apply to EVERY feature. Ideally, they never change.)*
* **Core Goal:** Text-based health tracking for a user with chronic illness.
* **Tone:** Empathetic, warm, non-medical.
* **Privacy:** Data stays local/private where possible.
* **Tech Constraints:** MacBook M4 Host, Low Cost (OpenRouter), Python/FastAPI.

## 2. Active Feature Specs
* **Feature:** Check-in Scheduler
* **User Story:** "As Shanelle, I want to receive a text at 9 AM asking how I feel..."
* **Requirements:**
    * Must handle timezones.
    * Must allow user to reply "Stop" to cancel.

## 3. Completed Features Log
* **[Done] Feature 1: The Core Brain (v1 Implementation)**
    * **Messaging Pipeline:** Integrated Twilio for inbound webhooks and outbound API calls.
    * **LLM Intelligence:** Configured "Luigi" agent via OpenRouter (GPT-4o-mini) with an empathetic, concise system prompt.
    * **Context Management:** Implemented logic to feed the LLM the lesser of the last 24 hours or 5 messages for cost-efficient continuity.
    * **Local Persistence:** Set up SQLite for portable, local-first storage of conversation history and schedules.
    * **Automated Prompts:** Integrated APScheduler for in-process morning (10 AM) and evening (8 PM) check-ins with auto-seeding logic.
    * **Observability:** Established standard logging patterns and a TDD-focused test suite using `pytest`.

---

# Health Tracker v1 - Architecture Decision Record (ADR)
**Status:** Accepted  
**Date:** February 2, 2026

## Context
We are building a conversational health tracker for a user (Shanelle) with chronic illnesses. The core need is a low-friction way for her to log symptoms, medications, and general wellbeing through natural conversation.

## Decision
Build an SMS-based conversational agent named "Luigi" that responds to texts, sends scheduled prompts, and stores data locally in SQLite.

## Technical Details
- **Runtime:** Python 3.12+
- **Web Framework:** FastAPI
- **Database:** SQLite (portable file)
- **Scheduler:** APScheduler
- **LLM:** OpenAI GPT-4o-mini via OpenRouter
- **SMS Provider:** Twilio
