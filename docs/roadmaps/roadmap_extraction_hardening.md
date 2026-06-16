# Roadmap: Extraction Hardening — Grounding, Eval, Table Robustness

**Goal:** Fold three learnings from the Document Intelligence series into the existing pipeline — a grounded LLM fallback, a per-failure-mode eval harness, and adaptive table parsing — adapted to our local/PHI constraints.
**Depends on:** Main EOB pipeline (`parse_table` from P2, the LLM fallback from P3) for touchpoints; a local second OCR engine for Workstream C.
**Estimated scope:** Days
**Track:** Three independent workstreams, applied opportunistically — none blocks another; each is picked up when its touchpoint is being worked.
**Status:** Not started
**Last updated:** June 4, 2026

> Local-only adaptation: no embeddings or vector store enter the stack; any "second engine" is local (PaddleOCR/PP-Structure or RapidOCR), never a cloud parser.

---

## Workstream A — Grounded LLM vision fallback
*Touchpoint: main roadmap Phase 3. "Grounded, not augmented": the model transcribes only what's on the page; parametric memory is allowed for formatting/schema/arithmetic-on-cited-values, never for inventing facts.*

**Done when:** the vision fallback emits only values it can point to on the page, each value carries a page citation, and any ungrounded value is flagged before it reaches the user.

- [x] Rewrite the vision-fallback prompt as **grounded extraction**: transcribe only values visible on the page into the `EOBDocument` schema; never infer or complete a missing value
- [x] Have the model return, per field, the `page` it came from (and a short verbatim `span`); missing data → `found: false, value: null`, not a guess
- [x] Add a post-extraction **grounding check**: verify each returned value actually appears in `Document.words` on its cited page; values not found on-page are marked ungrounded
- [x] Constrain parametric use: arithmetic only over cited values (e.g. totals reconciliation), no prose composition — the extractor returns `EOBDocument` only
- [x] Feed grounding failures into `validate()` confidence (ungrounded value → low confidence → resend/confirm)
- [x] Tests: a genuinely-absent field → `null`/`found:false` (no hallucination); an off-page value → flagged ungrounded by the check

```python
# grounded field shape returned by the LLM fallback — value + provenance, never invented
{ "field": "anthem_paid", "value": "207.20", "page": 3, "span": "207.20", "found": true }
# post-check: assert value's tokens ∈ Document.words[page]; else outcome = "ungrounded"
```

### Handoff — Workstream A
**Completed:** 2026-06-15
**Branch:** main
**Tests:** pytest tests/ -x -q → 322 passed

#### What was built
The LLM vision fallback now requests a grounded JSON envelope where every leaf field carries provenance (`value`, `page`, `span`, `found`), normalizes it through `_unwrap_envelope` back to the existing `EOBDocument` shape (keeping all downstream consumers unchanged), and runs `check_grounding` to verify each cited span's tokens exist in `Document.words` on the cited page. Ungrounded fields flow into `validate()` as confidence-penalizing issues, so a hallucinated or off-page value reduces the extraction's confidence score and surfaces in `ValidationResult.issues`.

#### Files changed
- `src/medical/eob/types.py` — Added `GroundedField`, `GroundingReport` frozen dataclasses; added `GroundedExtractor` Protocol (left `Extractor` unchanged for deterministic paths)
- `src/medical/eob/extractors/grounding.py` (new) — `check_grounding` pure function; span-token containment check, both sides normalized (`$`/comma/whitespace/case)
- `src/medical/eob/extractors/llm.py` — New grounded system prompt; `_parse_grounded_field` + `_unwrap_envelope` normalization boundary; `extract` returns `tuple[EOBDocument, GroundingReport]`; `_parse_eob_json`/builders unchanged
- `src/medical/eob/validate.py` — Keyword-only `grounding_report: GroundingReport | None = None` param; ungrounded fields appended to `issues` (same `_ISSUE_PENALTY` as arithmetic mismatches)
- `src/medical/eob/pipeline.py` — `LLM_EXTRACTOR` typed as concrete `LLMVisionExtractor`; tuple unpacked; report forwarded to `validate`
- `tests/test_eob_llm.py` — 6 new test scenarios: absent field, grounded on-page, wrong-page flag, confidence penalty, empty-words, found=false skipped

#### How to verify manually
```bash
pytest tests/test_eob_llm.py -x -q  # 6 new grounding tests + existing LLM tests
pytest tests/ -x -q                  # full suite: 322 passed
```
To trace the grounding path end-to-end: run `process_eob(doc, llm_override=True)` with a `Document` whose `words` list does not contain a value the LLM would extract; inspect `Extracted.validation.issues` for `"ungrounded field: ..."` entries and `Extracted.validation.confidence` for the penalty.

#### Open questions / deferred decisions
- **Multi-token name fields** (e.g. `subscriber: "JANE A DOE"`): the grounding check tokenizes on whitespace and requires all tokens present anywhere on the page. Middle initials or punctuation mismatches (`"A."` vs `"A"`) may produce false-positive ungrounded flags on name fields. Accepted as a known limitation; if it causes noise in practice, a "any-token-present" threshold or looser matching can be added.
- **`received_date` behavior change**: previously the LLM could return Python `None` for a missing date; now `_unwrap_envelope` coerces absent fields to `""`. Downstream code that distinguishes `None` from `""` on `Claim.received_date` should be audited before Workstream B adds eval expectations for that field.
- **`_MAX_TOKENS = 4000`** may be tight for multi-claim documents with the new envelope shape (each leaf adds ~60 bytes). Bump to 6000 if truncation appears in fixture testing.

## Workstream B — Per-failure-mode eval harness
*Touchpoint: the per-phase tests + the runbook's insurer-cutover gate. "Measure the process, not the model": aggregate accuracy lies; report per (insurer × kind × subtype × column). A standalone harness writes a results table you group by.*

**Done when:** running the harness over the fixture corpus produces a results table you can `groupby` to see exactly which (insurer, kind, subtype, column) combinations fail, and the insurer-cutover decision reads from it.

- [x] Author labeled expectations per fixture (the expected `EOBDocument`) under `tests/fixtures/expected/`
- [x] Build `src/eob/eval/harness.py`: run `to_document → process_eob` over each labeled fixture, diff extracted vs expected **per field**
- [x] Create the `eval_results` store (schema below) in `src/eob/eval/store.py` — one row per (fixture, claim, field), tagged with the failure-mode dimensions
- [x] Implement per-failure-mode reporting: `pandas.groupby` over the dims → accuracy per (insurer × kind), per column, per subtype; surface the worst buckets, never a single aggregate
- [x] Wire the runbook **cutover gate** to read from `eval_results`: a new insurer profile leaves LLM-fallback only when its buckets pass thresholds (deterministic output vs the logged `log_unknown` LLM records)
- [x] Add a CLI entry to run the eval over the corpus on demand

```sql
-- src/eob/eval/store.py — one row per (fixture, claim, field); reports are groupby over this
CREATE TABLE eval_results (
    run_id     TEXT NOT NULL,
    fixture    TEXT NOT NULL,
    insurer    TEXT,
    kind       TEXT,        -- text | image | mixed
    subtype    TEXT,        -- summary | denial | payment_notice | duplicate_notice
    block_type TEXT,        -- header | claim_banner | claim_table | doc_banner
    field      TEXT,        -- anthem_paid | claim_number | patient_owes | ...
    extractor  TEXT,        -- deterministic profile | llm
    expected   TEXT,
    actual     TEXT,
    outcome    TEXT,        -- match | miss | mismatch | ungrounded
    confidence REAL,
    ts         DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Handoff — Workstream B
**Completed:** 2026-06-15
**Branch:** main
**Tests:** pytest tests/ -x -q → 335 passed (322 prior + 13 new)

#### What was built
A standalone per-failure-mode eval harness (`src/medical/eob/eval/`) that runs `to_document → process_eob` over labeled fixture expectations, diffs each extracted `EOBDocument` against the expectation one row per field (outcome: `match | miss | mismatch | ungrounded`), persists results to SQLite, and exposes `pandas.groupby` reporting over `(insurer × kind × subtype × field)` dimensions. The cutover gate in `docs/playbook/adding_an_insurer.md` now reads from this table. A CLI entry point (`python -m src.medical.eob.eval.cli`) runs the eval on demand.

#### Files changed
- `src/medical/eob/eval/__init__.py` — empty package marker
- `src/medical/eob/eval/store.py` — `init_eval_db` / `insert_eval_row` / `get_eval_results`; open-commit-close, never-raise, parameterized inserts
- `src/medical/eob/eval/diff.py` — pure `diff_eob`; field enumeration (`doc_banner`, `header`, `claim_banner`, `claim_table`), outcome priority `ungrounded > miss > mismatch > match`, claim/line-item-count mismatch handled without IndexError, `None`/`""` normalization for `received_date`
- `src/medical/eob/eval/expectations.py` — `load_expectation` / `eob_from_dict`; never-raise JSON→`EOBDocument` deserializer
- `src/medical/eob/eval/harness.py` — `run_harness`; catches all `to_document` exceptions per fixture (emits miss rows per expected field, not a crash); parses ungrounded field paths from `validation.issues` strings (GroundingReport is not exposed by `process_eob`); emits one miss row per expected field on total failures
- `src/medical/eob/eval/report.py` — `load_results`, `accuracy_by_insurer_kind`, `accuracy_by_column`, `accuracy_by_subtype`, `worst_buckets`; all sorted worst-first
- `src/medical/eob/eval/cli.py` — argparse CLI (`--fixture-dir`, `--expected-dir`, `--eval-db`, `--run-id`, `--llm`, `--report`)
- `tests/fixtures/expected/anthem_summary_01.json` — synthetic multi-claim Anthem summary expectation
- `tests/fixtures/expected/cigna_denial_01.json` — synthetic Cigna denial expectation with null `received_date`
- `tests/test_eob_eval.py` — 13 tests: store idempotency, insert/fetch round-trip, run_id filter, diff match/mismatch/miss/ungrounded/claim-count-mismatch, harness mock-run, NotAPdf total failure, Unreadable path, accuracy groupby, worst_buckets sort order
- `requirements.txt` — added `pandas>=2.0.0`
- `docs/playbook/adding_an_insurer.md` — new "Cutover gate" section referencing the eval CLI; 0.90 threshold marked as placeholder pending empirical calibration

#### How to verify manually
```bash
pytest tests/test_eob_eval.py -q          # 13 passed
pytest tests/ -x -q                        # 335 passed

# Run the CLI (needs fixture PDFs under tests/fixtures/ for non-failure rows)
python -m src.medical.eob.eval.cli \
  --fixture-dir tests/fixtures \
  --expected-dir tests/fixtures/expected \
  --eval-db /tmp/eval.db \
  --report worst
```

#### Open questions / deferred decisions
- **Cutover threshold (0.90):** explicitly a placeholder. The roadmap lists this as an open blocker; calibrate empirically against a representative fixture corpus before using the gate in production.
- **`in_network` boolean false-positive match:** if a claim exists with `in_network=False` in both expected and actual, it diffs as "match" regardless of whether it was genuinely extracted. If this field matters for the cutover gate, consider making it `Optional[bool]` or adding special-casing in `diff._classify`.
- **`_empty_eob()` subtype sentinel:** total-failure rows use `subtype=""` (off-Literal sentinel) so the `subtype` field diffs as "miss" rather than a spurious "match". If a strict `EOBSubtype` runtime guard is ever added, this line needs a real sentinel.
- **CLI `--llm` flag and CI:** the `--llm` flag calls the real LLM path over the corpus; it is intentionally absent from the test suite (all LLM calls are mocked per testing rules). Gate it out of CI if the corpus grows large.
- **Fixture PDFs:** only synthetic JSON expectations exist. Real (sanitized) fixture PDFs must be added to `tests/fixtures/` before accuracy metrics become meaningful.

## Workstream C — Adaptive table parsing (representation levels + local escalation)
*Touchpoint: main roadmap Phase 2 `parse_table`. "Don't flatten the grid" + adaptive parsing: cheap parse first, escalate only the tables that fail a diagnostic to a stronger local engine, record which method parsed each table.*

**Done when:** `parse_table` runs cheap-first, self-diagnoses bad parses, escalates only those tables to a local second engine, records the parsing method per table, and the narrow/magenta columns the cheap pass drops now resolve.

- [x] Define table representation levels for claim tables: L0 raw bucketed rows → L1 typed/named columns via `ColumnSpec`; keep most tables at the simplest level that works
- [x] Add a per-table **parse diagnostic**: do row values reconcile (arithmetic), are all expected columns populated, did the narrow right-side + magenta `your_total` columns resolve? → a quality score per table
- [x] Add an **escalation hook** in `parse_table`: on diagnostic failure, re-parse that table/page only with a local second engine (PP-Structure table recognition / RapidOCR), keep the better result
- [x] Keep the second engine behind the existing `ColumnSpec` so the output shape is unchanged (mechanism stays; only cell recovery improves)
- [x] Record `parsing_method` per claim/table for provenance (extends `source`/`extractor`)
- [x] Feed parse-diagnostic failures into the eval harness (Workstream B) so escalation needs surface per insurer/column
- [x] Tests: the `EOB_denial` multi-page / narrow-column case (or a known-bad table) triggers escalation, the escalated result reconciles, and `parsing_method` is recorded

### Handoff — Workstream C
**Completed:** 2026-06-15
**Branch:** main
**Tests:** pytest tests/ -x -q → 347 passed (335 prior + 12 new)

#### What was built
`parse_table` now runs a coordinate-bucket pass (L0), scores it with `_compute_diagnostic` (empty `your_total` columns, missing columns, per-row arithmetic), and escalates to a pluggable `SecondEngine` when the score falls below 0.6. A `NoOpSecondEngine` stub is provided until PP-Structure/RapidOCR is benchmarked. `parsing_method` (`"coordinate_bucket"` | `"second_engine"` | `"none"`) is recorded on every `Claim` and flows through `diff.py` as metadata on every eval row, so `report.py` groupby can slice accuracy by escalation status without affecting the accuracy denominator.

#### Files changed
- `src/medical/eob/types.py` — Added `TableDiagnostic`, `TableParseResult` frozen dataclasses; added `parsing_method: str = field(default="none")` to `Claim`
- `src/medical/eob/tables.py` — `SecondEngine` Protocol, `NoOpSecondEngine` stub, `_compute_diagnostic` pure function, `_try_parse_amount` helper; `parse_table` rewritten to return `TableParseResult` with diagnostic + escalation hook
- `src/medical/eob/profiles/anthem.py` — `_extract_claim_table` now returns `tuple[list[dict], str]`
- `src/medical/eob/profiles/__init__.py` — `_assemble_claim` accepts `parsing_method`; `ProfileExtractor.extract` unpacks the tuple and threads the method through
- `src/medical/eob/eval/diff.py` — `_row` carries `parsing_method`; `_diff_claim` receives it from `act_claim.parsing_method`; `diff_eob` reads the method per claim
- `src/medical/eob/eval/store.py` — `parsing_method TEXT` column added to schema with idempotent `ALTER TABLE` migration
- `tests/test_eob_extraction.py` — Updated 4 existing `parse_table` tests to use `.rows`; added 12 new tests covering diagnostic scoring, escalation trigger/keep-primary, no-engine path, `Claim.parsing_method` default

#### How to verify manually
```bash
pytest tests/test_eob_extraction.py -q   # 12 new Workstream C tests pass
pytest tests/ -x -q                      # full suite: 347 passed
```
To trace the escalation path: construct a `Block` with words only in the left columns (leaving `your_total` empty), call `parse_table(block, spec, second_engine=NoOpSecondEngine())`, and inspect `result.diagnostic.escalate` (True) and `result.parsing_method` (stays `"coordinate_bucket"` since NoOp returns nothing better).

#### Open questions / deferred decisions
- **Second engine selection (open blocker):** PP-Structure vs RapidOCR must be benchmarked against the CAX11 RAM budget on the denial fixture before wiring. Plug in by implementing `SecondEngine.parse(block, spec) -> list[dict[str,str]]` and passing the instance to `parse_table`.
- **Escalation threshold (0.6):** Set conservatively. Calibrate empirically once fixture PDFs exist.
- **`ESCALATION_THRESHOLD` is module-level:** callers can override per-call via the `escalation_threshold` kwarg. Anthem's `_extract_claim_table` does not currently pass a second engine — the hook is ready but inactive until an engine is wired.
- **`parsing_method` on banner-only claims:** claims assembled with no `claim_table` block carry `"none"` (the dataclass default), which is correct — they were never table-parsed.

---

## Blockers
- [ ] Second-engine selection for Workstream C — PP-Structure (heavier, native table cells) vs RapidOCR (lighter, ONNX) — must fit the CAX11 RAM budget; benchmark both on the denial fixture before wiring
- [ ] Confidence/score thresholds for the cutover gate (Workstream B) and the escalation trigger (Workstream C) — set empirically against the fixture corpus

## Reference
- Series: *Document Intelligence* — grounded-not-augmented (Vol 1 announcement §1.4), per-failure-mode eval (Article 20), tables / don't-flatten-the-grid (B04), adaptive parsing & second engine (Articles 5bis, 10)
- `roadmap_eob_extraction.md` — the main pipeline these layer onto (P2 `parse_table`, P3 LLM fallback)
- `adding_an_insurer.md` — the cutover gate Workstream B feeds
- Engines: Tesseract (primary), PaddleOCR/PP-Structure or RapidOCR (local escalation only)
