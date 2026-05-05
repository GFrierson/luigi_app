---
description: Reviewer loop — scans the branch for new issues and writes them to the ledger as 'pending'. Does not fix anything. Run with /loop for continuous review.
argument-hint: "[--dry-run]"
---

# Code Review Loop — Reviewer

Scan the branch for new issues and write them to the shared ledger as `pending`. Does not fix anything.

1. Run parallel review agents
2. Parse and deduplicate findings
3. Filter against existing ledger entries
4. Write new issues as `pending`
5. Report results

**Ledger location:** `scratch/code-review-loop/ledger.json`
**Pair with:** `/fix-issues` to process pending entries

---

## Step 0: Parse Arguments

Check `$ARGUMENTS` for:
- `--dry-run` → run the review and report what would be written, but do not modify the ledger

---

## Step 1: Pre-flight

```bash
git branch --show-current
git log --oneline -3
git status --short
```

Record the current HEAD SHA.

---

## Step 2: Load the Ledger

```bash
mkdir -p scratch/code-review-loop
cat scratch/code-review-loop/ledger.json 2>/dev/null || echo "[]"
```

Parse the JSON. Hold all existing fingerprints in memory for deduplication.

**Fingerprint formula:** `"{normalizedFile}:{floor(line/5)*5}:{category.toLowerCase()}"` where `normalizedFile` strips the leading project path, keeping only `src/...`, `tests/...`, or `scripts/...`.

**Section derivation:** Take the top-level directory component of the file path:

| section | covers |
|---|---|
| `src` | `src/*.py` (core modules) |
| `src/medical` | `src/medical/` |
| `tests` | `tests/` |
| `scripts` | `scripts/` |
| `shared` | everything else (config, root files) |

**Complexity derivation:**
- `quick` — category is one of: `logging`, `style`, `naming`, `imports`, `lint`
- `bug` — everything else, or severity is CRITICAL or HIGH regardless of category

---

## Step 3: Run Code Review Agents

Launch both agents **in parallel**. Each reviews `git diff main...HEAD`.

**Agent 1 — @rules-reviewer:**
> "Review the diff between main and HEAD for violations of project conventions in `.claude/rules/python/`. For each issue found, output a line in this exact format:
> `[SEVERITY] [CATEGORY] description — file:line | why: reason | fix: suggestion`
> where SEVERITY is CRITICAL, HIGH, MEDIUM, or LOW. One issue per line. If no issues, output `NONE`."

**Agent 2 — @bug-hunter:**
> "Scan the diff between main and HEAD for bugs, logic errors, security issues, and runtime failures. Focus on changed code only. For each issue found, output a line in this exact format:
> `[SEVERITY] [CATEGORY] description — file:line | why: reason | fix: suggestion`
> where SEVERITY is CRITICAL, HIGH, MEDIUM, or LOW. One issue per line. If no issues, output `NONE`."

Collect all lines from both agents. Filter out `NONE` lines.

---

## Step 4: Parse, Deduplicate, and Filter

### 4a. Merge across agents

Combine all issue lines. If two agents flagged the same `file:line`, merge into one entry and note which agents caught it.

### 4b. Parse each issue

For each line, parse:
- `severity` — CRITICAL, HIGH, MEDIUM, LOW
- `category` — the bracketed tag after severity (e.g., `logging`, `bug`, `sql-injection`)
- `description` — text before ` — `
- `file` — filename before `:line`
- `line` — line number (integer)
- `why` — text after `why: `
- `fix` — text after `fix: `

Derive `section` and `complexity` using the rules in Step 2.

### 4c. Filter against ledger

Compute each issue's fingerprint. If it matches **any** existing ledger entry (any status), drop it — it's already tracked.

### 4d. Report

Output: `"N new issues found, M already in ledger."`

If N = 0: output `"No new issues — ledger is current."` and stop.

---

## Step 5: Write to Ledger

If `--dry-run`: print what would be written and stop.

For each new issue, append a `pending` entry to the ledger:

```json
{
  "fingerprint": "src/database.py:40:logging",
  "description": "Missing exc_info=True on exception log",
  "severity": "MEDIUM",
  "category": "logging",
  "section": "src",
  "complexity": "quick",
  "why": "...",
  "fix": "...",
  "status": "pending",
  "claimedAt": null,
  "claimedBy": null,
  "fixedAt": null,
  "commitSha": null,
  "reviewNote": null,
  "iterationNote": null
}
```

Merge new entries with the existing array and write back:

```bash
cat > scratch/code-review-loop/ledger.json << 'LEDGER_EOF'
[...full updated JSON array...]
LEDGER_EOF
```

---

## Step 6: Report

```
## Code Review Loop — Scan Complete

Timestamp: <ISO>
Branch:    <branch>
HEAD SHA:  <sha>

Issues found by review:    N
Already in ledger:         M
New pending entries added: N

### New Issues
[For each: severity | section | complexity | description | file:line]

### Pending Backlog
Total pending: X issues across Y sections
[Breakdown by section: section → count]
```

---

## Step 7: Signal for /loop

Output one of these signals on its own line:

- `ALL_CLEAR` — N = 0, nothing new found
- `ISSUES_WRITTEN: N` — N new pending entries added; run `/fix-issues` to process them
