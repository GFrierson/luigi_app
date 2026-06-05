---
description: End-of-day check-in — reviews today's MIT outcome, surfaces what derailed you, sets up tomorrow's first thing, and writes the EOD section.
---

# End-of-Day Check-in

You are running the end-of-day review. Your job is to read today's plan, gather git evidence, ask the user how the day went with focus on the MIT and what derailed them, then write the EOD section and surface todo sync candidates.

## Setup

Get today's date:

!`date +%Y-%m-%d`

Note this as TODAY.

- `TODAY_FILE` = `/Users/jgfrussell/Git/luigi_app/docs/check-ins/TODAY.md` (substitute actual date)

## Step 1 — Read today's morning section

!`ls /Users/jgfrussell/Git/luigi_app/docs/check-ins/*.md 2>/dev/null | sort | tail -1`

Read the file. If it doesn't exist or the most recent file doesn't have today's date, stop and tell the user to run `/morning` first.

If today's file already contains a `## EOD` section, show what's there and stop — do not append a second EOD section.

Extract:
- `**Active project:**` value
- `**Today's MIT:**` value
- `**Supporting tasks (if time):**` list
- Any `## Mid-day check` sections — note how many ran and whether any logged "drifted" status

## Step 2 — Gather git commits

**luigi_app:**
!`git -C /Users/jgfrussell/Git/luigi_app log --oneline --since="6am" 2>/dev/null || echo "(no commits)"`

Note a one-line characterization of what was committed.

## Step 3 — Load roadmap context (conditional)

If active project is not "none", find the matching roadmap file in `docs/roadmaps/`. Read it. Note the active phase and which tasks appear to have been touched by today's commits (by filename or topic match).

## Step 4 — Ask the user

Present:
1. **Today's MIT was:** (from morning plan)
2. **Git summary:** what committed today
3. **Mid-day checks:** (if any ran) how many, and whether any noted drift or intentional switches

Then ask these questions — ask naturally in a short conversation rather than all at once:

> 1. **Did the MIT land?** (landed / partial / didn't get to it)
> 2. **What happened with supporting tasks?** (quick rundown)
> 3. **What derailed you today, if anything?** (unplanned work, interruptions, context switches, energy crash, got blocked)
> 4. **What's the first thing you'll do tomorrow?** (single concrete action — this pre-fills tomorrow's MIT)
> 5. **Anything else to carry forward?**

**Coach-mode pattern note:** if the user had 2+ "drifted" mid-day checks, surface it lightly at the end: "Looks like today had a few context switches from the MIT — anything systematic blocking it, or was the MIT just not the right call?" Accept their answer. Don't belabor it.

Wait for the user's responses before writing the EOD section.

## Step 5 — Check todo for sync candidates

!`cat /Users/jgfrussell/Git/luigi_app/todo.md 2>/dev/null || echo "(no todo.md found)"`

Scan unchecked items (`- [ ]`) and compare against today's git commits and the user's account of what got done. Identify any items that today's work appears to have completed. Surface them:

> "Based on today's work, these todo items might be done — want to check them off?"
> - `[ ] item X`

Do not edit `todo.md`. Surface candidates only — the user updates the file themselves.

## Step 6 — Append the EOD section

Append to today's file (do not overwrite the `## Morning` section or any `## Mid-day check` sections):

```
## EOD
**MIT outcome:** [landed / partial / didn't get to it]
**MIT evidence:** [commit shas or files changed, or "no code touched"]

**Supporting tasks:**
- [task]: done / partial / not started
- ...

**Git summary:**
- luigi_app: [commit count + one-line characterization, or "(no commits)"]

**What derailed me (if anything):**
- [unplanned work / interruption / context switch / energy crash / blocked on X / nothing]

**Roadmap progress:**
- [only if active project is set: which phase tasks advanced, or "No roadmap progress today."]

**Tomorrow's first thing:** [single concrete action — pre-fills as tomorrow's MIT]

**Carry forward:**
- [bullets, or "- Nothing."]

**Todo sync candidates:**
- [items identified in Step 5, or "None identified."]
```

Confirm to the user: "EOD logged."

## Hard rules

- Never run without a `## Morning` section in today's file — Step 1 enforces this
- Only include the Roadmap progress section if active project is not "none"
- Append to the existing file — never overwrite `## Morning` or `## Mid-day check` sections
- Never append a second `## EOD` if one already exists today
- Do not edit `todo.md` — surface candidates only
