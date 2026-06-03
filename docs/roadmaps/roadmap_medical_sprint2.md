# Roadmap: Medical Bill Tracking — Sprint 2
**Goal:** Close the three v1 deferred gaps from Sprint 1 (scanned PDF rendering, multi-photo combining, correction loop), add document intelligence that learns EOB/statement page structure so extraction focuses only on content-bearing pages, fix claim matching so billed-amount mismatches surface as an explicit user prompt rather than silently creating duplicate claims, and introduce a playbook-driven deterministic extractor layer — starting with Anthem EOBs and EOB check PDFs — that routes known-issuer documents away from the LLM.
**Depends on:** Sprint 1 complete (Phases 1–8 of `roadmap_medical_bill_tracking.md`)
**Estimated scope:** 3–4 weeks
**Status:** Not started
**Last updated:** 2026-06-03

---

## Extractor Playbook

Every Phase 13+ extractor follows this sequence. The pattern is adapted from the dobby PDF extraction platform (`dobby_docs/roadmaps/roadmap-pdf-extraction-v2.md`), where it took initial multi-extractor precision from 6.9% to ≥ 95–100% across all gated doc types by working one issuer × doc-type at a time.

1. **Collect sample** — gather N ≥ 10 real PDFs of the target `(insurer, doc_type)`. Save to `experiments/medical/{insurer}_{doc_type}/sample/`. Aim for N ≥ 15 for multi-field extractors. Note distinct layout variants (single-page vs. multi-page, electronic vs. paper-scan).
2. **Annotate ground truth** — for each doc, manually fill true values for each target field. Commit as `experiments/medical/{insurer}_{doc_type}/annotations.csv`. Only mark a row `_review_status=verified` after personal inspection of the PDF.
3. **Build annotation + eval scripts before the extractor** — `src/medical/scripts/annotate_{insurer}_{doc_type}.py` runs the pypdf text layer against the sample and pre-fills `_hyp_*` hypothesis columns to reduce manual annotation work; `src/medical/scripts/eval_{insurer}_{doc_type}.py` reads verified rows, runs the extractor, and prints precision / recall / N per field vs. the gate. The review-and-improve loop only works if you can measure the effect of each change in under a minute.
4. **Build the extractor** — `src/medical/extractors/{insurer}_{doc_type}.py` with a module-level `EXTRACTOR_VERSION = "{insurer}_{doc_type}_v1"` constant. Export a top-level `extract(text: str) -> Optional[ExtractionResult]` function. Never raise — return `None` on failure, log with `exc_info=True`.
5. **Review-and-improve loop** — for each iteration:
   1. Run `eval_{insurer}_{doc_type}.py`. Identify failing rows.
   2. Group failures by root cause: wrong label matched? date format variant? multi-claim table? header vs. line-item confusion? Name the failure mode.
   3. Fix the highest-impact failure mode, add a unit test that pins the case.
   4. Re-run the eval. Also run `run_all_extractor_evals.py` to confirm no regressions on previously-gated extractors.
   5. **Improved and gate cleared** → stop, move to step 6. **Improved but gate not cleared** → loop. **No improvement** → check in with a note explaining why before continuing.
6. **Gate** — precision ≥ 90%, N ≥ 10 docs for single-field extractors; N ≥ 15 for multi-field. A field with zero annotated rows is `UNSCORED` (skip, not failure).
7. **Register in allowlist** — add entry to `EXTRACTOR_ALLOWLIST` in `src/medical/extractors/allowlist.py`; confirm `extract_from_file()` in `extraction.py` routes the new `(insurer, doc_type)` combination to the extractor before the LLM fallback. Run `run_all_extractor_evals.py` (exit 0) as the gate record.
8. **Update shared runner** — add the new extractor's `run_eval()` function to `src/medical/scripts/run_all_extractor_evals.py` so regressions are caught automatically.

**The canonical example:** dobby Phase 4C (ONE invoice MBL) — initial eval 96.2% precision (above gate, but 3 known misses) → root cause identified (carrier SCAC prefix `ONEY` decorating extracted BL numbers) → normalizer shipped with 5 unit tests → re-run ~99% precision.

---

## Phase 9: Scanned PDF Rendering + Multi-Photo Combining
**What's true when this is done:** A scanned-only EOB PDF (no selectable text) produces a valid `ExtractionResult`. An album of N photos sent to the bot produces one extraction that covers all N images in a single vision call — no pages silently dropped.

- [x] Add `pdf2image>=1.16.0` to `requirements.txt`; add a comment documenting the required Poppler system dependency (`apt: poppler-utils` / `brew: poppler`)
- [x] In `src/medical/extraction.py`: after pypdf text extraction, check `len(text.strip()) < SPARSE_TEXT_THRESHOLD` (tune threshold, suggest 100 chars per page); if sparse, rasterize via `pdf2image.convert_from_path()` and build a multi-image base64 payload instead of a text payload — reuse the existing image vision call path
- [x] In `src/medical/ingestion.py` `_flush_photo_group`: collect all `file_id` values buffered for the group before calling extraction; pass the full list to `extract_from_file` (extend signature to accept `List[str]` file paths)
- [x] Extend `extraction.py` `extract_from_file` to accept a list of image paths and pack them as multiple `image_url` content blocks in one vision call (OpenRouter supports multi-image in a single message)
- [x] Write tests: sparse-PDF branch triggers rasterization path (mock `pdf2image`); album of 3 images produces one `ExtractionResult` with all 3 source images in the payload; dense-text PDF still uses text path (no rasterization)

### Handoff — Phase 9
**Completed:** 2026-06-03
**Branch:** main
**Tests:** pytest tests/ -x -q → 219 passed

#### What was built
Scanned PDFs (no selectable text layer) are now rasterized via `pdf2image` and sent to the vision model as a multi-image base64 payload rather than an empty text prompt. Photo albums of up to `MAX_ALBUM_IMAGES=6` images are packed into a single vision call so no pages are silently dropped; albums exceeding the cap receive a user-facing rejection message. The `SPARSE_TEXT_THRESHOLD * page_count` check scales with document length so multi-page EOBs don't false-trigger.

#### Files changed
- `requirements.txt` — added `pdf2image>=1.16.0` with Poppler system dependency comment
- `src/medical/extraction.py` — added `SPARSE_TEXT_THRESHOLD=100` and `MAX_ALBUM_IMAGES=6` constants; refactored `_extract_pdf_text` to return `(full_text, per_page_texts)`; added `_rasterize_pdf` (returns `[]` on failure, never raises) and `_build_multi_image_message`; extended `extract_from_file` with `extra_image_bytes` param and sparse-PDF rasterization branch (falls back to text path if rasterization yields nothing)
- `src/medical/ingestion.py` — extended `ingest_document` with pass-through `extra_image_bytes` param; rewrote `_flush_photo_group` to collect all buffered photos, enforce the cap with a user-facing Telegram message, and pass `photos[1:]` as extras
- `tests/test_medical_extraction.py` — 4 new tests: sparse-PDF triggers rasterization, dense-PDF skips it, threshold scales with page count, multi-image album completes successfully

#### How to verify manually
1. Send a scanned-only EOB PDF (no selectable text) to the bot — confirm a valid extraction is returned (requires Poppler installed)
2. Send an album of 2–3 photos — confirm one combined `ExtractionResult` covers all images
3. Send an album of 7+ photos — confirm the bot replies with the album-limit rejection message and does not extract
4. Send a normal text-layer EOB PDF — confirm behavior is unchanged (no rasterization, uses text path)

#### Open questions / deferred decisions
- **Poppler system dependency:** `_rasterize_pdf` degrades gracefully (logs warning, falls back to sparse text) if Poppler is not installed, but scanned PDFs will not extract correctly until `poppler-utils` is installed on the VPS. This is a deployment task tracked in the Blockers section.
- **Multi-image token cost:** large albums (3–6 images) increase per-call token usage; the `MAX_ALBUM_IMAGES=6` guard is conservative. Monitor costs in production and lower the cap if needed.
- **`extra_image_bytes` abstraction:** the extras are raw bytes in memory (never written to disk as separate `documents` rows). If per-photo provenance is needed in the future, `_flush_photo_group` will need to call `save_document` for each extra photo before passing paths.

---

## Phase 10: Correction Loop
**What's true when this is done:** When a confirmation message shows an unrecognized practice or wrong date and the user replies with a numbered correction (e.g. `1: Manhattan Pain Med`), Luigi re-renders the confirmation with the correction applied. The user then replies `confirm` to commit. Corrections round-trip correctly for practice name, provider name, and service date. After 3 correction rounds without a `confirm`, Luigi prompts to confirm or cancel to prevent unbounded loops.

- [x] In `src/medical/confirmation.py`: add `apply_correction(pending: dict, action: dict) -> dict` — pure function that updates `pending["match_results"]` (and `pending["practice_id_by_name"]` for name corrections) based on a parsed correction action; returns an updated copy of `pending`
- [x] In `src/medical/matching.py`: add `rematch_after_correction(db_path: str, pending: dict) -> dict` — re-runs `match_practice` / `match_provider` / `match_claim` only on fields that were corrected; returns updated `match_results`
- [x] In `src/medical/ingestion.py`: on `correction` reply action from `parse_confirmation_reply`, call `apply_correction` → `rematch_after_correction` → `build_confirmation_message`; store updated pending and re-send the confirmation message; track correction round count in the pending dict
- [x] Cap correction rounds at 3; on the 4th correction attempt, send: _"I've applied 3 corrections. Reply `confirm` to save or `cancel` to discard."_ — do not re-render
- [x] Write tests: correction re-renders confirmation with updated practice name; correction on service date updates claim match lookup; `cancel` reply after correction discards pending and sends cancellation message; 3-round cap sends final prompt instead of re-rendering

### Handoff — Phase 10
**Completed:** 2026-06-03
**Branch:** main
**Tests:** pytest tests/ -x -q → 223 passed

#### What was built
Users can now correct numbered items in a confirmation message by replying `<number> <correction>`. Each correction re-matches the affected entity against the DB and re-sends an updated confirmation. Corrections are capped at 3; the 4th attempt gets a final prompt instead of a re-render. A `cancel`/`discard` reply discards the pending confirmation and clears state. The round count lives in memory only — no schema change required.

#### Files changed
- `src/medical/confirmation.py` — added `_CANCEL_WORDS` constant and `cancel` branch in `parse_confirmation_reply`; added `apply_correction(pending, action) -> Optional[dict]` that deep-copies pending, maps 1-based item_index to the filtered action-required list (unmatched practices → unmatched claims → providers), patches the field, syncs `practice_id_by_name` and `extraction.claims[*].practice_name` on practice-name corrections, returns `None` on out-of-range index
- `src/medical/matching.py` — added `rematch_after_correction(db_path, pending) -> dict` that re-runs `match_practice` on unresolved practices, `match_claim` on unmatched claims, and `match_provider` on all providers; returns updated `match_results`
- `src/medical/ingestion.py` — added `_MAX_CORRECTION_ROUNDS = 3` and async `handle_correction(db_path, chat_id, pending, action, context)`: increments round count, enforces cap with `> _MAX_CORRECTION_ROUNDS` (fires on 4th attempt so exactly 3 corrections are re-rendered), sends user-facing error on invalid item index, re-matches via `asyncio.to_thread`, stores updated pending, sends new confirmation
- `src/telegram_handler.py` — replaced stub `correction` branch with call to `handle_correction`; added `cancel` branch that pops `_pending_confirmations[chat_id]` and sends "Cancelled."
- `tests/test_medical_ingestion.py` — 4 new tests: practice-name correction re-renders and finds matched practice (rounds → 1), service-date correction finds existing claim, cancel reply clears pending, 3-round cap (starts at correction_rounds=3, 4th attempt fires cap without advancing stored state)

#### How to verify manually
1. Send an EOB that has an unrecognized practice — confirm the numbered prompt appears
2. Reply `1 Correct Practice Name` — confirm the confirmation re-renders with the corrected name and DB-matched practice
3. Reply `1 correct date` targeting a claim date — confirm the claim match updates
4. Reply `cancel` — confirm "Cancelled." is sent and no pending state remains
5. Make 3 corrections in sequence — confirm each re-renders; the 4th attempt returns the "I've applied 3 corrections" prompt instead of re-rendering

#### Open questions / deferred decisions
- **Provider corrections:** providers are re-matched in `rematch_after_correction` but the result is informational only (providers aren't surfaced in `match_results`). If per-provider match state in the confirmation becomes useful, `match_results` would need a `providers` sub-dict.
- **`practice_id_by_name` after correction to a new (unrecognized) name:** `rematch_after_correction` calls `match_practice` which returns `None` for unknown names, leaving `practice_id = None`. This means `commit_ingestion` will create a new practice for the corrected name — which is correct behavior; no change needed.

---

## Phase 11: Document Intelligence — Layout Learning
**What's true when this is done:** On the second EOB ingestion from the same practice, Luigi skips blank/boilerplate pages and sends only content-bearing pages to the vision model. The learned page range is stored per `(doc_type, practice_id)` and reused automatically. First-time ingestion auto-detects relevant pages using text-density scoring and persists the result.

- [x] Add `document_templates` table to `src/database.py` `init_db()`:
  ```sql
  CREATE TABLE IF NOT EXISTS document_templates (
      id              INTEGER PRIMARY KEY,
      doc_type        TEXT NOT NULL,
      practice_id     INTEGER REFERENCES practices(id),
      relevant_pages  TEXT NOT NULL,   -- JSON array of 0-based page indices
      sample_count    INTEGER NOT NULL DEFAULT 1,
      created_at      TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
      UNIQUE(doc_type, practice_id)
  )
  ```
- [x] Write `src/medical/layout.py` with:
  - `score_page_relevance(page_text: str) -> float` — ratio of non-whitespace chars to total chars; 0.0 = blank, 1.0 = dense
  - `detect_relevant_pages(pages: list[str], threshold: float = 0.05) -> list[int]` — returns indices of pages whose score exceeds threshold; excludes leading and trailing blank pages
  - `load_template(db_path: str, doc_type: str, practice_id: Optional[int]) -> Optional[list[int]]` — returns stored relevant page indices or `None`
  - `update_template(db_path: str, doc_type: str, practice_id: Optional[int], observed_pages: list[int]) -> None` — upsert; on conflict, expand the stored range to the union of stored and observed (conservative: never drop a page seen in prior samples); increment `sample_count`
- [x] In `src/medical/extraction.py`: after reading PDF pages, call `load_template` → if found, filter `pages` list to stored indices before building the extraction payload; if not found, call `detect_relevant_pages` on all pages, filter to detected range, then call `update_template` to persist it for next time
- [x] Extend `extract_from_file` to accept an optional `db_path: Optional[str]` and `practice_id: Optional[int]` so the template lookup has the right keys; callers that don't need layout learning can pass `None`
- [x] Write tests: `score_page_relevance` returns 0.0 for blank page, >0.5 for dense page; `detect_relevant_pages` strips leading and trailing blanks; `update_template` union-expands on second call; extraction uses stored range on second call (mock `load_template`); extraction stores template on first call (mock `update_template`, assert called)

### Handoff — Phase 11
**Completed:** 2026-06-03
**Branch:** main
**Tests:** pytest tests/ -x -q → 232 passed

#### What was built
Luigi now learns which pages of a multi-page EOB carry real content and skips filler pages (cover sheets, blank backs, boilerplate) on subsequent extractions. On the first ingestion of a given doc type, `detect_relevant_pages` runs text-density scoring across all pages, the relevant page range is persisted to a new `document_templates` table, and the filtered text is sent to the LLM. On subsequent ingestions, the stored template is loaded and applied directly, reducing LLM payload size. The unique index uses `COALESCE(practice_id, 0)` to safely handle the NULL practice_id case (practice matching occurs after extraction, so v1 templates are keyed per `(doc_type, NULL)` — one template per doc type per user).

#### Files changed
- `src/database.py` — added `document_templates` table + `uq_document_templates_doc_type_practice` expression-based unique index (`COALESCE(practice_id, 0)`) to `init_db()`
- `src/medical/layout.py` — new module: `score_page_relevance`, `detect_relevant_pages` (strips leading/trailing blanks, keeps sandwiched pages), `load_template` (NULL-safe query), `update_template` (sorted-union upsert)
- `src/medical/extraction.py` — added `db_path` and `practice_id` optional params to `extract_from_file`; layout learning runs only in the dense-text PDF branch (after the sparse check); out-of-bounds stored indices fall back to all pages
- `src/medical/ingestion.py` — `ingest_document` passes `db_path` through to `extract_from_file`
- `tests/test_medical_extraction.py` — 9 new Phase 11 tests (5 unit for layout.py, 4 integration for extract_from_file)

#### How to verify manually
1. Send any text-layer EOB PDF for the first time — confirm extraction succeeds and `document_templates` has a new row for the doc type
2. Send the same issuer's EOB PDF again — confirm extraction succeeds (template reused, only relevant pages in the LLM payload)
3. Send a scanned-only EOB PDF — confirm rasterization path is taken and no template interaction occurs
4. Check `document_templates` in the user's SQLite DB: `SELECT doc_type, relevant_pages, sample_count FROM document_templates;`

#### Open questions / deferred decisions
- **Template keying by practice:** `practice_id` is always `None` at extraction time (practice matching happens after extraction). Templates are keyed `(doc_type, NULL)` for now — one per doc type per user. If per-practice layouts diverge noticeably, backfill templates by practice after commit and re-key. Tracked in Blockers.
- **Template keying by insurer:** the Blockers section already tracks the fallback lookup chain `(doc_type, practice_id)` → `(doc_type, insurer_id)` → `(doc_type, NULL)` — deferred until second insurer.

---

## Phase 12: Claim Matching — Amount-Tolerance + Ambiguity Prompt
**What's true when this is done:** Sending an EOB for a visit whose bill was already uploaded no longer silently creates a duplicate claim when the billed amounts differ. When an exact match fails but a `submitted` claim exists for the same `(service_date, practice_id)`, Luigi flags the ambiguity in the confirmation message and asks the user to link or treat as separate. The `matched_claims_by_date` collision bug for multi-claim same-date visits is also fixed.

- [x] In `src/medical/claims.py`: add `find_submitted_by_date_and_practice(db_path: str, service_date: str, practice_id: int) -> list[dict]` — returns all claims with `current_status = 'submitted'` for the given `(service_date, practice_id)`, ordered by `created_at`
- [x] In `src/medical/matching.py`: update `match_claim` — after `find_by_match_key` returns `None`, call `find_submitted_by_date_and_practice`; if one result is found, return it as a `suggested_link` (not `matched=True`) with `match_type='prior_bill'`; if multiple are found, return all as suggestions; if none, return unmatched as before
- [x] In `src/medical/confirmation.py`: update `build_confirmation_message` to render `suggested_link` entries distinctly — e.g. _"⚠️ No exact match, but a prior bill exists for this visit (billed $X on [date]). Reply `link` to connect this EOB to it, or `confirm` to create a separate claim."_
- [x] In `src/medical/ingestion.py`: handle the new `link` reply action from `parse_confirmation_reply` — set `existing_claim_id` to the suggested claim's id and proceed as a matched claim (adjudicate only, no new claim created); update `parse_confirmation_reply` in `confirmation.py` to recognise `link` as a valid keyword
- [x] Fix `matched_claims_by_date` in `commit_ingestion`: change the key from `service_date` to `(service_date, billed_amount)` so two claims for the same date but different amounts each resolve independently
- [x] Write tests: exact-match path unchanged; `find_submitted_by_date_and_practice` returns prior bill when amounts differ; confirmation message renders `suggested_link` warning; `link` reply adjudicates existing claim without creating a new one; two claims same date different amounts both resolve correctly after the key fix

### Handoff — Phase 12
**Completed:** 2026-06-03
**Branch:** main
**Tests:** pytest tests/ -x -q → 242 passed (10 new)

#### What was built
When an incoming EOB's claim has no exact match-key hit but a prior `submitted` bill exists for the same `(service_date, practice_id)`, the confirmation message now flags the ambiguity with a "Possible duplicate" block. The user can reply `link` (single candidate) or `link N` (multiple candidates) to attach the EOB adjudication to the existing claim without creating a duplicate, or `confirm` to create a separate claim. A same-date/different-amount key-collision bug in `commit_ingestion` and `rematch_after_correction` was also fixed by switching from service-date-keyed dicts to index-based pairing.

#### Files changed
- `src/medical/claims.py` — added `find_submitted_by_date_and_practice(db_path, service_date, practice_id) -> list[dict]` querying `current_status='submitted'` by `billing_practice_id`, ordered by `id ASC` (no `created_at` column in schema; id ordering gives equivalent oldest-first result)
- `src/medical/matching.py` — `match_claim` falls back to the submitted-bill lookup and returns `{"matched": False, "suggested_link": [...], "match_type": "prior_bill"}` on ambiguity; `rematch_after_correction` now pairs claim_results to extraction.claims by list index instead of a date-keyed dict (fixes same-date collision)
- `src/medical/confirmation.py` — `build_confirmation_message` renders a "Possible duplicate" section (no correction number, link-only resolution); `parse_confirmation_reply` parses `link`/`link N` into `{"action": "link", "choice": int}` (0-based); `apply_correction` skips `suggested_link` entries from the numbered action list
- `src/medical/ingestion.py` — added `handle_link` async handler; `commit_ingestion` pairs match-results to claims by index (key-collision fix)
- `src/telegram_handler.py` — imported `handle_link`; added `link` branch in `_on_message` alongside `correction`/`cancel`
- `tests/test_medical_claims.py` — 4 tests: returns prior bill, returns multiple, excludes adjudicated, returns empty when no practice match
- `tests/test_medical_ingestion.py` — 6 tests: link reply parsing, suggested-link message render, link adjudicates existing claim without creating new, out-of-range choice preserved, same-date different-amounts both resolve

#### How to verify manually
1. Upload a bill (creates a `submitted` claim)
2. Send an EOB for the same visit with a different billed amount — confirm the confirmation shows the "Possible duplicate" warning with a `link` prompt instead of silently creating a second claim
3. Reply `link` — confirm the adjudication attaches to the existing claim (no new claim row created)
4. Reply `confirm` — confirm a separate claim is created as expected
5. For multi-suggestion: two prior submitted claims on the same date — confirm `link 1` / `link 2` options are rendered

#### Open questions / deferred decisions
- **`ORDER BY id ASC` instead of `created_at`**: the `claims` table has no `created_at` column, so the approved "order by created_at" was implemented as `ORDER BY id ASC` (equivalent for insertion order). If a `created_at` column is added in a future migration, the query should be updated.
- **`suggested_link` with 2+ candidates**: the full `link N` disambiguation round-trip is implemented, but the multi-candidate path has less test coverage than the single-candidate path. Add more fixture-based tests if edge cases surface in practice.

---

## Phase 13: Extractor Infrastructure — Dispatch Router + Annotation Harness
**What's true when this is done:** A new `src/medical/extractors/` package exists with an allowlist-based dispatch layer. `extract_from_file()` checks `(insurer, doc_type)` against the allowlist and routes to a registered deterministic extractor before falling back to the LLM — so existing behavior is completely unchanged until a Phase 14/15 extractor clears its gate. A shared eval runner script exists that runs all gated extractors, confirms no regressions, and exits non-zero if any fail. The `experiments/medical/` directory tree and annotation CSV conventions are documented. This phase ships zero extractor logic — only the infrastructure that Phases 14 and 15 build on.

- [ ] Create `src/medical/extractors/__init__.py` (empty package marker)
- [ ] Create `src/medical/extractors/allowlist.py`:
  ```python
  # Each entry: { "insurer": str, "doc_type": str, "extractor_version": str }
  EXTRACTOR_ALLOWLIST: list[dict] = []
  ```
  Start empty; each new extractor appends its entry here after clearing its gate.
- [ ] Add `_detect_insurer(text: str) -> Optional[str]` helper to `src/medical/extraction.py` — scans lowercased first-page text for insurer-identifying phrases before dispatching. Initial entries: `"anthem"` / `"blue cross blue shield of georgia"` / `"bcbs"` → `"anthm"`. Returns `None` if no match. Extend the mapping as new extractors are added.
- [ ] Refactor `extract_from_file()` in `src/medical/extraction.py` to call `_detect_insurer(text)` after the pypdf text pass, then check `EXTRACTOR_ALLOWLIST` for a matching `(insurer, doc_type)` entry. If found, import and call that extractor's `extract(text)` and return the result; if not found (or if the deterministic extractor returns `None`), fall through to the existing LLM call unchanged.
- [ ] Add `"check"` to the `doc_type` literal in `ExtractionResult` and `EOB_PROMPT` so Anthem check PDFs can be classified as a distinct doc type from `"eob"`.
- [ ] Create `src/medical/scripts/run_all_extractor_evals.py` — shared runner: imports `run_eval()` from each registered extractor's eval script, runs them in sequence, prints a summary table (extractor × field: N, precision %, PASS / FAIL / SKIP), exits 1 if any FAIL. Initially no registered evals; add one line per eval script as Phases 14/15 complete.
- [ ] Create directory skeleton: `experiments/medical/` with a `README.md` describing the annotation CSV format (`file_path`, `_true_{field}`, `_hyp_{field}`, `_review_status`). **Do not commit the sample PDFs** — commit only the annotation CSVs (field values only, no raw document content).
- [ ] Write tests: `_detect_insurer` returns `"anthm"` on Anthem text, `None` on unrecognized text; dispatch routes to a registered extractor (stub); unregistered `(insurer, doc_type)` falls through to LLM unchanged; `run_all_extractor_evals.py` exits 0 with no registered evals.

---

## Phase 14: Anthem EOB Parser
**What's true when this is done:** Anthem EOB PDFs (text-layer) produce an `ExtractionResult` deterministically without an LLM call, at ≥ 90% precision on N ≥ 15 annotated docs (multi-field extractor). The extractor is registered in the allowlist, dispatched from `extract_from_file()`, and covered by the shared eval runner.

**Blocked by:** Phase 13 complete. Can start in parallel with Phases 9–12 — no dependency on scanned PDF rendering or claim-matching work.

**Playbook steps (follow Extractor Playbook above):**

- [ ] **Sample** — collect N ≥ 15 Anthem EOB PDFs. Save to `experiments/medical/anthm_eob/sample/`. Note layout variants: single-claim vs. multi-claim, electronic delivery vs. paper-scan, standard EOB vs. coordination-of-benefits EOB.
- [ ] **Annotate** — manually fill true values for each doc. Target fields: `service_date`, `practice_name`, `provider_name`, `billed_amount`, `allowed_amount`, `plan_paid`, `member_responsibility`, `claim_number`, `procedure_codes` (list), `diagnosis_codes` (list). Commit as `experiments/medical/anthm_eob/annotations.csv` with `_review_status=verified` per row.
- [ ] **Annotation script** — `src/medical/scripts/annotate_anthm_eob.py`: reads PDFs in sample directory, extracts text via pypdf, pre-fills `_hyp_*` columns using heuristic label searches (e.g., `"Claim Number"`, `"Billed Amount"`, `"Plan Paid"`), writes `annotations.csv` with `_true_*` columns blank for manual fill. Prints a summary of which fields had hypothesis hits vs. misses.
- [ ] **Eval script** — `src/medical/scripts/eval_anthm_eob.py`: reads verified rows from `annotations.csv`, calls `extract(text)` from `anthm_eob.py`, compares each field, prints precision / recall / N per field, PASS / FAIL vs. gate (≥ 90%, N ≥ 15). Export `run_eval(sample_dir: str, annotations_path: str) -> dict` for use by the shared runner.
- [ ] **Build extractor** — `src/medical/extractors/anthm_eob.py`:
  - `EXTRACTOR_VERSION = "anthm_eob_v1"` at module level
  - `extract(text: str) -> Optional[ExtractionResult]` — label-based text extraction; never raises
  - Label targets (tune from real docs): `"Service Date"` / `"Date of Service"`, `"Billed"` / `"Billed Amount"`, `"Allowed"`, `"Plan Paid"`, `"Your Responsibility"` / `"Member Responsibility"`, `"Claim Number"` / `"Claim #"`, provider block (look for `"Provider:"` or rendering provider header), procedure code block (`CPT:` or table column)
  - Return `None` and `logger.error(..., exc_info=True)` on any parse failure; caller falls through to LLM
- [ ] **Review-and-improve loop** — typical failure modes to watch for: multi-claim EOBs with one row per claim (need to iterate claim rows, not just find first label); `"Plan Paid"` vs. `"Check Amount"` label drift across EOB versions; date format variants (`MM/DD/YYYY` vs. `MMM DD, YYYY`); procedure codes in a table vs. inline. Fix by root cause, add a pinning unit test per fix, re-run eval.
- [ ] **Gate** — precision ≥ 90%, N ≥ 15. Run `run_all_extractor_evals.py` (exit 0) as the gate record.
- [ ] **Register** — add `{ "insurer": "anthm", "doc_type": "eob", "extractor_version": "anthm_eob_v1" }` to `EXTRACTOR_ALLOWLIST`; add `run_eval` import to `run_all_extractor_evals.py`.
- [ ] **Tests** — `tests/test_medical_extractors.py`: happy path extracts correct `service_date`, `billed_amount`, `plan_paid` from a minimal fixture string; multi-claim EOB iterates all claim rows; `claim_number` not found returns `None` (not a crash); gate score committed as a comment in the test file header.

---

## Phase 15: Anthem EOB Check PDF Parser
**What's true when this is done:** Anthem check PDFs (Explanation of Payment / remittance advice PDFs sent with physical checks to the member) produce an `ExtractionResult` with `doc_type="check"` deterministically, at ≥ 90% precision on N ≥ 10 annotated docs. Registered in the allowlist, dispatched, and covered by the shared runner. Running `run_all_extractor_evals.py` confirms both the Anthem EOB extractor (Phase 14) and this extractor pass with no regressions.

**Blocked by:** Phase 13 complete. Phase 14 recommended first — shares evaluation infrastructure and teaches Anthem label conventions; check PDFs often reference EOB claim numbers.

**Playbook steps:**

- [ ] **Sample** — collect N ≥ 10 Anthem check / remittance PDFs. Save to `experiments/medical/anthm_check/sample/`. Determine whether these are electronic PDFs or paper-scan (informs whether scanned-PDF handling from Phase 9 is a prerequisite).
- [ ] **Annotate** — target fields: `check_number`, `check_date`, `payee_name`, `check_amount`, `claim_references` (list of claim numbers paid by this check). Commit as `experiments/medical/anthm_check/annotations.csv`.
- [ ] **Annotation script** — `src/medical/scripts/annotate_anthm_check.py`: same structure as Phase 14's annotation script. Pre-fill `_hyp_*` columns from label scans (`"Check Number"`, `"Check Date"`, `"Pay To"`, `"Total Amount"`, `"Claim Number"`).
- [ ] **Eval script** — `src/medical/scripts/eval_anthm_check.py`: same structure. Export `run_eval()` for the shared runner.
- [ ] **Build extractor** — `src/medical/extractors/anthm_check.py`:
  - `EXTRACTOR_VERSION = "anthm_check_v1"`
  - `extract(text: str) -> Optional[ExtractionResult]` with `doc_type="check"`
  - Label targets (tune from real docs): `"Check Number"`, `"Check Date"`, `"Pay To"` / `"Payable To"`, `"Total Amount"` / `"Amount"`, claim number table or reference block
  - Handle remittance tables: a single check may reference multiple claim numbers — collect all into `claim_references` list
- [ ] **Review-and-improve loop** — typical failure modes: check amount vs. per-claim amount confusion; claim references in a table (multi-row extraction); check number format variety. Same pattern as Phase 14.
- [ ] **Gate** — precision ≥ 90%, N ≥ 10. Run `run_all_extractor_evals.py` (exit 0 for both this extractor AND Phase 14 — no regressions).
- [ ] **Register** — add `{ "insurer": "anthm", "doc_type": "check", "extractor_version": "anthm_check_v1" }` to `EXTRACTOR_ALLOWLIST`; add `run_eval` import to `run_all_extractor_evals.py`.
- [ ] **Tests** — `tests/test_medical_extractors.py`: extend with check fixture tests; multi-claim check iterates all claim reference rows; `check_amount` parses correctly for both `$1,234.56` and `1234.56` formats.

---

## Blockers & Open Questions

- [ ] **Poppler system dependency** — `pdf2image` requires Poppler binaries installed at the OS level. This needs to be documented in the project README and added to any deployment scripts or Dockerfiles. The VPS deployment process must be updated before Phase 9 ships.
- [ ] **Template keying by insurer vs. practice** — the `document_templates` schema keys templates on `(doc_type, practice_id)`. EOBs from the same insurer but different practices may have identical layouts. If this proves true in practice, add an `insurer_id` key column and a fallback lookup chain: `(doc_type, practice_id)` → `(doc_type, insurer_id)` → `(doc_type, NULL)`. Deferred until second insurer enters the picture.
- [ ] **Multi-image vision token cost** — combining N album photos into one vision call increases per-call token usage. For 3-page photo albums this is acceptable; for large albums (>6 images) it may exceed model context or become expensive. Add a `MAX_ALBUM_IMAGES` guard (suggest 6) with a user-facing message if exceeded.
- [ ] **Correction loop and `practice_id_by_name` consistency** — `apply_correction` must keep `practice_id_by_name` in sync when a practice name correction resolves to an existing DB row vs. a new entity. The `rematch_after_correction` step must re-run `match_practice` against the DB so the corrected name gets a real `practice_id` before `confirm` is processed.
- [ ] **Anthem check PDFs may be scanned** — if the physical check + remittance is scanned and sent as a PDF, Phase 9 (scanned PDF rendering via pdf2image) is a prerequisite for Phase 15 text extraction. Inspect sample docs early to determine whether Phase 15 is blocked on Phase 9 or can proceed independently.
- [ ] **Insurer classification breadth** — `_detect_insurer()` (Phase 13) uses text-matching heuristics. Anthem operates under multiple brand names (Anthem BCBS, Empire BlueCross, Amerigroup, etc.) — the phrase list will need to be expanded from real EOB text. Document the expansion in `src/medical/extractors/allowlist.py` as a comment alongside each entry.
- [ ] **Annotation CSV privacy** — annotation CSVs contain real medical field values (service dates, amounts, claim numbers). They are safe to commit because they contain no PHI beyond what's already in the user's own SQLite DB, but the sample PDFs themselves must never be committed. Add `experiments/medical/*/sample/` to `.gitignore`.
