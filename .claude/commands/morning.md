---
description: Morning check-in — surfaces yesterday's carry-forward, loads active roadmap context, and writes today's plan.
---

# Morning Check-in

You are running the morning planning session. Your job is to surface yesterday's carry-forward, load relevant context, have a brief conversation about today's plan, and write the morning section of today's check-in file.

## Setup

Get today's date:

!`date +%Y-%m-%d`

Note this as TODAY. All file paths below that reference TODAY use this value.

- `CHECKINS_DIR` = `/Users/jgfrussell/Git/luigi_app/docs/check-ins`
- `ROADMAPS_DIR` = `/Users/jgfrussell/Git/luigi_app/docs/roadmaps`
- `TODAY_FILE` = `/Users/jgfrussell/Git/luigi_app/docs/check-ins/TODAY.md` (substitute actual date)

## Step 1 — Check if today's file already exists

List all check-in files sorted by name:

!`ls /Users/jgfrussell/Git/luigi_app/docs/check-ins/*.md 2>/dev/null | sort | tail -1`

If the most recent file has today's date (TODAY) in its filename AND already contains a `## Morning` section, read it, show the user what's there, and stop — do not overwrite. Tell them to run `/eod` when the day is done.

## Step 2 — Load yesterday's context

The most recent file from Step 1 is yesterday's check-in (or the last day a check-in was done). Read it using the path from Step 1. Extract:
- The `**Active project:**` line from its `## Morning` section
- The `**Carry forward:**` bullet list from its `## EOD` section

If no file exists (first run), carry forward is empty and active project is "none".

## Step 3 — Load roadmap context and resolve Obsidian links

List the roadmaps directory:

!`ls /Users/jgfrussell/Git/luigi_app/docs/roadmaps/ 2>/dev/null`

For each project named in the active project field, find the roadmap file whose name most closely matches it. Record the **filename without the `.md` extension** — this becomes the Obsidian link target (e.g., `roadmap_eob_extraction`).

If active project is not "none":
- Read the matching roadmap file(s)
- Identify open phases: phases with unchecked `- [ ]` tasks and no `### Handoff` block
- Prepare a two or three sentence summary: what phase is active, what's left in it

If active project names multiple projects (e.g., "X + Y"), resolve and summarize each one.

## Step 4 — Brief the user and ask for the plan

Present:
1. **Carried from yesterday:** the carry-forward bullets (or "Nothing carried forward — fresh start." if first run or EOD had no carry-forward)
2. **Roadmap status:** (only if active project is set) the brief summary from Step 3, with roadmap names formatted as `[[filename-without-extension]]` so they render as Obsidian links
3. Then ask: "What's the plan for today?"

Wait for the user's response before continuing.

## Step 4.5 — Atomize compound tasks

After the user shares their plan, review each item. Identify tasks that are:
- **Compound** — contain "and" linking two distinct actions, or name multiple phases in one bullet
- **Vague** — the done state is unclear, or the first physical action isn't obvious (e.g., "figure out X", "look into Y", "identify Z")

For each such item, propose a breakdown using the goblin-tools style:
- Each sub-step is a single concrete action starting with an action verb
- Each sub-step has a clear, binary done state
- Sub-steps are sized to ~15–30 min of focused work
- No sub-step should itself need further decomposition

Present the proposed breakdown to the user. Example:

> **"Wire up extraction pipeline and test it against sample EOBs"** — I'd break this into:
> - Open `src/medical/extractors/` and confirm existing extractor entry point
> - Implement `extract_charges()` function
> - Wire return value into the claims pipeline
> - Run against sample EOB PDFs and capture results
> - Review failures and document any edge cases
>
> Does this look right, or do you want to adjust any of these?

Leave already-atomic items (a single action with a clear done state) untouched. Do not propose a breakdown for those.

Wait for the user to confirm or adjust the breakdowns before writing the file.

## Step 5 — Write today's morning section

After the user confirms the plan (with any atomized breakdowns), write `/Users/jgfrussell/Git/luigi_app/docs/check-ins/TODAY.md` (substitute actual date for TODAY). Create the `docs/check-ins/` directory first if it doesn't exist:

!`mkdir -p /Users/jgfrussell/Git/luigi_app/docs/check-ins`

For the **Active project** field, format each matched roadmap as an Obsidian wiki link using the filename without extension:
- Single project: `**Active project:** [[roadmap_eob_extraction]]`
- Multiple projects: `**Active project:** [[roadmap_eob_extraction]] + [[roadmap_medical_bill_tracking]]`
- If no roadmap file was matched, write the name as plain text

```
# YYYY-MM-DD

## Morning
**Active project:** [[roadmap-name-here]]
**Carried from yesterday:**
- [bullets from yesterday's carry forward, or "- Nothing carried forward."]

**Plan for today:**
- [confirmed atomic task list from Steps 4 and 4.5]
```

Confirm to the user: "Morning logged. Run `/eod` at the end of the day."

## Hard rules

- Never overwrite an existing morning section — check in Step 1 first
- Only load roadmaps when active project is not "none"
- Write the file only after the user has confirmed the atomized plan
- Active project carries forward automatically from the previous file — do not ask the user to re-state it unless they want to change it
- Roadmap Obsidian links go in the Active project line only — not inside individual plan bullets
- Do not break down items that are already atomic — only touch compound or vague tasks
