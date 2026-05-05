---
description: Run a single phase of a roadmap end-to-end — plan, review plan, implement, verify, write handoff. Stops after one phase.
argument-hint: "[roadmap-path] [phase-number]"
---

# Phase Runner

You are running a single phase of a multi-phase roadmap. Your job is to take ONE phase from "decided to build" to "implemented, verified, handed off." Stop after the phase completes. The user will invoke you again for the next phase.

## Setup

Parse `$ARGUMENTS` as `<roadmap-path> <phase-number>`. If either is missing or ambiguous, stop and ask.

Read the roadmap:

!`cat $1 2>/dev/null || echo "MISSING_ROADMAP"`

## Pre-flight checks

Stop and surface to the user if any of these are true:

1. **Roadmap missing.** The file doesn't exist.
2. **Phase missing.** No `## Phase N` header matches the requested phase.
3. **Phase already complete.** The phase has a `### Handoff — Phase N` block, OR all task checkboxes are `[x]`. Ask the user to confirm a re-run.
4. **Prior phases incomplete.** Phase N-1 has unchecked tasks AND no handoff block. Warn the user and ask whether to proceed anyway.
5. **Uncommitted changes on the branch.**

   !`git status --short`

   If there are uncommitted changes, stop and ask the user to commit, stash, or confirm.

## Workflow

Run these steps in order. Do not parallelize.

### Step 1 — Plan the phase

Spawn a `general-purpose` agent with this prompt:

```
You are planning ONE phase of a roadmap. Do not implement — only plan.

Roadmap: <roadmap-path>
Phase: Phase N

## Your task
1. Read the full roadmap to understand the architecture and prior decisions
2. Focus only on Phase N — its tasks, deliverable, and "what's true when done"
3. Produce an implementation plan for Phase N covering:
   - Order of operations (which tasks first, where they connect)
   - Which files to create or modify (exact paths)
   - Which patterns from existing code to follow (read src/ to identify them)
   - What DB migrations or schema changes are needed (if any)
   - What integration test scenarios to cover

## Output format
Return a numbered implementation plan. For each step:
- What to do
- Which file(s)
- Which existing file to use as a pattern reference

Keep the plan focused on Phase N only. Do not plan future phases.
```

Present the plan to the user. Ask: "Does this plan look right? Say yes to proceed, or tell me what to adjust."

Wait for user confirmation before continuing.

### Step 2 — Review the plan

After user approval, spawn a `general-purpose` agent running the `/plan-review` command logic:

```
You are reviewing an implementation plan as a skeptical staff engineer.

Follow the /plan-review workflow:
1. Derive your own understanding of the problem from the roadmap
2. Compare against the proposed plan
3. Stress test for silent failures, bad assumptions, and reinvented wheels

Plan: [paste the plan from Step 1]
Roadmap: <roadmap-path>
Source docs: Read src/ and any docs/ files referenced in the roadmap
```

If the plan-review verdict is **stop and rethink**: surface the findings to the user and stop. Let them revise the plan before proceeding.

If the verdict is **proceed** or **proceed with changes**: note the changes and continue.

### Step 3 — Implement

Spawn a `@senior-dev` agent with this prompt:

```
You are implementing Phase N of a roadmap. The plan has been reviewed and approved.

Roadmap: <roadmap-path>
Phase: Phase N
Approved plan: [paste the plan from Step 1, incorporating any changes from Step 2]

## Your task
Execute the implementation plan exactly. For each step:
1. Read the files you need to understand before modifying
2. Make the change
3. After all changes, run: pytest tests/ -x -q

## Rules
- Follow the patterns in .claude/rules/python/
- DB schema changes go in init_db() in src/database.py using idempotent migrations
- All new functions need type hints
- New DB helpers get tests in tests/
- Do not implement anything from other phases

## Done when
- All Phase N tasks are implemented
- pytest tests/ -x -q passes
- Output: PHASE_COMPLETE
```

Monitor the agent. If it hits blockers or asks clarifying questions, relay them to the user.

### Step 4 — Verify

After the agent completes, run verification yourself:

```bash
pytest tests/ -x -q
```

If pytest fails:
- Identify the failure
- Spawn a targeted `general-purpose` agent to fix it, or fix it yourself if it's trivial
- Re-run pytest until it passes

Also do a manual spot-check:
- Read each new file the agent created
- Verify it matches the plan and follows conventions
- Check that `init_db()` was updated for any new tables

### Step 5 — Update roadmap checkboxes

For each task in Phase N that is now complete, update the roadmap file to mark it `[x]`:

```bash
# For each task, change "- [ ] task text" to "- [x] task text"
```

### Step 6 — Write handoff

Append a handoff block to the roadmap under Phase N:

```markdown
### Handoff — Phase N
**Completed:** <date>
**Branch:** <current branch>
**Tests:** pytest tests/ -x -q passes

#### What was built
[2-3 sentences describing what Phase N delivered]

#### Files changed
[List each new or modified file with one-line description]

#### How to verify manually
[Concrete steps to verify the feature works — what to run, what to check]

#### Open questions / deferred decisions
[Anything that came up during implementation that Phase N+1 should know about]
```

### Step 7 — Commit

Stage and commit all changes:

```bash
git add -p  # or specific files
git commit -m "feat: implement roadmap phase N — <short description>"
```

Present the commit to the user for confirmation before committing.

### Step 8 — Report

Output:

```
## Phase N Complete

**Roadmap:** <roadmap-path>
**Phase:** N — <phase name>
**Commit:** <sha>

### Implemented
[List each roadmap task with [x] checkbox]

### Test results
pytest tests/ -x -q: X passed

### What's next
Phase N+1: <phase name> — run `/phase-runner <roadmap-path> <N+1>` to continue.
```

---

## Hard rules

- Never implement tasks from a future phase.
- Never skip the plan or plan-review steps.
- Never commit without user confirmation.
- If pytest fails at the end of Step 4 and you can't fix it in 2 attempts, stop and surface the failure to the user.
- The handoff block is mandatory — don't skip it.
