---
description: Quick breakage check on uncommitted changes before continuing work
---

## Changed Files (Uncommitted)

!`git diff --name-only; git diff --cached --name-only`

## Uncommitted Diff

!`git diff; git diff --cached`

## Test Run

!`pytest tests/ -x -q 2>&1 | head -50 || true`

---

You are doing a **breakage check**, not a code review. Focus exclusively on whether recent uncommitted changes have broken existing systems. Ignore style, performance, and missing tests.

## What to Check

For each changed file, trace outward — look at what the changed code exports or depends on, then check if anything outside the diff is now broken.

Specifically look for:

1. **Stale references** — Was a function renamed or removed? Search for other files that still call the old name.

2. **Signature mismatches** — Was a function signature changed (new required param, return type change)? Find callers and verify they've been updated.

3. **DB schema changes without migration** — Was a table or column added/changed in code but not in `init_db()`? Flag if the schema and migration are out of sync.

4. **Async/sync mismatch** — Was a sync DB function called inside an `async def` without `asyncio.to_thread()`? Was a coroutine called without `await`?

5. **Import breaks** — Were any module-level imports changed? Check that dependents still import the correct names.

## Test Output

Report any failures from the pytest run above. If tests fail, identify which changed file caused them.

## Output Format

If issues are found:
```
Issues found:

1. [description] — file:line
   Fix: [what to do]

2. ...

Verdict: Fix these before continuing.
```

If nothing is broken:
```
Verdict: Safe to continue.
```

Do not add caveats, suggestions, or general advice. Only report concrete breakage.
