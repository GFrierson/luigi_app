---
description: Fast mechanical lint pass for Python code style violations
argument-hint: "[file-or-directory | staged | changed]"
---

# /lint — Python Code Style Check

You are running a fast lint pass to catch code style violations. This is a mechanical check, not a code review — don't evaluate logic, architecture, or bugs. Just check the rules.

## Scope

The user may provide a path as an argument: $ARGUMENTS

- **If a file path is provided:** Lint that file only.
- **If a directory path is provided:** Lint all `.py` files in that directory.
- **If "staged" or "changed" is provided:** Lint files from `git diff --name-only HEAD`.
- **If nothing is provided:** Default to `git diff --name-only HEAD` (uncommitted changes). If no uncommitted changes, fall back to `git diff --name-only HEAD~3` (last 3 commits).

## Before Scanning

1. Read `.claude/rules/python/code-style.md` to load the current rules. These are the source of truth.
2. Identify the files to scan based on the scope above. Skip `__pycache__/`, `*.pyc`, migrations, and test fixtures.

## Checks to Run

For each file in scope, check the following by reading the actual code.

### 1. Logging Violations
- Flag any use of `print()` in non-test code.
- Flag log calls missing the module-level `logger = logging.getLogger(__name__)` declaration.
- Flag exception catches that don't log `exc_info=True`:
  ```python
  except Exception as e:
      logger.error(f"failed: {e}")  # VIOLATION — missing exc_info=True
  ```
- Report: file, line number, what was found.

### 2. Silent Exception Swallowing
- Flag `except: pass` or `except Exception: pass` with no logging.
- Report: file, line number.

### 3. SQL Injection Risk
- Flag any SQL string built via f-string or `%` formatting with variables.
  ✅ `cursor.execute("SELECT * FROM foo WHERE id = ?", (user_id,))`
  ❌ `cursor.execute(f"SELECT * FROM foo WHERE id = {user_id}")`
- Report: file, line number.

### 4. Unclosed DB Connections
- Flag `sqlite3.connect()` calls where the connection is not `.close()`d on all paths (including exception paths).
- Report: file, function name, line number.

### 5. Async/Sync Mismatch
- Flag sync DB calls made directly inside `async def` functions without `asyncio.to_thread()`.
  ✅ `await asyncio.to_thread(get_all_schedules, db_path)`
  ❌ `get_all_schedules(db_path)` inside an `async def`
- Report: file, function name, line number.

### 6. Pipeline Write Separation
- Flag functions that both transform/compute data AND write to the database (calls to functions that do `INSERT`, `UPDATE`, `DELETE`).
- Report: file, function name, what write was found.

### 7. Mid-Function Imports
- Flag `import` statements inside function bodies (unless the comment explains it's a circular-import workaround).
- Report: file, line number.

### 8. Script / Filename Quality
- Flag files with generic names: `run.py`, `script.py`, `temp.py`, `helper.py`, `utils.py` as a top-level file (not in a `utils/` subdir).
- Report: file, suggested improvement.

## Output Format

Group violations by file:

```
## src/telegram_handler.py
- **Silent exception:** `except Exception: pass` on line 254 — log the error with exc_info=True
- **Async/sync mismatch:** `get_all_schedules(db_path)` on line 311 inside `async def handle_message` — wrap with asyncio.to_thread()

## src/database.py
- **Unclosed connection:** `get_connection()` on line 88 in `init_db` — connection not closed if cursor.execute raises
```

## After Scanning

End with a one-line summary: `**X violations across Y files.**`

If there are violations, ask: "Want me to fix any of these?"

If there are no violations: "Clean pass — no style violations found."

Do not explain the rules or justify the checks. Just report what you found.
