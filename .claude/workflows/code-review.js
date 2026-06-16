export const meta = {
  name: 'code-review',
  description: 'Fan-out review of the branch diff; returns verified tickets as proposed',
  phases: [
    { title: 'Find', detail: 'parallel bug-hunter + rules-reviewer on git diff main...HEAD' },
    { title: 'Verify', detail: 'adversarial 2-skeptic refutation per fresh finding' },
  ],
}

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------

const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file:        { type: 'string', description: 'Relative file path, e.g. src/database.py' },
          line:        { type: 'integer', description: 'Line number of the issue' },
          severity:    { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] },
          category:    { type: 'string', description: 'e.g. logging, bug, sql-injection, naming' },
          description: { type: 'string', description: 'Concise description of the issue' },
          why:         { type: 'string', description: 'Why this matters in practice' },
          fix:         { type: 'string', description: 'Concrete suggestion to fix it' },
        },
        required: ['file', 'line', 'severity', 'category', 'description', 'why', 'fix'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    refuted: { type: 'boolean', description: 'true if the finding is a false positive' },
    reason:  { type: 'string',  description: 'Brief explanation' },
  },
  required: ['refuted', 'reason'],
}

// ---------------------------------------------------------------------------
// Pure-JS helpers (run in the script, no filesystem access needed)
// ---------------------------------------------------------------------------

function normalizeFile(file) {
  // Strip leading project path segments; keep src/... tests/... scripts/...
  return file.replace(/^.*?(src\/|tests\/|scripts\/)/, '$1')
}

function fp(f) {
  const nf = normalizeFile(f.file)
  const bucket = Math.floor(f.line / 5) * 5
  return `${nf}:${bucket}:${f.category.toLowerCase()}`
}

function sectionOf(file) {
  const nf = normalizeFile(file)
  if (nf.startsWith('src/medical/')) return 'src/medical'
  if (nf.startsWith('src/'))         return 'src'
  if (nf.startsWith('tests/'))       return 'tests'
  if (nf.startsWith('scripts/'))     return 'scripts'
  return 'shared'
}

function complexityOf(category, severity) {
  if (severity === 'CRITICAL' || severity === 'HIGH') return 'bug'
  const quick = ['logging', 'style', 'naming', 'imports', 'lint']
  return quick.includes(category.toLowerCase()) ? 'quick' : 'bug'
}

function toTicket(f) {
  const fingerprint = fp(f)
  return {
    file:        f.file,
    line:        f.line,
    fingerprint,
    description: f.description,
    severity:    f.severity,
    category:    f.category,
    section:     sectionOf(f.file),
    complexity:  complexityOf(f.category, f.severity),
    why:         f.why,
    fix:         f.fix,
    status:      'proposed',
    fixedAt:     null,
    commitSha:   null,
    reviewNote:  null,
  }
}

// ---------------------------------------------------------------------------
// Finder dimensions
// ---------------------------------------------------------------------------

const DIFF_PROMPT = `Review \`git diff main...HEAD\` to find issues in the changed code.

Run: git diff main...HEAD

Then inspect each changed file as needed for context. Return your findings as structured JSON — one entry per distinct issue. If you find no issues, return an empty findings array.

Requirements for each finding:
- file: relative path (e.g. src/database.py)
- line: specific line number in the current HEAD version
- severity: CRITICAL / HIGH / MEDIUM / LOW
- category: a short tag (e.g. logging, bug, sql-injection, naming, style, imports, lint)
- description: concise statement of what is wrong
- why: why this matters in practice (runtime failure, data loss, rule violation, etc.)
- fix: concrete suggestion to correct it`

const DIMS = [
  {
    key:       'bugs',
    agentType: 'bug-hunter',
    prompt:    `You are hunting for bugs, logic errors, security issues, and runtime failures.\n\n${DIFF_PROMPT}`,
  },
  {
    key:       'rules',
    agentType: 'rules-reviewer',
    prompt:    `You are checking for violations of the project conventions in .claude/rules/python/. Read those rule files first.\n\n${DIFF_PROMPT}`,
  },
]

// ---------------------------------------------------------------------------
// Main script
// ---------------------------------------------------------------------------

const seen = new Set()  // fingerprints of all confirmed tickets across rounds
const tickets = []
let dry = 0             // consecutive rounds with nothing new

while (dry < 1) {
  phase('Find')

  // Run both finders in parallel
  const results = (await parallel(
    DIMS.map(d => () =>
      agent(d.prompt, {
        label:     `find:${d.key}`,
        agentType: d.agentType,
        phase:     'Find',
        schema:    FINDINGS_SCHEMA,
      })
    )
  )).filter(Boolean)

  const allFindings = results.flatMap(r => r.findings)
  const fresh = allFindings.filter(f => !seen.has(fp(f)))

  if (fresh.length === 0) {
    dry++
    log(`Round complete — no new findings (dry=${dry}/1)`)
    continue
  }
  dry = 0
  log(`Found ${fresh.length} new finding(s) to verify`)

  // Dedup fresh within this round (same fingerprint from both agents)
  const dedupedFresh = []
  const roundSeen = new Set()
  for (const f of fresh) {
    const key = fp(f)
    if (!roundSeen.has(key)) {
      roundSeen.add(key)
      dedupedFresh.push(f)
    }
  }

  phase('Verify')

  // Adversarial verify: 2 skeptics per finding, majority survives (>=1 of 2 not-refuted)
  const verifications = await parallel(
    dedupedFresh.map(f => () =>
      parallel([
        () => agent(
          `Try to refute this code-review finding. Default to refuted=true if uncertain.\n\nFinding:\nFile: ${f.file}:${f.line}\nSeverity: ${f.severity}\nCategory: ${f.category}\nDescription: ${f.description}\nWhy: ${f.why}\nFix: ${f.fix}`,
          { label: `verify:${fp(f)}:1`, phase: 'Verify', schema: VERDICT_SCHEMA }
        ),
        () => agent(
          `Try to refute this code-review finding. Default to refuted=true if uncertain.\n\nFinding:\nFile: ${f.file}:${f.line}\nSeverity: ${f.severity}\nCategory: ${f.category}\nDescription: ${f.description}\nWhy: ${f.why}\nFix: ${f.fix}`,
          { label: `verify:${fp(f)}:2`, phase: 'Verify', schema: VERDICT_SCHEMA }
        ),
      ]).then(votes => {
        const notRefuted = votes.filter(Boolean).filter(v => !v.refuted).length
        return { f, survives: notRefuted >= 1 }
      })
    )
  )

  for (const result of verifications.filter(Boolean)) {
    if (result.survives) {
      seen.add(fp(result.f))
      tickets.push(toTicket(result.f))
    }
  }

  log(`Verified: ${tickets.length} ticket(s) total so far`)

  // Stop early if we've hit the budget ceiling
  if (budget.total && budget.remaining() < 50000) {
    log('Budget nearly exhausted — stopping early')
    break
  }
}

return { tickets }
