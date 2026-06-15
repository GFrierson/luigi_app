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

- [ ] Author labeled expectations per fixture (the expected `EOBDocument`) under `tests/fixtures/expected/`
- [ ] Build `src/eob/eval/harness.py`: run `to_document → process_eob` over each labeled fixture, diff extracted vs expected **per field**
- [ ] Create the `eval_results` store (schema below) in `src/eob/eval/store.py` — one row per (fixture, claim, field), tagged with the failure-mode dimensions
- [ ] Implement per-failure-mode reporting: `pandas.groupby` over the dims → accuracy per (insurer × kind), per column, per subtype; surface the worst buckets, never a single aggregate
- [ ] Wire the runbook **cutover gate** to read from `eval_results`: a new insurer profile leaves LLM-fallback only when its buckets pass thresholds (deterministic output vs the logged `log_unknown` LLM records)
- [ ] Add a CLI entry to run the eval over the corpus on demand

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

## Workstream C — Adaptive table parsing (representation levels + local escalation)
*Touchpoint: main roadmap Phase 2 `parse_table`. "Don't flatten the grid" + adaptive parsing: cheap parse first, escalate only the tables that fail a diagnostic to a stronger local engine, record which method parsed each table.*

**Done when:** `parse_table` runs cheap-first, self-diagnoses bad parses, escalates only those tables to a local second engine, records the parsing method per table, and the narrow/magenta columns the cheap pass drops now resolve.

- [ ] Define table representation levels for claim tables: L0 raw bucketed rows → L1 typed/named columns via `ColumnSpec`; keep most tables at the simplest level that works
- [ ] Add a per-table **parse diagnostic**: do row values reconcile (arithmetic), are all expected columns populated, did the narrow right-side + magenta `your_total` columns resolve? → a quality score per table
- [ ] Add an **escalation hook** in `parse_table`: on diagnostic failure, re-parse that table/page only with a local second engine (PP-Structure table recognition / RapidOCR), keep the better result
- [ ] Keep the second engine behind the existing `ColumnSpec` so the output shape is unchanged (mechanism stays; only cell recovery improves)
- [ ] Record `parsing_method` per claim/table for provenance (extends `source`/`extractor`)
- [ ] Feed parse-diagnostic failures into the eval harness (Workstream B) so escalation needs surface per insurer/column
- [ ] Tests: the `EOB_denial` multi-page / narrow-column case (or a known-bad table) triggers escalation, the escalated result reconciles, and `parsing_method` is recorded

---

## Blockers
- [ ] Second-engine selection for Workstream C — PP-Structure (heavier, native table cells) vs RapidOCR (lighter, ONNX) — must fit the CAX11 RAM budget; benchmark both on the denial fixture before wiring
- [ ] Confidence/score thresholds for the cutover gate (Workstream B) and the escalation trigger (Workstream C) — set empirically against the fixture corpus

## Reference
- Series: *Document Intelligence* — grounded-not-augmented (Vol 1 announcement §1.4), per-failure-mode eval (Article 20), tables / don't-flatten-the-grid (B04), adaptive parsing & second engine (Articles 5bis, 10)
- `roadmap_eob_extraction.md` — the main pipeline these layer onto (P2 `parse_table`, P3 LLM fallback)
- `adding_an_insurer.md` — the cutover gate Workstream B feeds
- Engines: Tesseract (primary), PaddleOCR/PP-Structure or RapidOCR (local escalation only)
