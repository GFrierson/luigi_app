---
description: Fixer loop — claims pending issues from the ledger, routes them to specialist agents, validates with tests, and updates issue status. Run standalone or with /loop.
argument-hint: "[--max-issues N] [--section SECTION] [--dry-run]"
---

# Fix Issues — Fixer Loop

Process pending issues from the ledger. Claims issues at startup, routes to the right agent per section simultaneously, then validates with pytest and reviewer agents.

1. Load ledger, recover stale in-progress entries
2. Identify blocked sections (another fixer run is active)
3. Claim a batch of pending issues — write before any fix work
4. Route each section's issues to the right agent
5. Launch all section agents in parallel
6. Validate each section as its agent completes: pytest + reviewer agents
7. Update ledger with final status
8. Report

**Ledger location:** `scratch/code-review-loop/ledger.json`
**Pair with:** `/code-review-loop` to populate the ledger with new issues

---

## Step 0: Parse Arguments and Generate Run ID

Check `$ARGUMENTS` for:
- `--max-issues N` — cap total issues to claim this run (default: 10)
- `--section SECTION` — only process issues in this section (e.g. `--section src`)
- `--dry-run` → show what would be claimed and fixed, but make no changes

Generate a run ID: `fixer-<unix-timestamp-seconds>` (e.g. `fixer-1746400000`). Used as `claimedBy` and in commit messages.

---

## Step 1: Pre-flight

```bash
git branch --show-current
git log --oneline -3
git status --short
```

If there are uncommitted changes, **stop and emit `ANOTHER_SESSION_ACTIVE`** — do not auto-commit them. Ask the user to commit or stash their work first, then restart.

Record the current HEAD SHA as the baseline.

---

## Step 2: Load Ledger and Recover Stale Entries

```bash
cat scratch/code-review-loop/ledger.json 2>/dev/null || echo "[]"
```

**Stale detection:** For every entry where `status === "in-progress"`:
- If `claimedAt` is more than 30 minutes ago, reset it:
  - `status` → `"pending"`
  - `claimedAt` → `null`
  - `claimedBy` → `null`
  - `iterationNote` → `"reset from stale in-progress by <runId> at <now>"`

If any entries were reset, write the updated ledger back now, before continuing.

Report: `"X stale in-progress entries reset to pending."`

---

## Step 3: Select and Claim Issues

### 3a. Identify blocked sections

Collect all sections where any entry has `status === "in-progress"`. These are blocked — another fixer owns them.

Report: `"Blocked sections (active fixer run): [list]"` or `"No blocked sections."`

### 3b. Filter candidates

From the ledger, select entries where:
- `status === "pending"`
- `section` is NOT in the blocked list
- If `--section` was passed: only that section

Sort by severity: CRITICAL → HIGH → MEDIUM → LOW. Within the same severity: `bug` before `quick`.

Apply `--max-issues` cap.

If 0 candidates: output `"No pending issues available to claim."` and stop.

### 3c. Claim immediately — write before any fix work

Update all selected entries in the ledger:
- `status` → `"in-progress"`
- `claimedAt` → current ISO timestamp
- `claimedBy` → `<runId>`

Write the full ledger back now. This is the concurrency gate.

Report: `"Claimed N issues across sections: [list]. Run ID: <runId>"`

If `--dry-run`: print the claim plan and stop.

---

## Step 4: Route Issues to Agents

Group claimed issues by section. For each section's issue set, determine the agent:

| Condition | Agent |
|---|---|
| File is in `tests/` or ends in `_test.py` | `@test-writer` |
| `complexity === "bug"` OR severity is CRITICAL/HIGH | `general-purpose` |
| `complexity === "quick"` | `general-purpose` |

Print routing plan:
```
Section: src
  → general-purpose: 3 issues (2 bug, 1 quick)

Section: tests
  → test-writer: 2 issues (quick)
```

---

## Step 5: Launch Section Agents in Parallel

Launch one agent per section simultaneously. Different sections don't share files, so parallel execution is safe.

### Agent prompts by type

**`general-purpose`:**
> "Fix the following Python code issues. Fix ALL of them.
>
> Issues:
> [For each: file, line, description, why, fix suggestion]
>
> Rules:
> - Fix ONLY the listed issues. Do not refactor or reorganize anything else.
> - Read each file fully before editing.
> - Use parameterized SQL queries (`?` placeholders), never string interpolation.
> - Use `logger.*` not `print()` for logging; always pass `exc_info=True` when logging caught exceptions.
> - Always close SQLite connections.
> - Wrap sync DB calls in `asyncio.to_thread()` inside async functions.
> - After all edits, output: FIXES_APPLIED"

**`@test-writer`:**
> "Fix the following test issues. Fix ALL of them.
>
> Issues:
> [For each: file, line, description, why, fix suggestion]
>
> Rules:
> - Fix ONLY these issues.
> - Read each file fully before editing.
> - After all edits, output: FIXES_APPLIED"

---

## Step 6: Validate Each Section as It Completes

As each section's agent finishes, validate immediately — do not wait for all agents.

### 6a. Run pytest

```bash
pytest tests/ -x -q 2>&1 | tail -30
```

If pytest **fails**:
- Identify which files this section's agent changed: `git diff --name-only HEAD`
- Revert only those files: `git checkout -- <files>`
- Mark all issues in this section's batch as `status: "failed"`, `iterationNote: "pytest failed: <first error line>"`
- Write ledger update
- Continue — other sections' validations proceed normally

If pytest **passes**, continue to 6b.

### 6b. Reviewer Agents (parallel)

Record exactly which files this section's agent changed:
```bash
git diff --name-only HEAD
```

Capture the diff using only those paths:
```bash
git diff HEAD -- <section-changed-files>
```

Spawn **two reviewer agents in parallel**:

**@rules-reviewer:**
> "Review this diff for any remaining convention violations or new problems introduced by the fix. Files changed: [list]. Output `LGTM` if the fix looks correct, or one issue per line using format: `[CONCERN] description — file:line`."
>
> ```
> [diff output]
> ```

**@bug-hunter:**
> "Review this diff for any bugs, regressions, or new logic errors introduced by the fix. Files changed: [list]. Output `LGTM` if the fix looks correct, or one issue per line using format: `[CONCERN] description — file:line | REVERT: reason` if the fix introduced a new problem that should be reverted."
>
> ```
> [diff output]
> ```

Evaluate combined reviewer output:

- Both output `LGTM` → **commit only the section's files**, mark issues `status: "completed"`:
  ```bash
  git add -- <section-changed-files>
  git commit -m "fix(<section>): [description] — fix-issues <runId>"
  ```
- One or both flag concerns but no `REVERT` → **commit only the section's files**, mark issues `status: "needs-review"`, set `reviewNote` to concerns
- Either reviewer outputs `REVERT: [reason]` → **revert this section's files**, mark issues `status: "failed"`

Write ledger update after each section's verdict.

---

## Step 7: Report

```
## Fix Issues — Run Complete

Run ID:       <runId>
Timestamp:    <ISO>
Branch:       <branch>
Baseline SHA: <sha before fixes>
End SHA:      <sha after fixes>

### This Run
- Issues claimed:              N
- Completed:                   K
- Needs review:                J
- Failed (pytest):             F
- Failed (reviewer revert):    R

### Completed
[For each: severity | section | agent used | description | file:line | commit SHA]

### Needs Review
[For each: severity | section | description | file:line | concern]

### Failed
[For each: severity | section | description | file:line | reason]

### Ledger Totals
- Pending:      X
- In-progress:  Y (other active runs)
- Completed:    Z
- Needs review: W
- Failed:       V
```

---

## Step 8: Signal for /loop

Output one of these signals on its own line:

- `ALL_ISSUES_RESOLVED` — no pending issues remain in the ledger
- `RUN_COMPLETE_NEEDS_ATTENTION` — some issues landed in `needs-review` or `failed`; human review recommended
- `RUN_COMPLETE_CONTINUE` — fixes applied and pending issues remain; safe to run again
