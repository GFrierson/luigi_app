---
description: Run a parallel multi-agent code review on the current branch
---

# Code Review

Run a comprehensive code review using parallel subagents, then synthesize findings.

## Scope

Determine what code to review:

1. **User specifies scope** — If the user provides:
   - A PR number: use `gh pr diff <number>` for the diff and `gh pr diff <number> --name-only` for changed files
   - A branch name: use `git diff main...<branch>`
   - File paths: review those files directly
2. **Branch diff** — Default to diff of current branch vs main.
3. Make sure there are no uncommitted changes on the branch. If there are, commit them.

Collect the changed files:

!`git diff --name-only main...HEAD`

## Pre-check

Stop and do not proceed if:
- The diff is empty (no changes vs main)
- The changes are trivial (only whitespace, comments, or auto-generated files)

## Launch Review Agents

Launch both agents **in parallel**. Each agent should review the diff between the current branch and main.

1. **@rules-reviewer** — Check the diff for violations of project conventions in `.claude/rules/python/`.

2. **@bug-hunter** — Scan the diff for bugs, logic errors, security issues, and runtime failures. Focus on changed code; use surrounding context only to validate findings.

## Synthesis

After all agents complete, synthesize the results into a single review.

### Deduplication

If multiple agents flagged the same issue, combine into one entry and note which agents caught it.

### Output Format

```
## Code Review Summary

### Issues Found (N total)

**CRITICAL / HIGH** (fix before merge)
1. [Category] Description — file:line
   Why: explanation
   Fix: suggestion

**MEDIUM** (strongly consider fixing)
2. [Category] Description — file:line
   Why: explanation
   Fix: suggestion

### All Clear
✓ Agent name — "No issues found"

### Verdict: [Ready to Merge | Needs Changes | Needs Discussion]
One sentence summary of what to do next.

### Triage Summary
- Quick fixes (N): #2, #3 — can be fixed in this session
- Deep fixes (N): #1 — recommend a fresh session per issue
```

### Verdict Guidelines

- **Ready to Merge** — No critical/high issues. Medium issues are minor and optional.
- **Needs Changes** — Has critical or high issues that should be fixed before merge.
- **Needs Discussion** — Has issues that require a judgment call or architectural discussion.

### Final Instruction

Present the synthesized review. Do NOT add generic advice like "consider adding more tests" or "looks good overall" — only report concrete findings from the agents.

## After Review

After presenting the verdict, ask: "Want me to fix the mechanical issues now? For complex bugs I'd recommend a fresh session per issue."
