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

- [ ] Add tables: `claims`, `claim_external_ids`, `claim_events`, `charges`, `adjudications`
- [ ] Write `src/medical/claims.py` with `create_claim`, `add_external_id`, `find_by_match_key`
- [ ] Implement adjudication revision logic: insert new revision + mark prior superseded + append event
- [ ] Implement `claim_events` append-only log with JSON payload by event_type (serialize via `json.dumps()` and bind as parameterized `?` — never interpolate into SQL)
- [ ] Update `current_status` denormalization on event insert
- [ ] Write tests for create→adjudicate→re-adjudicate sequence, external ID lookup, match-key lookup

## Phase 3: Documents + Payments
**What's true when this is done:** PDF stored on disk, linked polymorphically to claim/encounter/procedure. Payments recorded with direction. The query "what does Shanelle owe Manhattan Pain Medicine?" returns $4,501.50 for the Sep 23 Siefferman claim plus a $5,275.50 member-held amount surfaced separately.

- [ ] Add tables: `documents`, `document_links`, `payments`, `payment_applications`
- [ ] Implement filesystem layout `data/{chat_id}/documents/yyyy/mm/` + `save_document` helper
- [ ] Write polymorphic linking helpers: `attach_document`, `list_documents_for_entity`
- [ ] Write payment recording with `from_party`/`to_party` + `payment_applications`
- [ ] Build SQL views: `v_claim_obligation`, `v_member_holds`, `v_encounter_balance`
- [ ] Manually populate Sep 23 Siefferman + Mikaberidze claims from the EOB/statement PDFs as fixture; verify all three queries return expected amounts

## Phase 4: Telegram Ingestion + Confirmation Flow
**What's true when this is done:** Forward an EOB PDF to Luigi, get a batched confirmation message listing extracted entities and proposed links, reply "confirm" or with corrections, see data persisted. All three Sep 23 PDFs route end-to-end correctly.

- [ ] Extend `telegram_handler.py` to accept Document/Photo messages, save to disk, handle 20MB cap with graceful user message
- [ ] Add 60-second photo grouping buffer keyed by `chat_id` (in-memory dict, flush on timeout; ephemeral by design — photos dropped on process restart are acceptable in v1)
- [ ] Add OpenRouter vision client wrapper in `src/medical/extraction.py`, model-swappable via env var (default `anthropic/claude-sonnet-4.6`)
- [ ] Add dependencies before starting: `pydantic>=2.0.0`, `rapidfuzz>=3.0.0`, `Pillow>=10.0.0`, `PyPDF2>=4.0.0` to `requirements.txt`
- [ ] Define Pydantic schemas + extraction prompts for `bill_or_statement`, `eob`, `receipt`
- [ ] Build practice/provider matcher: alias exact match → rapidfuzz fallback → propose-new
- [ ] Build claim matcher using `(service_date, billing_practice_id, billed_amount)` key
- [ ] Build batched confirmation message generator + reply parser ("yes" / numbered corrections / free-text edits)
- [ ] Run all three Sep 23 PDFs end-to-end as the integration test

## Phase 5: Reconciliation & Alerts
**What's true when this is done:** `/balance` returns total outstanding by practice. Re-adjudications trigger a Telegram alert. Money-held-for-provider unforwarded >7 days triggers a nudge.

- [ ] Implement net obligation queries: by claim, encounter, practice, global
- [ ] Add Telegram commands: `/balance`, `/pending`, `/readjudications`
- [ ] Build re-adjudication detection (new adjudication revision vs prior superseded) + alert dispatch
- [ ] Build member-holds nudge: payments where `to_party='member'` with no matching outflow >7 days
- [ ] Build monthly summary message via APScheduler job on the 1st of each month

## Blockers & Open Questions
- [x] **DB migration strategy** — use existing `init_db()` pattern: all schema changes go in `src/database.py` inside `init_db()` with idempotent `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN` wrapped in `try/except`. No alembic, no separate migration files.
- [ ] **Multi-insurer support deferred** — single insurer assumed throughout. Revisit when a second insurer enters the picture (HSA/FSA, dental).
- [ ] **HSA/FSA reimbursement** — `from_party='hsa'|'fsa'` is in the schema enum but no automation in v1.

## Reference
- Sep 23 docs: `EOB_20251106_-_923_2.pdf`, `EOB_20251028_-_923_1.pdf`, `923_statement_office.pdf`
- Schema design: see chat thread, May 4 2026
- Two-call extraction pattern: `product_requirements.md` Mar 2 2026 ADR
