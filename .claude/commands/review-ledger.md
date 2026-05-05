---
description: Review of what the code-review-loop fixed — plain-language explanations of every change.
---

# Ledger Review

Read the ledger and explain every fix in plain English: what was wrong, why it mattered, what was changed, and what to check if something looks off.

## Step 1: Load the Ledger

```bash
cat scratch/code-review-loop/ledger.json 2>/dev/null || echo "[]"
```

If the ledger is empty or missing, say so and stop.

## Step 2: Load the Git Log for Context

```bash
git log --oneline -30
```

This gives you commit SHAs to cross-reference against ledger entries.

## Step 3: Produce the Report

Output the following report. Write every explanation as if talking to the developer who wrote the code — no jargon, no bullet soup, just clear sentences.

---

### Header

```
Code Review Loop — Report
Branch: <branch>
Generated: <current date/time>

Fixed:   N issues
Failed:  M issues (needs your attention)
Skipped: K issues (already handled in a prior run)
```

---

### Section 1: What Got Fixed

For each ledger entry with `status: "fixed"`, write a block like this:

```
── Fix #N ────────────────────────────────────────
Severity:  HIGH / MEDIUM / LOW
Category:  logging / bug / sql-injection / etc.
Commit:    abc1234  (run `git show abc1234` to see the exact diff)

What was wrong:
  <2-3 sentences in plain English. Not "violation of rule X" — explain what
  the code was actually doing wrong and why that's a problem in practice.>

What the fix did:
  <1-2 sentences. What specifically changed.>

Rule broken (if applicable):
  <Quote the relevant rule from .claude/rules/ if this was a convention fix.>
```

After all fixes, add:

```
To review all changes at once:
  git diff <first-fix-sha>~1..<last-fix-sha>
```

---

### Section 2: What Failed (Needs Your Review)

For each ledger entry with `status: "failed"`, write:

```
── Failed Fix #N ─────────────────────────────────
Severity:  <severity>
File:      <file>:<line>
Commit:    none — changes were reverted

What was attempted:
  <plain English description of the issue that was tried>

Why it failed:
  Tests failed after the fix was applied — the attempted fix broke something.
  The change was automatically reverted, so your codebase is unchanged for this item.

What to do:
  This one needs a fresh look. Run `/code-review-loop --dry-run` to see it
  in the triage plan, then fix it manually or start a new session focused
  on just this issue.
```

If there are no failures, say: "Nothing failed — the loop either fixed it or skipped it."

---

### Section 3: What Was Skipped

Keep this section brief. For each `status: "skipped"` entry, just list:

```
- [MEDIUM] Description — file:line  (was over the per-iteration fix cap; will be picked up next run)
```

If there are no skipped items, omit this section.

---

### Section 4: Anything to Watch Out For

After writing the three sections, call out any fixed items that deserve extra scrutiny:

- Fixes to files that also have uncommitted changes
- Multiple fixes to the same file (higher chance of interaction)
- Any CRITICAL-severity fix (always worth a manual read)
- Fixes whose commit SHAs don't appear in recent `git log`

If nothing warrants extra attention, say: "No items flagged for extra review."

---

### Footer

```
To dig into a specific fix:
  git show <commitSha>          — see exact diff
  git revert <commitSha>        — undo a specific fix if something looks wrong
  git log --oneline main...HEAD — full list of commits on this branch
```

---

## Step 4: Pending Issues — What To Do Next

After printing the report, scan the ledger for entries with `status: "pending"`.

If there are no pending issues, skip this section entirely.

If pending issues exist, count them and assess their complexity:
- **Total count**: how many pending entries
- **Severity mix**: how many are CRITICAL, HIGH, MEDIUM, LOW
- **Category breadth**: how many distinct categories/files are touched

**Recommendation logic:**

| Condition | Recommendation |
|-----------|----------------|
| ≤3 pending, all LOW/MEDIUM, same file/category | Spawn agents now — straightforward, low blast radius |
| 4–8 pending, mixed severity, multiple files | Write a handoff — context needed, safer to prep and hand off |
| >8 pending OR any CRITICAL | Write a handoff — too much to safely auto-fix in one shot |

Output:

```
── Pending Issues ────────────────────────────────
Count:       N pending
Complexity:  <brief characterization>

Recommendation: <spawn agents | write a handoff>
Reason: <one sentence explaining why>
```

Then ask the user:
> "Want me to **[recommended action]**, or would you prefer **[the other option]**?"

- If they say **spawn agents**: invoke `/fix-issues` and let it run.
- If they say **write a handoff**: produce a markdown handoff block they can paste into a new session.
- If they say something else, ask a clarifying follow-up.

**Handoff format:**

```
## Handoff — Pending Ledger Issues
Branch: <branch>
Date:   <today>

These issues are in `scratch/code-review-loop/ledger.json` with status "pending".
Run `/fix-issues` in a fresh session to pick them up, or address manually:

<for each pending issue>
- [SEVERITY] <description> — <file>:<line>  (category: <category>)
</for each>

Context: <1–2 sentences about anything the fixer should know>
```

---

## Step 5: Completion

When pending issues and decision issues are both resolved (or there were none), end with:

```
── Review Complete ───────────────────────────────
All ledger items reviewed.
Pending: handled (spawning agents | handoff written | none)

You're done. The branch is clean.
```
