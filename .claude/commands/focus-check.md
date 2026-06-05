---
description: Mid-day focus check — compares current activity to the morning MIT and asks if you're on track. Designed for `/loop /focus-check`.
---

# Mid-Day Focus Check

You are running a mid-day check-in. Your job is to quickly surface what the user committed to this morning, compare it against what they've actually been doing, and have a short focused conversation. This should take under 2 minutes.

## Setup

Get the current time and today's date:

!`date "+%Y-%m-%d %H:%M"`

Note TODAY (date portion) and NOW (HH:MM portion).

- `TODAY_FILE` = `/Users/jgfrussell/Git/luigi_app/docs/check-ins/TODAY.md` (substitute actual date)

## Step 1 — Read today's plan

!`ls /Users/jgfrussell/Git/luigi_app/docs/check-ins/*.md 2>/dev/null | sort | tail -1`

Read the file. If it doesn't exist or has no `## Morning` section, stop and tell the user to run `/morning` first.

Extract:
- `**Today's MIT:**` value
- `**Supporting tasks (if time):**` list
- Any existing `## Mid-day check` sections — note how many have run and whether any logged "drifted" status

## Step 2 — Gather recent activity (last 2 hours)

**Recent commits:**

!`git -C /Users/jgfrussell/Git/luigi_app log --oneline --since="2 hours ago" 2>/dev/null || echo "(no commits)"`

**Recently modified files:**

!`find /Users/jgfrussell/Git/luigi_app/src -name "*.py" -mmin -120 2>/dev/null | head -10 || echo "(none)"`

## Step 3 — Compare activity to MIT and ask

Based on Step 2, classify what the user has been doing:

- **On track** — recent commits or modified files relate to the MIT's domain/keywords
- **Drifted** — recent activity is clearly in a different area than the MIT
- **No activity** — no commits, no recently modified files (meetings, planning, blocked, or away)

Surface the comparison in 2–3 sentences. Then ask:

> "Your MIT is: **[MIT from morning plan]**
>
> Recent activity: [one-sentence characterization, or "No recent commits or file changes detected."]
>
> Are you (a) still on the MIT, (b) intentionally switched to something else, or (c) drifted and want to refocus?"

Wait for the user's response.

## Step 4 — Handle the response

**If (a) still on MIT:**
> "Great — keep going."
Append the log block (Step 5) and end the check.

**If (b) intentionally switched:**
Ask: "What did you switch to, and do you want to update the MIT for the rest of the day?"
- If they update the MIT, record the new MIT in the log block.
- If keeping the original, note the intentional detour and expected return.

**If (c) drifted:**
Do not judge. Ask: "Do you want to refocus on the MIT now, or is something blocking it?"
- If blocked: ask what's blocking and note it in the log.
- If refocusing: "What's your single next action to make progress on the MIT?" Get a concrete step.

## Step 5 — Append a mid-day check block

Append to today's file after the last existing section:

```
## Mid-day check — HH:MM
**Status:** [on track / intentionally switched / drifted — refocused / drifted — blocked]
**Activity:** [one-line characterization of what was being worked on]
**MIT:** [updated MIT if changed, otherwise "unchanged"]
**Note:** [what they switched to, what's blocking, next concrete action, or "n/a"]
```

No other output needed.

## Hard rules

- Never run without a `## Morning` section in today's file — stop and tell the user to run `/morning` first
- Never overwrite existing sections — always append
- Multiple checks per day are expected — each gets its own `## Mid-day check — HH:MM` block
- Keep the conversation short — this is a 2-minute check, not a planning session
- If the user is on track, do not extend the conversation
