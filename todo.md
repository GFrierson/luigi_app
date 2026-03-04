# Medication Tracking & Management — Implementation Plan

## Context
Add medication tracking to Luigi: three new DB tables (groups, medications, events), a two-call LLM architecture (conversation + extraction), conversational capture with confirmation for setup, direct logging for adherence, and group-level reminders via the existing APScheduler.

## Rationale & Consequences
This plan is sequenced to build the data layer first (testable in isolation), then the extraction layer (testable with mocked DB), then wire into the message handler, and finally connect reminders to the scheduler. Each sub-phase produces a working, testable increment. The two-call LLM architecture adds latency to every message (~200-400ms for Call 2). The extraction prompt will need iteration once real user messages are flowing — expect to revisit it. The `[PREFERRED_NAME: X]` tag pattern in `src/agent.py` is NOT replaced; it stays as-is for now since it's in Call 1 territory. Technical debt: no retry logic on Call 2 failure (extraction silently fails, conversation still delivered).

## Target Files
- `src/database.py` (modify — add tables + query functions)
- `src/agent.py` (modify — add extraction prompt + function)
- `src/telegram_handler.py` (modify — wire Call 2 into message flow)
- `src/scheduler.py` (modify — add medication reminder jobs)
- `tests/test_database.py` (modify — add medication table tests)
- `tests/test_agent.py` (modify — add extraction tests)
- `tests/test_telegram.py` (modify — add medication flow tests)

---

## Sub-Phase 1: Database Schema & Queries

### Step 1.1 — Add medication tables to `init_db()` in `src/database.py`

Add three new `CREATE TABLE IF NOT EXISTS` statements inside the existing `init_db()` function, after the `user_profile` table creation. Use the same pattern (raw SQL, no ORM).

```sql
CREATE TABLE IF NOT EXISTS medication_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    aliases TEXT,
    schedule_hour INTEGER CHECK (schedule_hour >= 0 AND schedule_hour <= 23),
    schedule_minute INTEGER CHECK (schedule_minute >= 0 AND schedule_minute <= 59),
    interval_days INTEGER DEFAULT 1,
    start_date DATE,
    reminder_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS medications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    dosage TEXT,
    type TEXT NOT NULL CHECK (type IN ('scheduled', 'as_needed')),
    group_id INTEGER REFERENCES medication_groups(id),
    active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS medication_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    medication_id INTEGER NOT NULL REFERENCES medications(id),
    taken_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL CHECK (status IN ('taken', 'skipped')),
    notes TEXT,
    source TEXT NOT NULL CHECK (source IN ('user', 'reminder'))
);
```

Add an index on `medication_events`:
```sql
CREATE INDEX IF NOT EXISTS idx_medication_events_taken_at ON medication_events(taken_at);
```

### Step 1.2 — TDD: Write tests for medication DB functions in `tests/test_database.py`

Add a new test class or section for medication queries. Tests must cover:

1. `test_create_medication_group` — insert a group, verify it's retrievable
2. `test_create_medication_in_group` — insert a medication linked to a group
3. `test_create_as_needed_medication` — insert a medication with `type='as_needed'` and `group_id=NULL`
4. `test_log_medication_event_taken` — insert an event with `status='taken'`
5. `test_log_medication_event_skipped` — insert an event with `status='skipped'`
6. `test_get_medications_by_group` — retrieve all active meds in a group
7. `test_get_all_medication_groups` — retrieve all groups with `reminder_active=TRUE`
8. `test_get_medication_group_by_name_or_alias` — look up group by name or alias match
9. `test_log_group_event_with_skips` — log a full group (some taken, some skipped), verify all rows created
10. `test_init_db_creates_medication_tables` — verify `init_db()` creates all three tables

Use the existing test pattern: create a temp DB with `init_db()`, run operations, assert results, clean up.

### Step 1.3 — Implement query functions in `src/database.py`

Add the following functions to `src/database.py`, following the existing `(db_path, ...) -> result` signature pattern:

```python
def create_medication_group(db_path: str, name: str, aliases: str | None, schedule_hour: int | None, schedule_minute: int | None, interval_days: int = 1, start_date: str | None = None) -> int
    # Returns group id

def create_medication(db_path: str, name: str, dosage: str | None, med_type: str, group_id: int | None = None) -> int
    # Returns medication id

def get_medications_by_group(db_path: str, group_id: int) -> list[dict]
    # Returns active medications in a group

def get_all_active_medication_groups(db_path: str) -> list[dict]
    # Returns groups where reminder_active=TRUE

def find_medication_group(db_path: str, query: str) -> dict | None
    # Match query against group name or aliases (case-insensitive)
    # Check: name LIKE query OR any alias in comma-separated aliases matches
    # Return first match or None

def get_all_medications(db_path: str) -> list[dict]
    # Returns all active medications (both scheduled and as-needed)

def get_medication_by_name(db_path: str, name: str) -> dict | None
    # Case-insensitive lookup by medication name

def log_medication_event(db_path: str, medication_id: int, status: str, source: str = 'user', notes: str | None = None) -> int
    # Returns event id

def log_group_events(db_path: str, group_id: int, taken_ids: list[int], skipped_ids: list[int], source: str = 'user') -> list[int]
    # Inserts one row per medication in the group
    # taken_ids get status='taken', skipped_ids get status='skipped'
    # Returns list of event ids

def deactivate_medication(db_path: str, medication_id: int) -> None
    # Sets active=FALSE
```

Each function must include `logger.debug()` or `logger.info()` calls consistent with the existing logging pattern in `src/database.py`.

### Step 1.4 — VERIFY
Run `/run pytest tests/test_database.py -v` and confirm all new and existing tests pass.

---

## Sub-Phase 2: Extraction LLM Call

### Step 2.1 — TDD: Write tests for extraction in `tests/test_agent.py`

Add tests for a new `extract_medication_action()` function. Mock the OpenAI client (same pattern as existing `test_agent.py` tests). Test cases:

1. `test_extract_log_group` — input: "took my morning meds" → `{"action": "log_group", "group_name": "morning meds", "taken": "all", "skipped": []}`
2. `test_extract_log_group_with_skip` — input: "took morning meds but skipped lorazepam" → `{"action": "log_group", "group_name": "morning meds", "taken": "rest", "skipped": ["lorazepam"]}`
3. `test_extract_log_single` — input: "just took ibuprofen" → `{"action": "log_single", "medication_name": "ibuprofen"}`
4. `test_extract_add_medication` — input: "I take metformin 500mg every morning at 8" → `{"action": "add_medication", ...}`
5. `test_extract_none` — input: "I have a headache" → `{"action": "none"}`
6. `test_extract_malformed_json_returns_none` — LLM returns garbage → function returns `{"action": "none"}`

### Step 2.2 — Implement `extract_medication_action()` in `src/agent.py`

Add a new function:

```python
def get_medication_state_context(medications: list[dict], groups: list[dict]) -> str
    # Formats current medication state as readable text for the extraction prompt

def get_extraction_prompt() -> str
    # Returns the system prompt for Call 2
    # Must define the JSON schema for all action types:
    # log_group, log_single, add_medication, create_group, modify_medication, none
    # Must instruct the model to return ONLY valid JSON, no prose

def extract_medication_action(user_message: str, luigi_response: str, medication_state: str) -> dict
    # Calls GPT-4o-mini with the extraction prompt
    # Parses JSON response
    # On any parse failure, returns {"action": "none"}
    # Uses same OpenAI client pattern as generate_response()
```

The extraction prompt must clearly define each action type and its required fields. Use a `json` code fence in the prompt to show the model the expected schema. Include 2-3 few-shot examples in the prompt for the most common actions (`log_group`, `log_single`, `none`).

### Step 2.3 — VERIFY
Run `/run pytest tests/test_agent.py -v` and confirm all new and existing tests pass.

---

## Sub-Phase 3: Message Handler Integration

### Step 3.1 — TDD: Write tests for medication handling in `tests/test_telegram.py`

Add tests for the medication flow inside `handle_message()`. Mock both LLM calls (conversation + extraction). Test cases:

1. `test_message_triggers_extraction_call` — verify that after `generate_response()`, `extract_medication_action()` is called
2. `test_log_group_action_creates_events` — extraction returns `log_group` → verify `log_group_events()` called with correct IDs
3. `test_add_medication_action_stages_pending` — extraction returns `add_medication` → verify data is staged, not committed
4. `test_none_action_skips_db_writes` — extraction returns `none` → no medication DB writes
5. `test_extraction_failure_does_not_block_response` — extraction throws exception → user still receives Luigi's response

### Step 3.2 — Implement medication dispatch in `src/telegram_handler.py`

Modify `handle_message()` in `src/telegram_handler.py`. After the existing response generation and `_extract_preferred_name_tag()` logic, add:

```python
# After sending Luigi's response to the user:
# 1. Build medication state context from DB
# 2. Call extract_medication_action()
# 3. Dispatch based on action type
```

Create a new helper function in `src/telegram_handler.py`:

```python
def _process_medication_action(action: dict, db_path: str, chat_id: int) -> None
    # Switch on action["action"]:
    #   "log_group" → resolve group, call log_group_events()
    #   "log_single" → resolve medication, call log_medication_event()
    #   "add_medication" → stage as pending (store in memory or DB)
    #   "create_group" → stage as pending
    #   "none" → return
    # Wrap in try/except, log errors, never raise
```

Wrap the entire extraction + dispatch in a `try/except` block so extraction failures never prevent the user from receiving Luigi's conversational response.

### Step 3.3 — VERIFY
Run `/run pytest tests/test_telegram.py -v` and confirm all new and existing tests pass.

---

## Sub-Phase 4: Medication Reminders in Scheduler

### Step 4.1 — TDD: Write tests for medication scheduling in a new file `tests/test_medication_scheduler.py`

1. `test_medication_reminder_registered_for_group` — verify a group with `reminder_active=TRUE` gets a cron job
2. `test_interval_check_fires_on_correct_day` — given `start_date` and `interval_days=7`, verify the check passes on day 7, fails on day 3
3. `test_medication_reminder_not_registered_when_inactive` — group with `reminder_active=FALSE` gets no job

### Step 4.2 — Add medication reminders to `src/scheduler.py`

Modify `schedule_check_ins()` in `src/scheduler.py`. After the existing schedule loop, add a second loop that reads `get_all_active_medication_groups(db_path)` for each user and registers medication reminder jobs.

Add a new function:

```python
async def send_medication_reminder(group_name: str, chat_id: int, db_path: str) -> None
    # 1. Check interval: (today - start_date) % interval_days == 0
    #    If not, return early (skip this day)
    # 2. Get medications in group via get_medications_by_group()
    # 3. Format reminder message: "Checking in — did you take your {group_name}?"
    # 4. Send via send_message()
    # 5. Log outbound message to DB
```

Use `CronTrigger(hour=group['schedule_hour'], minute=group['schedule_minute'])` — the interval check happens inside the job, not in the trigger. This avoids complex trigger math and keeps APScheduler jobs simple.

Job ID pattern: `"med_reminder_{chat_id}_{group_id}"` to avoid collisions with existing `"checkin_{chat_id}_..."` IDs.

### Step 4.3 — VERIFY
Run `/run pytest tests/ -v` to confirm ALL tests (existing + new) pass.

---

## Sub-Phase 5: Final Integration Verification

### Step 5.1 — VERIFY
Run `/run pytest tests/ -v` and confirm the full suite passes. Count should be 57 (existing) + new tests.

### Step 5.2 — DOCUMENT
Update `product_requirements.md` Section 3 (Completed Features Log): move the Medication Tracking ADR from Section 2 to Section 3 with a 2-sentence summary of what was implemented and any deviations from this plan.

---

Want me to write this out as a file you can save directly as `todo.md`?
