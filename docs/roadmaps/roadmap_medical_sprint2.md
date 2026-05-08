# Roadmap: Medical Bill Tracking — Sprint 2
**Goal:** Close the three v1 deferred gaps from Sprint 1 (scanned PDF rendering, multi-photo combining, correction loop), add document intelligence that learns EOB/statement page structure so extraction focuses only on content-bearing pages, and fix claim matching so billed-amount mismatches between bills and EOBs surface as an explicit user prompt rather than silently creating duplicate claims.
**Depends on:** Sprint 1 complete (Phases 1–8 of `roadmap_medical_bill_tracking.md`)
**Estimated scope:** 2–3 weeks
**Status:** Not started
**Last updated:** 2026-05-07

---

## Phase 9: Scanned PDF Rendering + Multi-Photo Combining
**What's true when this is done:** A scanned-only EOB PDF (no selectable text) produces a valid `ExtractionResult`. An album of N photos sent to the bot produces one extraction that covers all N images in a single vision call — no pages silently dropped.

- [ ] Add `pdf2image>=1.16.0` to `requirements.txt`; add a comment documenting the required Poppler system dependency (`apt: poppler-utils` / `brew: poppler`)
- [ ] In `src/medical/extraction.py`: after pypdf text extraction, check `len(text.strip()) < SPARSE_TEXT_THRESHOLD` (tune threshold, suggest 100 chars per page); if sparse, rasterize via `pdf2image.convert_from_path()` and build a multi-image base64 payload instead of a text payload — reuse the existing image vision call path
- [ ] In `src/medical/ingestion.py` `_flush_photo_group`: collect all `file_id` values buffered for the group before calling extraction; pass the full list to `extract_from_file` (extend signature to accept `List[str]` file paths)
- [ ] Extend `extraction.py` `extract_from_file` to accept a list of image paths and pack them as multiple `image_url` content blocks in one vision call (OpenRouter supports multi-image in a single message)
- [ ] Write tests: sparse-PDF branch triggers rasterization path (mock `pdf2image`); album of 3 images produces one `ExtractionResult` with all 3 source images in the payload; dense-text PDF still uses text path (no rasterization)

---

## Phase 10: Correction Loop
**What's true when this is done:** When a confirmation message shows an unrecognized practice or wrong date and the user replies with a numbered correction (e.g. `1: Manhattan Pain Med`), Luigi re-renders the confirmation with the correction applied. The user then replies `confirm` to commit. Corrections round-trip correctly for practice name, provider name, and service date. After 3 correction rounds without a `confirm`, Luigi prompts to confirm or cancel to prevent unbounded loops.

- [ ] In `src/medical/confirmation.py`: add `apply_correction(pending: dict, action: dict) -> dict` — pure function that updates `pending["match_results"]` (and `pending["practice_id_by_name"]` for name corrections) based on a parsed correction action; returns an updated copy of `pending`
- [ ] In `src/medical/matching.py`: add `rematch_after_correction(db_path: str, pending: dict) -> dict` — re-runs `match_practice` / `match_provider` / `match_claim` only on fields that were corrected; returns updated `match_results`
- [ ] In `src/medical/ingestion.py`: on `correction` reply action from `parse_confirmation_reply`, call `apply_correction` → `rematch_after_correction` → `build_confirmation_message`; store updated pending and re-send the confirmation message; track correction round count in the pending dict
- [ ] Cap correction rounds at 3; on the 4th correction attempt, send: _"I've applied 3 corrections. Reply `confirm` to save or `cancel` to discard."_ — do not re-render
- [ ] Write tests: correction re-renders confirmation with updated practice name; correction on service date updates claim match lookup; `cancel` reply after correction discards pending and sends cancellation message; 3-round cap sends final prompt instead of re-rendering

---

## Phase 11: Document Intelligence — Layout Learning
**What's true when this is done:** On the second EOB ingestion from the same practice, Luigi skips blank/boilerplate pages and sends only content-bearing pages to the vision model. The learned page range is stored per `(doc_type, practice_id)` and reused automatically. First-time ingestion auto-detects relevant pages using text-density scoring and persists the result.

- [ ] Add `document_templates` table to `src/database.py` `init_db()`:
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
- [ ] Write `src/medical/layout.py` with:
  - `score_page_relevance(page_text: str) -> float` — ratio of non-whitespace chars to total chars; 0.0 = blank, 1.0 = dense
  - `detect_relevant_pages(pages: list[str], threshold: float = 0.05) -> list[int]` — returns indices of pages whose score exceeds threshold; excludes leading and trailing blank pages
  - `load_template(db_path: str, doc_type: str, practice_id: Optional[int]) -> Optional[list[int]]` — returns stored relevant page indices or `None`
  - `update_template(db_path: str, doc_type: str, practice_id: Optional[int], observed_pages: list[int]) -> None` — upsert; on conflict, expand the stored range to the union of stored and observed (conservative: never drop a page seen in prior samples); increment `sample_count`
- [ ] In `src/medical/extraction.py`: after reading PDF pages, call `load_template` → if found, filter `pages` list to stored indices before building the extraction payload; if not found, call `detect_relevant_pages` on all pages, filter to detected range, then call `update_template` to persist it for next time
- [ ] Extend `extract_from_file` to accept an optional `db_path: Optional[str]` and `practice_id: Optional[int]` so the template lookup has the right keys; callers that don't need layout learning can pass `None`
- [ ] Write tests: `score_page_relevance` returns 0.0 for blank page, >0.5 for dense page; `detect_relevant_pages` strips leading and trailing blanks; `update_template` union-expands on second call; extraction uses stored range on second call (mock `load_template`); extraction stores template on first call (mock `update_template`, assert called)

---

## Phase 12: Claim Matching — Amount-Tolerance + Ambiguity Prompt
**What's true when this is done:** Sending an EOB for a visit whose bill was already uploaded no longer silently creates a duplicate claim when the billed amounts differ. When an exact match fails but a `submitted` claim exists for the same `(service_date, practice_id)`, Luigi flags the ambiguity in the confirmation message and asks the user to link or treat as separate. The `matched_claims_by_date` collision bug for multi-claim same-date visits is also fixed.

- [ ] In `src/medical/claims.py`: add `find_submitted_by_date_and_practice(db_path: str, service_date: str, practice_id: int) -> list[dict]` — returns all claims with `current_status = 'submitted'` for the given `(service_date, practice_id)`, ordered by `created_at`
- [ ] In `src/medical/matching.py`: update `match_claim` — after `find_by_match_key` returns `None`, call `find_submitted_by_date_and_practice`; if one result is found, return it as a `suggested_link` (not `matched=True`) with `match_type='prior_bill'`; if multiple are found, return all as suggestions; if none, return unmatched as before
- [ ] In `src/medical/confirmation.py`: update `build_confirmation_message` to render `suggested_link` entries distinctly — e.g. _"⚠️ No exact match, but a prior bill exists for this visit (billed $X on [date]). Reply `link` to connect this EOB to it, or `confirm` to create a separate claim."_
- [ ] In `src/medical/ingestion.py`: handle the new `link` reply action from `parse_confirmation_reply` — set `existing_claim_id` to the suggested claim's id and proceed as a matched claim (adjudicate only, no new claim created); update `parse_confirmation_reply` in `confirmation.py` to recognise `link` as a valid keyword
- [ ] Fix `matched_claims_by_date` in `commit_ingestion`: change the key from `service_date` to `(service_date, billed_amount)` so two claims for the same date but different amounts each resolve independently
- [ ] Write tests: exact-match path unchanged; `find_submitted_by_date_and_practice` returns prior bill when amounts differ; confirmation message renders `suggested_link` warning; `link` reply adjudicates existing claim without creating a new one; two claims same date different amounts both resolve correctly after the key fix

---

## Blockers & Open Questions

- [ ] **Poppler system dependency** — `pdf2image` requires Poppler binaries installed at the OS level. This needs to be documented in the project README and added to any deployment scripts or Dockerfiles. The VPS deployment process must be updated before Phase 9 ships.
- [ ] **Template keying by insurer vs. practice** — the `document_templates` schema keys templates on `(doc_type, practice_id)`. EOBs from the same insurer but different practices may have identical layouts. If this proves true in practice, add an `insurer_id` key column and a fallback lookup chain: `(doc_type, practice_id)` → `(doc_type, insurer_id)` → `(doc_type, NULL)`. Deferred until second insurer enters the picture.
- [ ] **Multi-image vision token cost** — combining N album photos into one vision call increases per-call token usage. For 3-page photo albums this is acceptable; for large albums (>6 images) it may exceed model context or become expensive. Add a `MAX_ALBUM_IMAGES` guard (suggest 6) with a user-facing message if exceeded.
- [ ] **Correction loop and `practice_id_by_name` consistency** — `apply_correction` must keep `practice_id_by_name` in sync when a practice name correction resolves to an existing DB row vs. a new entity. The `rematch_after_correction` step must re-run `match_practice` against the DB so the corrected name gets a real `practice_id` before `confirm` is processed.
