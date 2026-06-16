---
description: Run a parallel multi-agent code review on the current branch, then ground and verify findings before reporting.
---

# Code Review

Review the current branch with parallel subagents, then synthesize, verify, and ground the findings into a single review.

## Scope

Determine what to review:

1. **User specifies scope:**
   - PR number: `gh pr diff <number>` for the diff, `gh pr diff <number> --name-only` for changed files
   - Branch name: `git diff main...<branch>`
   - File paths: review those files directly
2. **Default:** diff of current branch vs main.
3. Ensure no uncommitted changes on the branch. If there are, commit them first.

Changed files:

!`git diff --name-only main...HEAD`

## Pre-check

Stop and do not proceed if:
- The diff is empty (no changes vs main)
- The changes are trivial (only whitespace, comments, or auto-generated files)

## Launch Review Agents

Launch both **in parallel**, each reviewing the diff between the current branch and main:

1. **@rules-reviewer** — convention violations against `.claude/rules/python/`.
2. **@bug-hunter** — bugs, logic errors, security issues, runtime failures.

Each agent returns findings with a **Grounded in** basis and a graded fix:
- **Verified from diff** — the fix is confirmed against the changed code.
- **Proposed — verify against <source>** — the fix depends on ground truth the agent did not open.
- **Symptom confirmed, cause uncertain** — a real symptom with no pinned cause.

## Verify & Ground (your job, after the agents return)

This is the core of the command. The agents detect; you ground.

**Dedup first.** If multiple agents flagged the same issue, combine into one entry and note which agents caught it.

**Pass through, don't re-verify:** findings marked *Verified from diff* are already grounded — accept their fix as-is. Don't spend a round-trip re-checking them.

**Ground everything else.** For each finding marked *Proposed — verify* or *Symptom, cause uncertain*, do the grounding the agent deferred and **author the fix yourself** from what you find. Open the source the agent named and:

- **Arithmetic / validation / reconciliation:** reconcile against the source document's *own* definition — the actual EOB, the fixture, its expected values — not against what the code implies the identity "should" be. If the agent's proposed fix contradicts the document, discard it and write the correct one.
- **Single failing fixture:** determine whether the failure is case-specific (one insurer, one schema) before changing anything shared. If specific, the fix is per-profile config, not a changed shared formula.
- **Convention fix touching migrations / compat shims / defensive guards:** confirm whether pre-existing state depends on the code before recommending removal. Prefer narrowing (a specific `except`) over deletion. Never let a fix violate another rule in the rule set.
- **Rule intent:** a satisfied `Why:` or an applicable `Exception:` means no violation; an `[INVARIANT]` admits none.

Two honest outcomes are expected:
- **Downgrade/dismiss** a finding if grounding shows it isn't a real problem (e.g., the arithmetic reconciles once you read the document). Report these — don't drop them silently.
- **Leave it open** if you genuinely cannot ground it without data or execution you don't have. Say so plainly; do not guess a fix.

## Output Format
```
Code Review Summary
Reviewed: <scope — branch/PR/files>
Issues Found (N total)
CRITICAL / HIGH (fix before merge)

[Category] Description — file:line  (caught by: <agent(s)>)

Why: concrete failing scenario

Grounded in: <diff | fixture X + expected values | rule Z + migration history>

Fix: <the fix> — [Verified from diff | Verified by review | Needs your call]

MEDIUM (strongly consider)

...
LOW

...
Downgraded on Verification (N)

[original finding] — why it's not a problem after grounding against <source>

All Clear
✓ <Agent> — "No issues found"
Verdict: [Ready to Merge | Needs Changes | Needs Discussion]
One sentence on what to do next.
Triage

Auto-applicable (verified from diff): #N, #N — mechanical, safe to apply now
Verified by review (grounded, eyeball first): #N
Needs your call (judgment / architecture): #N — see note
```

## Verdict Guidelines

- **Ready to Merge** — no critical/high issues; medium issues minor/optional.
- **Needs Changes** — has critical or high issues to fix before merge.
- **Needs Discussion** — issues that need a judgment call or architectural decision.

## Final Instruction

Present the synthesized, verified review. Do NOT add generic advice ("consider more tests," "looks good overall") — only concrete, grounded findings. Every fix carries its verification status; never present a grounded-uncertain fix as if it were verified from the diff.

## After Review

Offer, gated on groundedness — not on whether a fix "looks mechanical":

"Want me to apply the auto-applicable fixes (verified from the diff) now? The verified-by-review fixes I'd suggest you eyeball first; the judgment calls need a decision before I touch them. For complex bugs I'd recommend a fresh session per issue."