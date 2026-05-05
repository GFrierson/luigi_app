---
description: One-shot validation of a new roadmap against the codebase — file paths, patterns, conventions, architectural assumptions. Run once per roadmap before starting work.
argument-hint: "[roadmap-path]"
---

# Roadmap Conformance Check

You are validating a new roadmap before any phase runs. Your job is to catch mistakes that would compound across phases — references to files that don't exist, patterns that contradict the codebase, architectural assumptions that don't hold here. Run this ONCE per roadmap. After the first pass passes, never run it again.

## Setup

Parse `$ARGUMENTS` as `<roadmap-path>`. If missing, stop and ask.

Read the roadmap:

!`cat $1 2>/dev/null || echo "MISSING_ROADMAP"`

## Pre-flight checks

Stop if any of these are true:

1. **Roadmap missing.** The file doesn't exist.
2. **Roadmap is already in progress.** Any phase has a `### Handoff — Phase N` block. Conformance check is for new roadmaps only — once a phase has shipped, you've validated by execution. Tell the user to run `/phase-runner` for the next phase instead.
3. **Uncommitted changes on the branch.**

   !`git status --short`

## Workflow

### Step 1 — Run reviewers in parallel

Spawn these two reviews **in parallel**.

#### Review A — Codebase conformance

Use this prompt:

```
You are validating a roadmap against this Python codebase. Do not implement. Do not plan. Only check that the roadmap's references and assumptions match the actual codebase.

Roadmap: <roadmap-path>

Read the roadmap, then verify each of the following. For every issue you find, give the roadmap section and a one-line fix.

## Checks

1. **File paths exist.** For every file path the roadmap references (e.g., `src/medical/entities.py`), confirm it exists. Flag only references to existing files that don't actually exist. Proposed new files are fine.

2. **Patterns match conventions.** Read `.claude/rules/python/code-style.md` first. Then for every architectural pattern the roadmap proposes, check it against the rules:
   - DB schema changes go in `init_db()` in `src/database.py` (not a separate migration file)
   - Per-user SQLite at `data/{chat_id}.db`
   - New feature modules go in `src/{domain}/` (e.g., `src/medical/`)
   - Async handlers must use `asyncio.to_thread()` for sync DB calls
   - Parameterized SQL queries only (no string interpolation)
   - `logger = logging.getLogger(__name__)` at module level

3. **Library/dependency assumptions.** If the roadmap names a Python package, check `requirements.txt` to confirm it's listed. Flag assumed dependencies that aren't there.

4. **Reinvention check.** If the roadmap proposes building something the codebase already has (a DB helper, a parser, a pattern), flag it and point at the existing implementation.

5. **DB migration strategy.** If the roadmap adds new tables or columns, verify it uses the established `init_db()` pattern rather than proposing a separate migration system.

## Output format

For each issue:
- **Section:** [file-paths | patterns | dependencies | reinvention | migrations]
- **Severity:** [BLOCKER | FIX | NOTE]
  - BLOCKER: roadmap is wrong in a way that would cause Phase 1 to fail
  - FIX: should be corrected before starting
  - NOTE: not strictly wrong but worth flagging
- **Roadmap location:** quote the line or section
- **What's wrong:** one sentence
- **Fix:** concrete edit to the roadmap

If everything passes, say "No conformance issues found" and stop.
```

#### Review B — Adversarial plan review

Use this prompt:

```
You are reviewing this roadmap as a plan. Follow the /plan-review workflow — derive your own understanding first, then compare, then stress test. Output your standard verdict block.

Plan: contents of <roadmap-path>
Question being solved: stated in the roadmap's Goal section
Source docs: Read src/, ARCHITECTURE.md, tech_stack.md, and any docs/ files the roadmap references
```

### Step 2 — Synthesize

Combine findings from both reviewers. Deduplicate where they overlap.

Present in this format:

```
## Roadmap Conformance Report

### Codebase issues (Review A)
**BLOCKER** (N)
1. [Section] What's wrong — roadmap location
   Fix: [edit]

**FIX** (N)
2. ...

**NOTE** (N)
3. ...

### Plan-level issues (Review B)
**Verdict:** [from plan-review]
**Confidence:** [from plan-review]

[plan-review's critical issues, challenges, simpler alternatives, what couldn't be verified]

### Verdict: [Ready to start | Needs fixes | Rethink before starting]
[One-sentence summary]
```

#### Verdict guidelines

- **Ready to start** — Zero blockers, no plan-level "stop and rethink." Minor NOTEs are fine.
- **Needs fixes** — Has FIX-level issues or BLOCKERS that are mechanical to address.
- **Rethink before starting** — Plan-review verdict is "stop and rethink," OR the conformance issues reveal a structural problem with the roadmap's approach.

### Step 3 — Offer to apply fixes

After presenting the report, ask:

```
Want me to apply the BLOCKER and FIX edits to the roadmap now? (NOTEs are advisory — leave those for you to decide.)

[yes / no / show me each edit first]
```

- **yes** → edit the roadmap file directly, then re-run the conformance check on the edited version (one re-run only).
- **no** → exit. The user will edit the roadmap themselves.
- **show me each edit first** → present each proposed edit as a diff, ask for approval per edit, apply approved ones.

If the verdict is **Rethink before starting**, do NOT offer to apply fixes. Stop and let the user revise the roadmap structurally.

## Hard rules

- Run reviewers in parallel, not sequential. They check different things.
- Never run more than once on a clean roadmap, except the single auto-rerun after applying fixes.
- Never apply fixes without explicit user approval.
- Never apply NOTE-level changes — those are judgment calls for the user.
- If plan-review says "stop and rethink," do not offer to apply fixes.
- Do not validate phase-by-phase planning. That's `/phase-runner`'s job.
