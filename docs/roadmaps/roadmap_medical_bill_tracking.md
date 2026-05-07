# Roadmap: Medical Bill Tracking
**Goal:** Add bill/EOB/statement tracking to Luigi — ingest via Telegram, link to encounters and procedures, compute net obligation including paid-to-member and re-adjudication cases.
**Depends on:** existing Luigi (per-user SQLite, `telegram_handler.py`, OpenRouter client)
**Estimated scope:** weeks
**Status:** Not started
**Last updated:** 2026-05-04

## Phase 1: Core Entities + Code Lookups
**What's true when this is done:** Can manually create an encounter at a practice with multiple providers and procedures coded to CPT/ICD. Aliases resolve "Manhattan Pain Medicine" and "Dr. Deborah Barbiere Psy.D., L.Ac." to the same practice.

- [x] Add tables to `data/{chat_id}.db`: `practices`, `practice_aliases`, `providers`, `provider_aliases`, `provider_practice_affiliations`, `insurers`
- [x] Add tables: `encounters`, `procedures`, `cpt_codes`, `icd_codes`
- [x] Download HCPCS/CPT data from CMS, load into `cpt_codes` (one-time seed script in `src/medical/scripts/`)
- [x] Download ICD-10-CM data from CMS, load into `icd_codes` (one-time seed script in `src/medical/scripts/`)
- [x] Write `src/medical/entities.py` with CRUD + alias-resolve helpers
- [x] Write tests for alias resolution, affiliation queries, encounter+procedure creation

### Handoff — Phase 1
**Completed:** 2026-05-05
**Branch:** main
**Tests:** pytest tests/ -x -q — 142 passed

#### What was built
Phase 1 adds the core billing schema (10 new tables) to each user's SQLite DB via idempotent `init_db()` migrations, along with a CRUD + alias-resolution module (`src/medical/entities.py`) and two stdlib-only seed scripts for loading CMS CPT/HCPCS and ICD-10-CM code data. Foreign key enforcement (`PRAGMA foreign_keys = ON`) was also added to `get_connection()`, hardening the entire DB layer.

#### Files changed
- `src/database.py` — `PRAGMA foreign_keys = ON` in `get_connection()`; 10 new `CREATE TABLE IF NOT EXISTS` blocks in `init_db()` with FK-ordering comment for Phase 2's `claims` table
- `src/medical/__init__.py` — empty package marker
- `src/medical/scripts/__init__.py` — empty package marker
- `src/medical/entities.py` — 12 functions: practice/provider CRUD, alias resolution (`resolve_practice`, `resolve_provider`, `resolve_entity_to_practice`), affiliation helpers, encounter + procedure creation
- `src/medical/scripts/seed_cpt_codes.py` — CLI seed script for CMS HCPCS data; `--db-path` + optional `--source-url`
- `src/medical/scripts/seed_icd_codes.py` — CLI seed script for CMS ICD-10-CM data; same interface
- `tests/test_medical_entities.py` — 14 tests covering schema creation, CRUD, alias resolution, provider→practice resolution via affiliation, encounter/procedure linking

#### How to verify manually
1. `pytest tests/test_medical_entities.py -v` — all 14 tests pass
2. Python REPL: `from src.database import init_db; from src.medical.entities import *; init_db("test.db"); p = create_practice("test.db", "Manhattan Pain Medicine"); add_practice_alias("test.db", p["id"], "MPM"); resolve_entity_to_practice("test.db", "MPM")` — should return the practice row
3. `resolve_entity_to_practice` via provider name: create provider, affiliate to practice, resolve by provider name — returns practice dict

#### Open questions / deferred decisions
- Seed script URLs point to 2024 CMS releases (best-effort); verify current URLs before running in production. Use `--source-url` to override.
- `PRAGMA foreign_keys = ON` is now active on every connection — a real-user DB with orphaned rows (unlikely, but possible) would surface new integrity errors on next access. Worth a dry-run on a copy of a live DB before deploying.
- Phase 2's `claims` table must be added after `practices` in `init_db()` — the comment is in place.

## Phase 2: Claims & Adjudication Lifecycle
**What's true when this is done:** Can represent the Sep 23 Siefferman claim with its 11/06 EOB adjudication and the 8/27 Mikaberidze re-adjudication with full event history. Can find a claim by `(service_date, billing_practice_id, billed_amount)`.

- [x] Add tables: `claims`, `claim_external_ids`, `claim_events`, `charges`, `adjudications`
- [x] Write `src/medical/claims.py` with `create_claim`, `add_external_id`, `find_by_match_key`
- [x] Implement adjudication revision logic: insert new revision + mark prior superseded + append event
- [x] Implement `claim_events` append-only log with JSON payload by event_type (serialize via `json.dumps()` and bind as parameterized `?` — never interpolate into SQL)
- [x] Update `current_status` denormalization on event insert
- [x] Write tests for create→adjudicate→re-adjudicate sequence, external ID lookup, match-key lookup

### Handoff — Phase 2
**Completed:** 2026-05-05
**Branch:** main
**Tests:** pytest tests/ -x -q — 156 passed

#### What was built
Phase 2 adds the claims and adjudication lifecycle: 5 new tables (`claims`, `claim_external_ids`, `charges`, `adjudications`, `claim_events`) via idempotent `init_db()` migrations, and a new module `src/medical/claims.py` with 7 functions covering claim creation, external ID management, match-key/external-ID lookup, and a fully atomic `adjudicate_claim` that handles initial adjudication and re-adjudications with a `superseded_by` chain and append-only event journaling.

#### Files changed
- `src/database.py` — 5 new `CREATE TABLE IF NOT EXISTS` blocks in `init_db()` after Phase 1 tables, all with `claim_id NOT NULL` and proper FK ordering
- `src/medical/claims.py` — new module: `_append_event`, `create_claim`, `add_external_id`, `find_by_match_key`, `find_by_external_id`, `adjudicate_claim`, `get_claim_events`
- `tests/test_medical_claims.py` — 14 tests covering create, duplicate rejection, event log, external ID CRUD, match-key lookup (exact + off-by-one), first adjudication, 2-revision re-adjudication, 3-revision superseded chain, event ordering

#### How to verify manually
1. `pytest tests/test_medical_claims.py -v` — all 14 tests pass
2. Python REPL sequence:
   ```python
   from src.database import init_db
   from src.medical.entities import create_practice
   from src.medical.claims import *
   init_db("test.db")
   p = create_practice("test.db", "Manhattan Pain Medicine")
   c = create_claim("test.db", "2025-09-23", p["id"], 250.00)
   adjudicate_claim("test.db", c["id"], "2025-11-06", 200.00, 160.00, 40.00)
   adjudicate_claim("test.db", c["id"], "2025-08-27", 210.00, 168.00, 42.00)
   get_claim_events("test.db", c["id"])  # → 3 events: created, adjudicated, readjudicated
   find_by_match_key("test.db", "2025-09-23", p["id"], 250.00)  # → current_status: 'readjudicated'
   ```

#### Open questions / deferred decisions
- `charges` table is created but has no CRUD helpers yet — deferred to Phase 3 or whenever line-item charges need to be surfaced in queries.
- `billed_amount NOT NULL` constraint means claims with unknown billed amounts cannot be inserted. If this edge case arises before Phase 4 ingestion, add a `0.0` default sentinel or lift the constraint and add a NULL guard in `find_by_match_key`.
- The `adjudicate_claim` rollback wraps `conn.rollback()` in its own try/except to avoid masking the original error — this pattern should be replicated in any future multi-step DB transactions.

## Phase 3: Documents + Payments
**What's true when this is done:** PDF stored on disk, linked polymorphically to claim/encounter/procedure. Payments recorded with direction. The query "what does Shanelle owe Manhattan Pain Medicine?" returns $4,501.50 for the Sep 23 Siefferman claim plus a $5,275.50 member-held amount surfaced separately.

- [x] Add tables: `documents`, `document_links`, `payments`, `payment_applications`
- [x] Implement filesystem layout `data/{chat_id}/documents/yyyy/mm/` + `save_document` helper
- [x] Write polymorphic linking helpers: `attach_document`, `list_documents_for_entity`
- [x] Write payment recording with `from_party`/`to_party` + `payment_applications`
- [x] Build SQL views: `v_claim_obligation`, `v_member_holds`, `v_encounter_balance`
- [x] Manually populate Sep 23 Siefferman + Mikaberidze claims from the EOB/statement PDFs as fixture; verify all three queries return expected amounts

### Handoff — Phase 3
**Completed:** 2026-05-06
**Branch:** main
**Tests:** pytest tests/ -x -q — 173 passed

#### What was built
Phase 3 adds document storage (filesystem + DB), polymorphic document linking, payment recording and application, and three SQL views that compute net member obligation, member-held insurer payments, and per-encounter balance rollups. A fixture seed script hard-codes the Sep 23 Siefferman and Mikaberidze claim data to verify the views return the expected headline figures.

#### Files changed
- `src/database.py` — 4 new tables (`documents`, `document_links`, `payments`, `payment_applications`) + 3 views (`v_claim_obligation`, `v_member_holds`, `v_encounter_balance`) added to `init_db()`, all idempotent via `IF NOT EXISTS`
- `src/medical/documents.py` — new module: `_resolve_document_path` (yyyy/mm + uuid8 suffix to prevent filename collisions), `save_document`, `attach_document`, `list_documents_for_entity`
- `src/medical/payments.py` — new module: `record_payment`, `apply_payment`, `get_payment_applications` (joined to parent payment row)
- `src/medical/scripts/seed_sep23_fixture.py` — CLI seed script (`--db-path`); hard-coded literals for Sep 23 Siefferman ($4,501.50 net obligation) and Mikaberidze ($5,275.50 member-held); prints all three views; idempotent on claims via `find_by_match_key`
- `tests/test_medical_documents.py` — 17 new tests including four review-mandated bug-catchers: submitted-claim visibility in `v_claim_obligation`, GROUP-BY stability across re-adjudications, filename collision prevention, and multi-claim encounter balance

#### How to verify manually
1. `pytest tests/test_medical_documents.py -v` — all 17 tests pass
2. `python -m src.medical.scripts.seed_sep23_fixture --db-path /tmp/luigi_p3_demo.db` — prints v_claim_obligation, v_member_holds, v_encounter_balance; Siefferman row shows `net_obligation=4501.5`, Mikaberidze row shows a `v_member_holds` entry of `held_amount=5275.5`

#### Open questions / deferred decisions
- Sep 23 fixture dollar amounts are reasoned literals; Phase 4 PDF extraction will reconcile them against source EOBs — update the seed script then.
- The seed script is idempotent on claims but NOT on adjudications; re-running on a populated DB appends extra `superseded_by` chain entries. Reset by deleting the DB.
- `documents_dir` is a parameter to `save_document` for now; Phase 4 will wire it to a `settings.documents_dir` config value alongside `settings.database_dir`.
- `document_links.entity_id` has no SQL FK (polymorphic table); application code must validate referenced entity existence.

## Phase 4: Telegram Ingestion + Confirmation Flow
**What's true when this is done:** Forward an EOB PDF to Luigi, get a batched confirmation message listing extracted entities and proposed links, reply "confirm" or with corrections, see data persisted. All three Sep 23 PDFs route end-to-end correctly.

- [x] Extend `telegram_handler.py` to accept Document/Photo messages, save to disk, handle 20MB cap with graceful user message
- [x] Add 60-second photo grouping buffer keyed by `chat_id` (in-memory dict, flush on timeout; ephemeral by design — photos dropped on process restart are acceptable in v1)
- [x] Add OpenRouter vision client wrapper in `src/medical/extraction.py`, model-swappable via env var (default `anthropic/claude-sonnet-4-6`)
- [x] Add dependencies before starting: `pydantic>=2.0.0`, `rapidfuzz>=3.0.0`, `Pillow>=10.0.0`, `pypdf>=4.0.0` to `requirements.txt`
- [x] Define Pydantic schemas + extraction prompts for `bill_or_statement`, `eob`, `receipt`
- [x] Build practice/provider matcher: alias exact match → rapidfuzz fallback → propose-new
- [x] Build claim matcher using `(service_date, billing_practice_id, billed_amount)` key
- [x] Build batched confirmation message generator + reply parser ("yes" / numbered corrections / free-text edits)
- [x] Run all three Sep 23 PDFs end-to-end as the integration test

### Handoff — Phase 4
**Completed:** 2026-05-07
**Branch:** main
**Tests:** pytest tests/ -x -q — 188 passed (173 prior + 15 new)

#### What was built
Phase 4 wires Telegram document/photo ingestion to the medical bill schema. Files sent to the bot are saved to disk, parsed by an OpenRouter vision LLM (`extraction.py`), matched against existing practices/providers/claims (`matching.py`), and surfaced as a numbered confirmation message (`confirmation.py`). Replying "confirm" triggers the actual claim/adjudication DB writes (`ingestion.py`). Multi-photo albums are buffered for 60 seconds before flushing. Pending confirmations expire after 10 minutes via `job_queue.run_once`.

#### Files changed
- `requirements.txt` — added `pydantic>=2.0.0`, `rapidfuzz>=3.0.0`, `Pillow>=10.0.0`, `pypdf>=4.0.0`
- `src/config.py` — added `DOCUMENTS_DIR` (required, in `required_vars`) + `VISION_MODEL` (optional, default `anthropic/claude-sonnet-4-6`)
- `src/medical/extraction.py` — new: Pydantic schemas (`ExtractionResult`, `ExtractedClaim`, `ExtractedAdjudication`, `ExtractedPractice`, `ExtractedProvider`); `STATEMENT_PROMPT`/`EOB_PROMPT`/`RECEIPT_PROMPT`; synchronous `extract_from_file()` routing PDFs through pypdf text extraction and images through base64 vision calls
- `src/medical/matching.py` — new: `match_practice()` / `match_provider()` (exact → rapidfuzz score≥85 → propose-new) + `match_claim()` (thin wrapper over `find_by_match_key`)
- `src/medical/confirmation.py` — new: pure `build_confirmation_message()` (numbers only action-required items) + `parse_confirmation_reply()` (confirm / numbered correction / free-text fallthrough)
- `src/medical/ingestion.py` — new: async `ingest_document()` orchestrator, `commit_ingestion()` DB writer, `handle_photo_group()` + `_flush_photo_group()` with cancel-and-reschedule semantics, `_expire_confirmation()` TTL job; module-level ephemeral state dicts
- `src/telegram_handler.py` — added `_on_document` (20MB cap), `_on_photo` (single vs. media-group routing), confirmation intercept at top of `_on_message`, registered `Document.ALL` and `PHOTO` handlers
- `tests/test_medical_extraction.py` — new: 15 tests (14 unit + 1 async integration with mocked LLM + save_document)

#### How to verify manually
1. `pytest tests/ -x -q` — 188 passed
2. Set `DOCUMENTS_DIR=./data/documents` in `.env`
3. Send an EOB PDF to the bot — expect a confirmation message listing matched/unmatched practices and claims
4. Reply `confirm` — expect "Saved." and the claim row visible via `find_by_match_key`
5. Send 2–3 photos in a single album — expect one confirmation ~60 seconds after the last photo
6. Send a document and wait 10 minutes without replying — expect a TTL expiry notice

#### Open questions / deferred decisions
- **Scanned PDFs:** v1 uses pypdf text extraction only. Sparse text on scanned/image PDFs produces best-effort extraction. Rendering to raster (requires `pdf2image` + system-level Poppler) is deferred.
- **Multi-photo albums:** `_flush_photo_group` ingests only the first photo. Combining N photos into a single multi-image vision call is deferred.
- **Correction flow:** `parse_confirmation_reply` returns `correction` actions but the ingestion pipeline only acknowledges them — it does not re-render the confirmation with the correction applied. A full correction loop is out of scope for v1.
- **Photo buffer key:** uses `(chat_id, media_group_id)`; single photos flush immediately. If a user sends an unrelated photo within 60 seconds of an album, they get separate confirmations — correct behavior.
- **End-to-end Sep 23 PDF test:** the integration test uses mocked extraction results representing the Sep 23 Siefferman EOB. A live PDF test requires a running bot + real OpenRouter key.

## Phase 5: Reconciliation & Alerts
**What's true when this is done:** `/balance` returns total outstanding by practice. Re-adjudications trigger a Telegram alert. Money-held-for-provider unforwarded >7 days triggers a nudge.

- [x] Implement net obligation queries: by claim, encounter, practice, global
- [x] Add Telegram commands: `/balance`, `/pending`, `/readjudications`
- [x] Build re-adjudication detection (new adjudication revision vs prior superseded) + alert dispatch
- [x] Build member-holds nudge: payments where `to_party='member'` with no matching outflow >7 days
- [x] Build monthly summary message via APScheduler job on the 1st of each month

### Handoff — Phase 5
**Completed:** 2026-05-07
**Branch:** main
**Tests:** pytest tests/ -x -q — 203 passed (188 prior + 15 new)

#### What was built
Phase 5 closes the reconciliation loop. New `src/medical/queries.py` exposes read-only obligation/holds/re-adjudication queries built on the Phase-3 SQL views (extended with `practice_name` for human-readable output). New Telegram commands `/balance`, `/pending`, and `/readjudications` surface those queries on demand. New `src/medical/alerts.py` defines three async alert dispatchers wired to APScheduler as global daily/monthly cron jobs (re-adjudication alert at 9:00, member-holds nudge at 10:00, monthly summary on day-1 at 8:00).

#### Files changed
- `src/database.py` — extended all three Phase-3 views to include `practice_name`. Switched `CREATE VIEW IF NOT EXISTS` to `DROP VIEW IF EXISTS` + `CREATE VIEW` so view definition changes are picked up on every `init_db()` call. `v_member_holds` also gained `billing_practice_id` and `service_date`.
- `src/medical/queries.py` — new module: `get_claim_obligation`, `get_obligations_by_practice`, `get_global_obligations`, `get_encounter_balance`, `get_member_holds_overdue`, `get_readjudicated_claims`, `get_recent_readjudication_events` (the only one querying `claim_events` directly rather than a view).
- `src/medical/alerts.py` — new module: async `send_readjudication_alerts`, `send_member_holds_nudge`, `send_monthly_summary`. All catch top-level exceptions and log with `exc_info=True`.
- `src/telegram_handler.py` — added `balance_command`, `pending_command`, `readjudications_command`; registered them in `create_application()` along with the previously unregistered `/schedule`.
- `src/scheduler.py` — added `register_medical_alert_jobs(scheduler, database_dir, timezone)` which registers three global cron jobs (idempotent via `replace_existing=True`). Called from `schedule_check_ins`.
- `tests/test_medical_queries.py` — 15 new tests covering all query functions, member-holds nudge dispatch (with mocked `send_message`), and `/balance` formatting.

## Phase 6: Encounter Stubs from EOB Ingestion
**What's true when this is done:** Sending an EOB for an appointment not yet in the system automatically creates a minimal encounter (service_date + practice_id) and links the new claim to it. `/balance` via `v_encounter_balance` rolls up correctly without any manual encounter entry.

- [x] Add `find_encounter_by_date_and_practice(db_path, service_date, practice_id) -> Optional[dict]` to `src/medical/entities.py`
- [x] Extend `create_claim` in `src/medical/claims.py` to accept an optional `encounter_id` parameter and include it in the INSERT
- [x] In `commit_ingestion` (`src/medical/ingestion.py`): before creating a new claim, look up or create a minimal encounter; pass `encounter_id` to `create_claim`
- [x] Only create encounter stubs for new claims — matched (existing) claims are left untouched
- [x] Write tests: new EOB creates encounter + linked claim; second EOB for same visit reuses existing encounter; existing claim with encounter_id already set is not overwritten

### Handoff — Phase 6
**Completed:** 2026-05-07
**Branch:** main
**Tests:** pytest tests/ -x -q — 208 passed

#### What was built
Phase 6 closes the gap between claim ingestion and encounter tracking. When `commit_ingestion` creates a new claim, it now first looks up an existing encounter for `(service_date, practice_id)` and creates a minimal stub if none exists, then passes the `encounter_id` to `create_claim`. A unique index on `encounters(service_date, practice_id)` enforces one encounter per visit, making the find-or-create safe on retries. Matched (existing) claims are left completely untouched.

#### Files changed
- `src/medical/entities.py` — new `find_encounter_by_date_and_practice(db_path, service_date, practice_id) -> Optional[dict]` at line 402; follows `resolve_practice` pattern (parameterized SELECT, fetchone, finally close)
- `src/database.py` — `CREATE UNIQUE INDEX IF NOT EXISTS idx_encounters_date_practice ON encounters(service_date, practice_id)` added to `init_db()` after the encounters table
- `src/medical/ingestion.py` — added `create_encounter` and `find_encounter_by_date_and_practice` to entity import block; in `commit_ingestion`'s new-claim branch, inserted find-or-create encounter logic (both calls wrapped in `asyncio.to_thread`) before `create_claim`; `create_claim` now receives explicit `encounter_id` kwarg
- `tests/test_medical_entities.py` — two unit tests for `find_encounter_by_date_and_practice` (returns None on empty, returns row on match)
- `tests/test_medical_ingestion.py` — new file with three behavioral tests: new-EOB creates stub + linked claim; second EOB reuses existing encounter; matched claim is not overwritten

#### How to verify manually
1. `pytest tests/test_medical_ingestion.py tests/test_medical_entities.py -v` — all pass
2. Python REPL: `from src.database import init_db; from src.medical.entities import *; from src.medical.ingestion import commit_ingestion` — create a practice, build a minimal pending dict with `claim_match: None`, call `commit_ingestion`, then verify `find_encounter_by_date_and_practice` returns a row and the claim's `encounter_id` matches it
3. Call `commit_ingestion` a second time with the same `service_date` and `practice_id` but different `billed_amount` — only one encounter row should exist in `SELECT COUNT(*) FROM encounters`

#### Open questions / deferred decisions
- `create_encounter` is called with `provider_id=None, notes=None` — Phase 7 will fill `provider_id` from the EOB rendering provider field. The stub is intentionally minimal.
- The unique index was added as a `CREATE UNIQUE INDEX IF NOT EXISTS` (not a table constraint) so it applies idempotently to existing live DBs without ALTER TABLE.
- `commit_ingestion` adopts a best-effort policy on encounter creation failure: if `create_encounter` returns `None`, the claim is still created with `encounter_id=None` rather than aborting the entire ingestion.

## Phase 7: Auto-Link Provider from EOB
**What's true when this is done:** When an EOB names a rendering provider, Luigi matches or creates that provider and links them to the encounter stub created in Phase 6. The provider appears in the encounter record without manual entry.

- [ ] Extend `ExtractionResult` / `ExtractedProvider` schema in `src/medical/extraction.py` to capture the rendering provider name per claim (if present in the EOB)
- [ ] In `commit_ingestion`: after resolving the encounter, match or create the provider (`match_provider` → `create_provider`), then update `encounters.provider_id` via a new `set_encounter_provider(db_path, encounter_id, provider_id)` helper in `entities.py`
- [ ] Only set provider if the encounter's `provider_id` is currently NULL — never overwrite a manually-set provider
- [ ] Write tests: EOB with provider name sets encounter.provider_id; second EOB with different provider does not overwrite; EOB with no provider leaves provider_id NULL

## Phase 8: Gap Review & Scoping
**What's true when this is done:** The roadmap has been reviewed against real usage, open questions are resolved or deferred with explicit reasoning, and the next build cycle has a clear scope.

- [ ] Review all open questions from Phase 1–7 handoffs and the Blockers section below
- [ ] Test end-to-end with the Sep 23 EOB PDFs after Phases 6–7 land: confirm encounter stubs created, providers linked, `/balance` totals correct
- [ ] Identify any gaps: missing commands, edge cases in the confirmation flow, scanned-PDF handling (deferred in Phase 4), multi-photo album combining (deferred in Phase 4)
- [ ] Decide: HSA/FSA reimbursement automation scope (currently deferred in Blockers)
- [ ] Decide: multi-insurer support scope (currently deferred in Blockers)
- [ ] Write a short findings doc or update this roadmap with decisions made

## Blockers & Open Questions
- [x] **DB migration strategy** — use existing `init_db()` pattern: all schema changes go in `src/database.py` inside `init_db()` with idempotent `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN` wrapped in `try/except`. No alembic, no separate migration files.
- [ ] **Multi-insurer support deferred** — single insurer assumed throughout. Revisit when a second insurer enters the picture (HSA/FSA, dental).
- [ ] **HSA/FSA reimbursement** — `from_party='hsa'|'fsa'` is in the schema enum but no automation in v1.

## Reference
- Sep 23 docs: `EOB_20251106_-_923_2.pdf`, `EOB_20251028_-_923_1.pdf`, `923_statement_office.pdf`
- Schema design: see chat thread, May 4 2026
- Two-call extraction pattern: `product_requirements.md` Mar 2 2026 ADR
