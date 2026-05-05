---
description: "Python code style, logging, error handling, and structural conventions for luigi_app"
---

## Module & File Structure

- Core modules live in `src/` with descriptive names. Feature-specific submodules go in `src/{domain}/` (e.g., `src/medical/`).
- `tests/` contains all test files named `test_{module}.py`.
- `scripts/` is for one-time data operations (seed scripts, migrations). Feature-specific scripts go in `src/{domain}/scripts/`.
- `docs/` is for living documentation only — not scratch notes or temp outputs.

## Function Design

- **One function, one job.** If a name needs "and" to describe it, split it.
- **Pipeline steps separate transformation from writes.** A function that computes or transforms data must return its result. DB writes go in a separate, dedicated function.
  ✅ `action = _extract_schedule_tag(response_text)` then `await _execute_schedule_action(action, ...)`
  ❌ A parser function that also calls `add_schedule()` inside

## Logging

- Declare a module-level logger at the top of every module: `logger = logging.getLogger(__name__)`
- Use `logger.debug` for operational tracing, `logger.info` for significant lifecycle events, `logger.warning` for recoverable anomalies, `logger.error` for failures.
- Always pass `exc_info=True` when logging caught exceptions so the traceback is preserved:
  ✅ `logger.error(f"Failed to handle message for chat {chat_id}", exc_info=True)`
  ❌ `logger.error(f"Failed: {e}")`
- Never use `print()` in production code.

## Error Handling

- Catch exceptions at integration boundaries (Telegram handlers, LLM calls, scheduler jobs). Never swallow exceptions silently.
- Pipeline helpers should never raise — return `None` or a default value on failure, and log the issue.
  ✅ `except Exception: logger.error(..., exc_info=True); return None`
  ❌ `except Exception: pass`
- Reserve raising exceptions for true programming errors (wrong arguments, impossible state).

## Database Patterns

- Per-user SQLite: each user's data lives in `data/{chat_id}.db`. Use `get_user_db_path(database_dir, chat_id)` to resolve the path.
- Always close connections: open a connection at the start of a DB function, commit, and close before returning.
- Use `conn.row_factory = sqlite3.Row` so rows are dict-accessible.
- Schema changes go through `init_db()` using `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN` inside a `try/except` for idempotent migrations.
- Use parameterized queries (`?` placeholders). Never interpolate user input into SQL strings.

## Async Conventions

- The Telegram handler and scheduler are async. DB calls are synchronous — wrap them with `asyncio.to_thread()` inside async functions.
  ✅ `schedules = await asyncio.to_thread(get_all_schedules, db_path)`
  ❌ `schedules = get_all_schedules(db_path)` inside an `async def`

## Imports

- All imports at the top of the file. Deferred mid-function imports are only acceptable when breaking circular imports (document why).
- Use absolute imports from `src.*` (e.g., `from src.database import get_connection`).

## Type Hints

- Use type hints on all function signatures. Use `Optional[T]` from `typing` for nullable parameters.
- Return types must be annotated, including `-> None` for side-effect-only functions.

## Naming

- Functions: `snake_case`. Private/internal helpers: prefix with `_`.
- Constants: `UPPER_SNAKE_CASE` at module level.
- Do not use generic names: `run`, `script`, `temp`, `helper`, `utils` as a standalone filename.
