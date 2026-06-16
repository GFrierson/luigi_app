---
description: Review of what the code-review workflow found and fixed — plain-language explanations of every change, plus the human gate to skip/approve proposed tickets before running /fix-issues.
---

# Ledger Review

Read the ledger, explain every outcome in plain English, and give you a chance to **skip or adjust `proposed` tickets** before `/fix-issues` runs. This is the human gate between the review and fix workflows.

## Step 1: Load the Ledger

```bash
cat scratch/code-review-loop/ledger.json 2>/dev/null || echo "[]"
```

If the ledger is empty or missing, say so and stop.

## Step 2: Load the Git Log for Context

```bash
git log --oneline -30
```

Cross-reference commit SHAs against ledger entries.

## Step 3: Produce the Report

---

### Header

```
Code Review Ledger — Report
Branch:    <branch>
Generated: <current date/time>

Fixed:    N issues
Failed:   M issues (needs your attention)
Proposed: K tickets awaiting your review
Skipped:  J tickets (you marked these skip)
```

---

### Section 1: What Got Fixed

For each ledger entry with `status: "fixed"`, write a block:

```
── Fix #N ────────────────────────────────────────
Severity:  HIGH / MEDIUM / LOW
Category:  logging / bug / sql-injection / etc.
File:      src/database.py:42
Commit:    abc1234  (run `git show abc1234` to see the exact diff)

What was wrong:
  <2-3 sentences in plain English — not "violation of rule X" but what the
  code was actually doing wrong and why that's a problem in practice.>

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
Commit:    none — changes were reverted or never committed

What was attempted:
  <plain English description of the issue that was tried>

Why it failed:
  <the reason from reviewNote or note field>

What to do:
  Fix this manually or start a targeted new session for just this issue.
  Re-run /code-review-loop to re-detect it if needed.
```

---

### Section 3: Anything to Watch Out For

Call out any fixed items that deserve extra scrutiny:
- Fixes to files that also have uncommitted changes
- Multiple fixes to the same file (higher chance of interaction)
- Any CRITICAL-severity fix (always worth a manual read)
- Fixes whose commit SHAs don't appear in recent `git log`

If nothing warrants extra attention: "No items flagged for extra review."

---

### Footer

```
To dig into a specific fix:
  git show <commitSha>          — see exact diff
  git revert <commitSha>        — undo a specific fix if something looks wrong
  git log --oneline main...HEAD — full list of commits on this branch
```

---

## Step 4: Human Gate — Review Proposed Tickets

**This is the gate between `/code-review-loop` and `/fix-issues`.**

Scan the ledger for entries with `status: "proposed"`. If there are none, skip this section.

If proposed tickets exist, list them:

```
── Proposed Tickets — Your Review Needed ─────────

  #1  [HIGH]   src/database.py:42   logging   Missing exc_info=True on exception log
  #2  [MEDIUM] src/agent.py:88      bug       Off-by-one in retry count
  #3  [LOW]    tests/test_db.py:15  naming    Variable named 'temp' — too generic
  ...

Total: N proposed ticket(s)
```

Then ask:

> "Which of these (if any) do you want to **skip**? List ticket numbers (e.g. `1, 3`) or say `none` to approve all. Everything not skipped will be treated as approved and passed to `/fix-issues`."

Wait for the user's response.

- For each skipped ticket number: set `status → "skip"` in the ledger and write it back.
- Everything remaining as `proposed` is implicitly `approved` — `/fix-issues` will pick it up.

Confirm: `"Marked N ticket(s) as skip. K ticket(s) remain as proposed (approved for fixing)."`

If the user skips all tickets: `"All proposed tickets skipped — nothing to fix."` and stop.

---

## Step 5: Pending Next Steps

After the gate, suggest the next action:

```
── What's Next ───────────────────────────────────

K ticket(s) approved for fixing across sections: [section list]

Run `/fix-issues` to process them.
```

If the user wants to adjust a ticket's description/fix before running (e.g., "change the fix for #2 to..."), apply the edit to the ledger entry's `fix` field and write it back before they proceed.

---

## Step 6: Completion

```
── Review Complete ───────────────────────────────
Fixed:    N
Failed:   M
Skipped:  J
Approved: K (ready for /fix-issues)
```
