---
description: "pytest conventions, fixture patterns, and mocking rules for luigi_app tests"
---

## Framework

- **pytest** with no mock database — tests run against a real SQLite file created via `tmp_path`.
- Run tests: `pytest tests/ -x -q`
- Run a single file: `pytest tests/test_database.py -x -q`
- Test files: `tests/test_{module}.py`. Add to an existing file rather than creating a duplicate.

## Database Fixtures

Use `tmp_path` (built-in pytest fixture) to create isolated per-test SQLite files:

```python
import pytest
from src.database import init_db, get_user_db_path

@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path
```

**Always mock:** LLM/OpenRouter API calls, Telegram Bot API calls, external HTTP.
**Never mock:** SQLite / the database.

## Mocking External Services

Use `pytest-mock` (`mocker` fixture) or `unittest.mock.patch`:

```python
from unittest.mock import patch, MagicMock

def test_something(db_path, mocker):
    mock_response = mocker.patch("src.agent.call_llm", return_value="Hello!")
    # ... test code
```

## What to Test

**In scope:**
- Database helper functions (CRUD, migrations, constraint enforcement)
- Message handler logic (`handle_message`) with mocked LLM and Telegram Bot
- Tag extraction helpers (`_extract_schedule_tag`, `_extract_preferred_name_tag`)
- Scheduler job registration/removal

**Out of scope — do not attempt:**
- Live Telegram API calls
- Live LLM calls (always mock)
- End-to-end network tests

## Test Quality Rules

1. **One scenario per test function.** Each test covers exactly one case.
2. **Test behavior, not implementation.** Assert what the function *does* (DB state, return value), not which internal functions it calls.
3. **Cover key paths:** happy path, not-found / empty case, duplicate / constraint violation, invalid input.
4. **Meaningful assertions.** A test that only checks `result is not None` without verifying the data is incomplete.
5. **Use descriptive names:** `test_add_schedule_rejects_duplicate_time`, not `test_add_schedule_2`.

## Example: Database Test

```python
def test_add_schedule_returns_row(db_path):
    result = add_schedule(db_path, 8, 0, "Good morning!")
    assert result is not None
    assert result["hour"] == 8
    assert result["minute"] == 0

def test_add_schedule_rejects_duplicate_time(db_path):
    add_schedule(db_path, 8, 0, "First")
    result = add_schedule(db_path, 8, 0, "Second")
    assert result is None
```

## Example: Async Handler Test

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_handle_message_returns_response(tmp_path):
    db_path = str(tmp_path / "test.db")
    with patch("src.telegram_handler.generate_response", return_value="Hi!"), \
         patch("src.telegram_handler.send_message", new_callable=AsyncMock, return_value=42):
        response = await handle_message(chat_id=12345, text="Hello", message_id=1)
    assert response == "Hi!"
```
