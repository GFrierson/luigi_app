---
description: Fixer — runs approved ledger tickets through per-section worktree agents, validates with pytest and adversarial review, then commits passing sections to the branch.
argument-hint: "[--dry-run]"
---

# Fix Issues

Process `approved` tickets from the ledger. Each section is fixed in an isolated worktree (bad sections can't break each other's pytest), then verified, then committed serially to the real branch.

**Ledger location:** `scratch/code-review-loop/ledger.json`
**Pair with:** `/code-review-loop` (to populate the ledger) → `/review-ledger` (human gate) → here

---

## Step 0: Parse Arguments

Check `$ARGUMENTS` for:
- `--dry-run` → load and report what would be fixed, but do not run the workflow or modify anything.

---

## Step 1: Pre-flight

```bash
git branch --show-current
git log --oneline -3
git status --short
```

If there are uncommitted changes, **stop** — do not auto-commit them. Ask the user to commit or stash first.

---

## Step 2: Load Ledger and Select Tickets

```bash
cat scratch/code-review-loop/ledger.json 2>/dev/null || echo "[]"
```

Select tickets where `status === 'proposed'`. The human gate in `/review-ledger` marks unwanted tickets as `skip`; everything still `proposed` is implicitly approved for fixing.

If 0 candidates: output `"No proposed tickets to fix."` and stop.

Report: `"N proposed tickets across sections: [list]."`

If `--dry-run`: print the selected tickets and stop.

---

## Step 3: Run the Fix Workflow

**Call the Workflow tool** with script path `.claude/workflows/fix-issues.js`, passing the approved tickets as `args` (the full array of ticket objects).

The workflow:
1. Groups tickets by `section`
2. Runs `pipeline(sections, fix, validate, verify)` — each section isolated in a worktree
3. Commits all passing sections serially to the real branch

Wait for the workflow to complete before proceeding. It returns:
```json
{
  "results": [
    {
      "section": "src",
      "status": "fixed",
      "commitSha": "abc1234",
      "reviewNote": null,
      "note": null,
      "fingerprints": ["src/database.py:40:logging"]
    }
  ]
}
```

---

## Step 4: Update Ledger

Build a fingerprint → result map from the returned results. For each ledger ticket whose `fingerprint` appears in the results:

| Workflow result status | Ledger update |
|---|---|
| `fixed` | `status → "fixed"`, `commitSha → <sha>`, `fixedAt → <ISO now>`, `reviewNote → <note if any>` |
| `failed` | `status → "failed"`, `reviewNote → <note>` |

Write the updated ledger back.

---

## Step 5: Report

```
## Fix Issues — Run Complete

Timestamp:  <ISO>
Branch:     <branch>

### This Run
- Tickets processed:   N
- Fixed:               K
- Failed:              F

### Fixed
[For each: severity | section | file:line | description | commit SHA]

### Failed
[For each: severity | section | file:line | description | reason]

### Ledger Totals
- Proposed:  X
- Approved:  Y
- Fixed:     Z
- Failed:    W
- Skipped:   V
```

---

## Step 6: Signal

Output one of these on its own line:

- `ALL_ISSUES_RESOLVED` — no proposed or approved tickets remain
- `RUN_COMPLETE_NEEDS_ATTENTION` — some tickets landed in `failed`; human review needed
- `RUN_COMPLETE_CONTINUE` — fixes applied and proposed tickets still remain; run `/code-review-loop` again or adjust with `/review-ledger`
