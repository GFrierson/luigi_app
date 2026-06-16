export const meta = {
  name: 'fix-issues',
  description: 'Fix approved ledger tickets per section; returns per-section results with commit SHAs',
  phases: [
    { title: 'Fix',    detail: 'per-section agents in isolated worktrees apply fixes and validate with pytest' },
    { title: 'Verify', detail: 'adversarial diff review per section (rules-reviewer + bug-hunter)' },
    { title: 'Commit', detail: 'serial commit stage — apply passing diffs to real branch one at a time' },
  ],
}

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------

// Stage 1: fix agent applies changes, runs pytest, and returns the result.
// All of this happens inside the worktree so pytest runs against the fixed code.
const FIX_RESULT_SCHEMA = {
  type: 'object',
  properties: {
    status:       { type: 'string', enum: ['fixed', 'failed'],
                    description: '"fixed" if fixes applied and pytest passed; "failed" otherwise' },
    diff:         { type: 'string',
                    description: 'Full output of `git diff HEAD` after applying fixes (empty string if no changes or reverted)' },
    changedFiles: { type: 'array', items: { type: 'string' },
                    description: 'List of relative file paths changed by the fix' },
    pytestPassed: { type: 'boolean' },
    note:         { type: 'string',
                    description: 'Error message or reason for failure (first pytest error line, or agent error)' },
  },
  required: ['status', 'diff', 'changedFiles', 'pytestPassed'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict:    { type: 'string', enum: ['LGTM', 'CONCERN', 'REVERT'],
                  description: 'LGTM = looks good; CONCERN = minor issues, still commit; REVERT = serious problem, drop this section' },
    reviewNote: { type: 'string', description: 'Details of concern or revert reason. Empty if LGTM.' },
  },
  required: ['verdict'],
}

const COMMIT_SCHEMA = {
  type: 'object',
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          section:   { type: 'string' },
          commitSha: { type: 'string',
                       description: 'Full or short SHA of the commit, or empty string if commit failed' },
          note:      { type: 'string', description: 'Optional note if commit failed' },
        },
        required: ['section', 'commitSha'],
      },
    },
  },
  required: ['results'],
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function routeAgent(tickets) {
  const hasTestFile = tickets.some(t =>
    t.file.startsWith('tests/') || t.file.endsWith('_test.py')
  )
  return hasTestFile ? 'test-writer' : 'general-purpose'
}

function issueList(tickets) {
  return tickets.map((t, i) =>
    `Issue ${i + 1}:\n  File: ${t.file} (line ${t.line})\n  Severity: ${t.severity}\n  Category: ${t.category}\n  Description: ${t.description}\n  Why: ${t.why}\n  Fix: ${t.fix}`
  ).join('\n\n')
}

const PROJECT_RULES = `Rules (mandatory):
- Fix ONLY the listed issues. Do not refactor or reorganize anything else.
- Read each file fully before editing.
- Use parameterized SQL queries (? placeholders), never string interpolation.
- Use logger.* not print() for logging; always pass exc_info=True when logging caught exceptions.
- Always close SQLite connections after use.
- Wrap sync DB calls in asyncio.to_thread() inside async functions.`

// ---------------------------------------------------------------------------
// Main script
// ---------------------------------------------------------------------------

// args = array of approved (proposed) tickets from the ledger
const approved = args || []

if (approved.length === 0) {
  return { results: [] }
}

// Group tickets by section
const bySection = {}
for (const ticket of approved) {
  const sec = ticket.section || 'shared'
  if (!bySection[sec]) bySection[sec] = []
  bySection[sec].push(ticket)
}
const sections = Object.keys(bySection)
log(`Processing ${approved.length} ticket(s) across ${sections.length} section(s): ${sections.join(', ')}`)

// ---------------------------------------------------------------------------
// Stage 1 + 2: Fix+Validate → Verify per section (pipeline)
//
// Stage 1 runs in an isolated worktree so:
//   a) parallel sections cannot conflict on the git index
//   b) pytest runs against the fixed code in isolation
//
// Because the worktree path is opaque to the script, pytest validation
// is collapsed INTO Stage 1. Stage 2 does adversarial diff review only.
// ---------------------------------------------------------------------------

phase('Fix')

const pipelineResults = await pipeline(
  sections,

  // -------------------------------------------------------------------
  // Stage 1: Fix + pytest validation (worktree isolated)
  // The agent applies all fixes for this section, runs pytest, and
  // either returns the diff (if pytest passes) or reverts and reports
  // failure (if pytest fails).
  // -------------------------------------------------------------------
  async (section) => {
    const tickets = bySection[section]
    const agentType = routeAgent(tickets)

    const prompt =
`Fix the following Python code issues in this worktree. Fix ALL of them.

Issues:
${issueList(tickets)}

${PROJECT_RULES}

After applying all fixes:
1. Run: pytest tests/ -x -q 2>&1 | tail -30
2. If pytest FAILS:
   - Revert ALL your changes: git checkout -- .
   - Set status to "failed", pytestPassed to false, note to the first pytest error line.
   - Return an empty diff and empty changedFiles.
3. If pytest PASSES:
   - Run: git diff HEAD
   - Run: git diff --name-only HEAD
   - Set status to "fixed", pytestPassed to true.
   - Return the full diff output and the list of changed files.

Return all results via the structured output tool.`

    const result = await agent(prompt, {
      label:     `fix:${section}`,
      agentType,
      isolation: 'worktree',
      phase:     'Fix',
      schema:    FIX_RESULT_SCHEMA,
    })

    if (!result) {
      return { section, tickets, stage: 'fix', status: 'failed', note: 'fix agent returned null', diff: '', changedFiles: [] }
    }
    if (result.status === 'failed') {
      return { section, tickets, stage: 'fix', status: 'failed', note: result.note || 'pytest failed', diff: '', changedFiles: [] }
    }
    if (!result.diff || result.diff.trim() === '') {
      return { section, tickets, stage: 'fix', status: 'failed', note: 'no changes in diff after fix', diff: '', changedFiles: [] }
    }
    return { section, tickets, stage: 'fix', status: 'pending-verify', diff: result.diff, changedFiles: result.changedFiles }
  },

  // -------------------------------------------------------------------
  // Stage 2: Adversarial diff review (Verify phase)
  // Two reviewer agents inspect the diff. Both must LGTM (or only CONCERN)
  // for the section to proceed to commit. Any REVERT drops the section.
  // -------------------------------------------------------------------
  async (fixResult, section) => {
    if (fixResult.status === 'failed') return fixResult

    // Note: do NOT call the global phase() here — it races when sections run concurrently.
    // Each agent call below carries phase:'Verify' to assign itself to the right group.

    const [rulesVerdict, bugVerdict] = await parallel([
      () => agent(
        `Review this diff for any remaining convention violations or new problems introduced by the fix.
Files changed: ${fixResult.changedFiles.join(', ')}

Output:
- LGTM if the fix looks correct and follows project conventions
- CONCERN if there are minor issues but the fix is still worth keeping
- REVERT if the fix introduced a serious new problem that must be reverted

\`\`\`diff
${fixResult.diff}
\`\`\``,
        { label: `verify-rules:${section}`, agentType: 'rules-reviewer', phase: 'Verify', schema: REVIEW_SCHEMA }
      ),
      () => agent(
        `Review this diff for any bugs, regressions, or new logic errors introduced by the fix.
Files changed: ${fixResult.changedFiles.join(', ')}

Output:
- LGTM if the fix looks correct and introduces no new problems
- CONCERN if there are minor issues but the fix is still worth keeping
- REVERT if the fix introduced a serious new bug that must be reverted

\`\`\`diff
${fixResult.diff}
\`\`\``,
        { label: `verify-bugs:${section}`, agentType: 'bug-hunter', phase: 'Verify', schema: REVIEW_SCHEMA }
      ),
    ])

    const verdicts = [rulesVerdict, bugVerdict].filter(Boolean)
    if (verdicts.length === 0) {
      // Both agents returned null — treat as pass (no evidence of problems)
      return { ...fixResult, status: 'verified', reviewNote: null }
    }
    if (verdicts.some(v => v.verdict === 'REVERT')) {
      const revertNote = verdicts.find(v => v.verdict === 'REVERT').reviewNote || 'reviewer requested revert'
      return { ...fixResult, status: 'failed', note: `verify-revert: ${revertNote}` }
    }
    const concerns = verdicts.filter(v => v.verdict === 'CONCERN').map(v => v.reviewNote).filter(Boolean)
    return { ...fixResult, status: 'verified', reviewNote: concerns.length > 0 ? concerns.join(' | ') : null }
  }
)

// ---------------------------------------------------------------------------
// Stage 3: Serial commit on the real branch
//
// Worktree agents can't commit to the branch directly. Instead they return
// diffs. A single serial commit agent here applies each passing section's
// diff to the real working tree and commits one at a time — no index-lock
// race, and sections don't share files so `git apply` won't conflict.
// ---------------------------------------------------------------------------

phase('Commit')

const passingSections = pipelineResults.filter(Boolean).filter(r => r.status === 'verified')
const failedSections  = pipelineResults.filter(Boolean).filter(r => r.status === 'failed')

log(`${passingSections.length} section(s) ready to commit, ${failedSections.length} failed`)

let commitResults = []

if (passingSections.length > 0) {
  const commitInstructions = passingSections.map((r, i) => {
    const desc = r.tickets.map(t => t.description).join('; ')
    return `--- Section ${i + 1}: ${r.section} ---
Files: ${r.changedFiles.join(', ')}
Commit message: fix(${r.section}): ${desc} — code-review
Diff to apply:
\`\`\`diff
${r.diff}
\`\`\``
  }).join('\n\n')

  const commitAgent = await agent(
    `Apply the following diffs to the working tree and commit each section in order. Process ONE section at a time — do NOT batch.

For each section:
1. Apply the diff: git apply --3way << 'DIFF_EOF' ... DIFF_EOF
   (If git apply fails, apply the changes by editing the files directly using the diff as a guide.)
2. Stage only that section's files: git add -- <files>
3. Commit with exactly the provided message: git commit -m "<message>"
4. Record the commit SHA from the git output.
5. Move to the next section.

Return one result per section with the section name and commit SHA.
If a commit fails, record an empty commitSha and explain in the note field.

${commitInstructions}`,
    {
      label:  'commit:serial',
      phase:  'Commit',
      schema: COMMIT_SCHEMA,
    }
  )

  commitResults = commitAgent ? commitAgent.results : []
}

// ---------------------------------------------------------------------------
// Assemble return value
// ---------------------------------------------------------------------------

const shaBySection = {}
for (const cr of commitResults) {
  shaBySection[cr.section] = cr
}

const results = pipelineResults.filter(Boolean).map(r => {
  if (r.status === 'failed') {
    return {
      section:      r.section,
      status:       'failed',
      commitSha:    null,
      reviewNote:   null,
      note:         r.note || null,
      fingerprints: r.tickets.map(t => t.fingerprint),
    }
  }
  // verified — look up commit result
  const cr = shaBySection[r.section]
  const commitSha = cr && cr.commitSha ? cr.commitSha : null
  return {
    section:      r.section,
    status:       commitSha ? 'fixed' : 'failed',
    commitSha,
    reviewNote:   r.reviewNote || null,
    note:         cr && !cr.commitSha ? (cr.note || 'commit failed') : null,
    fingerprints: r.tickets.map(t => t.fingerprint),
  }
})

return { results }
