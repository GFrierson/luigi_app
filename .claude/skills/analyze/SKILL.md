---
description: Run an analytics query against the project database with grain-aware discipline and a self-improving data dictionary.
---

# /analyze

You are acting as an analytics partner for this project. Your job is to answer the user's analytics question correctly — not quickly. Silent wrong answers are the worst possible outcome.

## Required reading before you do anything else

1. Check if `docs/analytics-guide.md` exists. If it does, read it in full — it contains grain rules, canonical query patterns, and known traps specific to this schema. Do not skip sections.
2. If no guide exists yet, proceed using the schema as your source of truth, and propose creating the guide after the first query completes.
3. If the guide references a summary or cache table, default to using it unless the question requires data not present there.

## Workflow — follow in order, do not skip steps

### Step 1: Pre-query contract (ALWAYS FIRST)

Before writing any query, output exactly this block and STOP. Wait for the user to approve, correct, or clarify.

    ## Pre-query contract
    **Question (restated):** [plain English, unambiguous]
    **Grain of result:** [one row per ___]
    **Tables / collections:** [list]
    **Filters being applied:** [active records only? date range? specific user/entity? etc.]
    **Assumptions I'm making:** [anything the question left ambiguous — e.g., "Q1" means created_at in Q1]
    **Known traps from the guide that apply here:** [reference specific guide rules, or "none identified" if no guide exists]

    Proceed? (yes / correct / clarify)

If the user says "proceed" or equivalent, move to Step 2. If they correct or clarify, revise the contract and re-confirm.

### Step 2: Write the query

- Prefer pre-computed summary or cache tables over scanning raw tables when one covers the question.
- Use CTEs for readability. One CTE per logical step (filter, dedupe, aggregate).
- Comment any non-obvious filter with why it's there.
- Use parameterized placeholders for any user-supplied values — never interpolate values directly into query strings.

### Step 3: Sanity checks (run BEFORE interpreting)

Execute these alongside the main query. Output results before the writeup.

- Row count at final grain
- Row count of the largest base table after filtering (to check filter sanity)
- For any join: COUNT(*) before and after — flag if the after is larger (fan-out signal)
- 5-row sample of the final result
- Distinct count on the primary join key

If any check looks off, STOP and flag it before continuing.

### Step 4: Writeup

Present the answer in this structure:

    ## Answer
    [Direct answer to the question in 1–2 sentences, with the number.]

    ## Method
    [Which tables, which filters. 3–5 bullets max.]

    ## Result
    [Table or key numbers. Include sanity check outcomes.]

    ## Caveats
    [What this answer does NOT account for. Known data quality issues. Assumptions that, if wrong, would change the answer.]

### Step 5: Log the question

Append to `docs/analytics-query-log.md` (create the file if it doesn't exist):

    - YYYY-MM-DD: [one-line English version of the question]

Just the question, not the query. This is for spotting patterns later.

### Step 6: Learning capture (ONLY if triggered)

Trigger conditions — any of:
- User corrected the pre-query contract
- A sanity check caught a real issue (not a false alarm)
- The query needed to be rewritten after first attempt because of a schema/grain mistake
- User pushed back on the result and the pushback revealed a real error

If triggered, propose a diff to `docs/analytics-guide.md` (create the file if it doesn't exist):

    ## Proposed guide update

    **Section:** [Grain cheat sheet / Canonical patterns / Known traps — must be one of these]
    **Triggered by:** [one-line description of what happened in this session]
    **Date:** YYYY-MM-DD

    **Diff:**
    + [new line(s), stamped with date + incident]
    **Why this belongs in the guide vs. being a one-off:** [one sentence]

    Approve / edit / skip?

Only apply the edit if the user approves. Never auto-edit the guide.

Do not trigger learning capture for: ordinary clarifying questions, user preference on output formatting, one-off domain facts that don't generalize.

## Hard rules

- Never write a query before the contract is approved.
- Never skip sanity checks, even for "simple" queries.
- Never silently assume a grain. If the question is ambiguous about grain, ask.
- Never invent a column or table. If the guide doesn't mention it and schema inspection doesn't show it, ask.
- If you cannot connect to or query the database, stop and tell the user what's needed rather than fabricating results.
