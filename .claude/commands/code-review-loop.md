---
description: Reviewer loop — fans out review agents, adversarially verifies findings, and writes proposed tickets to the ledger. Run once; loop-until-dry lives inside the workflow.
argument-hint: "[--dry-run]"
---

# Code Review Loop — Reviewer

Fan out `bug-hunter` and `rules-reviewer` against `git diff main...HEAD`, adversarially verify each finding, and write new tickets to the shared ledger as `proposed`.

Does not fix anything. Pair with `/review-ledger` (human gate) then `/fix-issues`.

**Ledger location:** `scratch/code-review-loop/ledger.json`

---

## Step 0: Parse Arguments

Check `$ARGUMENTS` for:
- `--dry-run` → run the workflow and report what would be written, but do not modify the ledger.

---

## Step 1: Pre-flight

```bash
git branch --show-current
git log --oneline -3
git status --short
```

---

## Step 2: Run the Review Workflow

**Call the Workflow tool** with script path `.claude/workflows/code-review.js`.

Pass no `args` (the workflow reads the git diff itself).

The workflow runs in the background and returns `{ tickets }` — an array of verified ticket objects with `status: 'proposed'`. Each ticket has:
- `file`, `line` — first-class fields (not just embedded in fingerprint)
- `fingerprint` — `{normalizedFile}:{floor(line/5)*5}:{category.toLowerCase()}`
- `description`, `severity`, `category`, `section`, `complexity`, `why`, `fix`
- `status: 'proposed'`
- `fixedAt: null`, `commitSha: null`, `reviewNote: null`

Wait for the workflow to complete before proceeding.

---

## Step 3: Load Ledger and Dedup

```bash
mkdir -p scratch/code-review-loop
cat scratch/code-review-loop/ledger.json 2>/dev/null || echo "[]"
```

Hold all existing fingerprints (any status) in a Set. From the workflow's returned `tickets`, filter out any whose `fingerprint` already appears in the ledger — they are already tracked.

Report: `"N new tickets returned, M already in ledger (skipped), K to write."`

If K = 0: output `"No new issues — ledger is current."` and emit `ALL_CLEAR`.

---

## Step 4: Write to Ledger

If `--dry-run`: print the new tickets and stop.

Merge the new (deduplicated) tickets with the existing array and write back:

```bash
cat > scratch/code-review-loop/ledger.json << 'LEDGER_EOF'
[...full updated JSON array...]
LEDGER_EOF
```

---

## Step 5: Report

```
## Code Review Loop — Scan Complete

Timestamp: <ISO>
Branch:    <branch>
HEAD SHA:  <sha>

New tickets written: K  (status: proposed)
Already tracked:     M
Workflow passes:     <number of find→verify rounds run>

### New Proposed Tickets
[For each: severity | section | complexity | file:line | description]

### Proposed Backlog
Total proposed: X tickets across Y sections
[Breakdown by section: section → count]
```

---

## Step 6: Signal for /loop

Output one of these signals on its own line:

- `ALL_CLEAR` — K = 0, nothing new found
- `ISSUES_WRITTEN: K` — K new proposed tickets added; run `/review-ledger` then `/fix-issues`
