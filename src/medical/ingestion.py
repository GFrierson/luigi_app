"""
Document ingestion pipeline for medical documents (Phase 4).

Public entry points (all async):
    - ingest_document(...)          — single doc end-to-end
    - handle_photo_group(...)       — buffer multi-photo album, flush after 60s
    - commit_ingestion(...)         — apply DB writes after user confirms

Internal job callbacks (registered via context.job_queue):
    - _expire_confirmation
    - _flush_photo_group

Module-level state (ephemeral; drops on process restart — acceptable in v1):
    - _photo_buffer       : (chat_id, media_group_id) -> [bytes]
    - _photo_group_jobs   : (chat_id, media_group_id) -> job handle
    - _pending_confirmations : chat_id -> pending state dict

Pipeline shape (transformation BEFORE writes):
    save_document -> extract_from_file -> match_practice/match_claim ->
    build_confirmation_message -> store pending state + send message.
The actual claim creation / adjudication writes happen in commit_ingestion
after the user replies "confirm".
"""

import asyncio
import logging
from typing import Optional

from src.medical.claims import adjudicate_claim, create_claim
from src.medical.confirmation import build_confirmation_message
from src.medical.documents import attach_document, save_document
from src.medical.entities import (
    add_practice_alias,
    add_provider_alias,
    create_encounter,
    create_practice,
    create_provider,
    find_encounter_by_date_and_practice,
    set_encounter_provider,
)
from src.medical.extraction import ExtractionResult, extract_from_file
from src.medical.matching import match_claim, match_practice, match_provider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level ephemeral state
# ---------------------------------------------------------------------------

# Multi-photo album buffer. Single photos use the key (chat_id, "single")
# but are flushed immediately by the caller — no scheduled job needed.
_photo_buffer: dict[tuple[int, str], list[bytes]] = {}

# Pending photo-group flush jobs, keyed identically to _photo_buffer so we
# can cancel + reschedule when more photos in the same album arrive.
_photo_group_jobs: dict[tuple[int, str], object] = {}

# Pending user-confirmation state per chat_id.
_pending_confirmations: dict[int, dict] = {}

_CONFIRMATION_TTL_SECONDS = 600  # 10 minutes
_PHOTO_GROUP_FLUSH_SECONDS = 60


# ---------------------------------------------------------------------------
# ingest_document
# ---------------------------------------------------------------------------

async def ingest_document(
    db_path: str,
    documents_dir: str,
    chat_id: int,
    file_bytes: bytes,
    original_name: str,
    mime_type: str,
    context,  # telegram.ext.CallbackContext (typed loosely to avoid hard import)
) -> str:
    """
    End-to-end ingestion for a single document. Returns the user-facing
    confirmation message string.

    No DB writes for claims/adjudications yet — those happen in commit_ingestion
    after the user replies "confirm". Only the documents row + on-disk file are
    persisted up-front.

    On extraction failure, returns a graceful error message.
    """
    try:
        # Step 1: persist file + insert documents row.
        # We do NOT yet know doc_type; pass 'other' provisionally and fix up
        # later via the extracted classification. (The DB constraint allows
        # 'other' explicitly.)
        saved = await asyncio.to_thread(
            save_document,
            documents_dir,
            chat_id,
            db_path,
            file_bytes,
            original_name,
            mime_type,
            "other",
            None,
            None,
        )
        if not saved:
            logger.error(
                f"ingest_document: save_document returned None for chat {chat_id} "
                f"original_name='{original_name}'"
            )
            return "I couldn't save that document. Please try again."

        document_id = saved["id"]
        file_path = saved["file_path"]

        # Step 2: extract structured data via vision LLM.
        extraction: Optional[ExtractionResult] = await asyncio.to_thread(
            extract_from_file, file_path, mime_type
        )
        if extraction is None:
            logger.warning(
                f"ingest_document: extraction failed for chat {chat_id} "
                f"document_id={document_id}"
            )
            return (
                "I saved the document but couldn't read its contents. "
                "Try a clearer photo or PDF."
            )

        # Step 3: match practices.
        practice_match_results: list[dict] = []
        practice_id_by_name: dict[str, Optional[int]] = {}
        for claim in extraction.claims:
            pname = claim.practice_name
            if pname in practice_id_by_name:
                continue
            matched = await asyncio.to_thread(match_practice, db_path, pname)
            practice_id_by_name[pname] = matched["id"] if matched else None
            practice_match_results.append({
                "name": pname,
                "matched": matched is not None,
                "practice_id": matched["id"] if matched else None,
            })

        # Step 4: match claims (only when we have a resolved practice).
        claim_match_results: list[dict] = []
        for claim in extraction.claims:
            pid = practice_id_by_name.get(claim.practice_name)
            if pid is None:
                claim_match_results.append({
                    "service_date": claim.service_date,
                    "matched": False,
                    "claim_id": None,
                })
                continue
            matched_claim = await asyncio.to_thread(
                match_claim, db_path, claim.service_date, pid, claim.billed_amount
            )
            claim_match_results.append({
                "service_date": claim.service_date,
                "matched": matched_claim is not None,
                "claim_id": matched_claim["id"] if matched_claim else None,
            })

        match_results = {
            "practices": practice_match_results,
            "claims": claim_match_results,
        }

        # Step 5: pure transformation -> confirmation message.
        message = build_confirmation_message(extraction, match_results)

        # Step 6: store pending state for later commit.
        _pending_confirmations[chat_id] = {
            "extraction": extraction,
            "match_results": match_results,
            "document_id": document_id,
            "practice_id_by_name": practice_id_by_name,
        }

        # Step 7: schedule TTL expiry for the pending state.
        try:
            if context is not None and getattr(context, "job_queue", None) is not None:
                context.job_queue.run_once(
                    _expire_confirmation,
                    _CONFIRMATION_TTL_SECONDS,
                    data=chat_id,
                )
        except Exception:
            logger.error(
                f"ingest_document: failed to schedule TTL expiry for chat {chat_id}",
                exc_info=True,
            )

        return message

    except Exception:
        logger.error(
            f"ingest_document: unexpected failure for chat {chat_id} "
            f"original_name='{original_name}'",
            exc_info=True,
        )
        return "Something went wrong while processing that document."


# ---------------------------------------------------------------------------
# Confirmation expiry
# ---------------------------------------------------------------------------

async def _expire_confirmation(context) -> None:
    """Job callback: drop a pending confirmation after the TTL window."""
    try:
        chat_id = context.job.data
        if chat_id in _pending_confirmations:
            del _pending_confirmations[chat_id]
            logger.info(f"Pending confirmation expired for chat {chat_id}")
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "That document confirmation timed out (10 min). "
                        "Re-send the document if you still want to save it."
                    ),
                )
            except Exception:
                logger.error(
                    f"_expire_confirmation: failed to send notice to chat {chat_id}",
                    exc_info=True,
                )
    except Exception:
        logger.error("_expire_confirmation: unexpected failure", exc_info=True)


# ---------------------------------------------------------------------------
# commit_ingestion
# ---------------------------------------------------------------------------

async def commit_ingestion(db_path: str, chat_id: int, pending: dict) -> None:
    """
    Apply DB writes after the user confirms a pending extraction.

    For each extracted claim:
      - If practice unmatched, create a new practice (with extracted aliases).
      - If claim unmatched, create_claim. If matched, reuse existing claim_id.
      - For each adjudication on this extraction, call adjudicate_claim.
      - attach_document to link the source file to the claim.

    Best-effort: failures on any one claim are logged but do not abort the
    overall commit. Never raises.
    """
    try:
        extraction: ExtractionResult = pending["extraction"]
        match_results: dict = pending["match_results"]
        document_id: int = pending["document_id"]
        practice_id_by_name: dict[str, Optional[int]] = pending.get(
            "practice_id_by_name", {}
        )

        # Build a quick lookup of which claims already matched.
        matched_claims_by_date: dict[str, Optional[int]] = {}
        for entry in match_results.get("claims", []):
            matched_claims_by_date[entry["service_date"]] = (
                entry["claim_id"] if entry.get("matched") else None
            )

        # Ensure every extracted practice exists, creating new rows as needed.
        for extracted_practice in extraction.practices:
            pname = extracted_practice.name
            if practice_id_by_name.get(pname) is None:
                created = await asyncio.to_thread(create_practice, db_path, pname)
                if created:
                    practice_id_by_name[pname] = created["id"]
                    for alias in extracted_practice.aliases:
                        await asyncio.to_thread(
                            add_practice_alias, db_path, created["id"], alias
                        )

        # Ensure every extracted provider exists.
        for extracted_provider in extraction.providers:
            created = await asyncio.to_thread(
                create_provider, db_path, extracted_provider.name
            )
            if created:
                for alias in extracted_provider.aliases:
                    await asyncio.to_thread(
                        add_provider_alias, db_path, created["id"], alias
                    )

        # Process each claim.
        committed_claim_ids: list[int] = []
        for claim in extraction.claims:
            pname = claim.practice_name
            pid = practice_id_by_name.get(pname)
            if pid is None:
                # Create the practice on the fly (claim-only mention).
                created = await asyncio.to_thread(create_practice, db_path, pname)
                if not created:
                    logger.error(
                        f"commit_ingestion: could not create practice '{pname}' "
                        f"for chat {chat_id}; skipping claim {claim.service_date}"
                    )
                    continue
                pid = created["id"]
                practice_id_by_name[pname] = pid

            existing_claim_id = matched_claims_by_date.get(claim.service_date)
            if existing_claim_id is not None:
                claim_id = existing_claim_id
            else:
                # Look up or create a minimal encounter stub for new claims only.
                encounter = await asyncio.to_thread(
                    find_encounter_by_date_and_practice, db_path, claim.service_date, pid
                )
                if encounter is None:
                    encounter = await asyncio.to_thread(
                        create_encounter, db_path, claim.service_date, pid, None, None
                    )
                    if encounter:
                        logger.info(
                            f"commit_ingestion: created encounter stub id={encounter['id']} "
                            f"service_date={claim.service_date} practice_id={pid} chat_id={chat_id}"
                        )
                    else:
                        logger.error(
                            f"commit_ingestion: failed to create encounter stub "
                            f"service_date={claim.service_date} practice_id={pid} chat_id={chat_id}",
                            exc_info=False,
                        )
                else:
                    logger.debug(
                        f"commit_ingestion: reusing encounter id={encounter['id']} "
                        f"service_date={claim.service_date} practice_id={pid}"
                    )
                encounter_id = encounter["id"] if encounter else None

                # Phase 7: auto-link rendering provider to the encounter stub.
                # Best-effort — never abort the commit on provider failure.
                provider_name = (claim.provider_name or "").strip()
                if provider_name and encounter is not None:
                    provider = await asyncio.to_thread(
                        match_provider, db_path, provider_name
                    )
                    if provider is None:
                        provider = await asyncio.to_thread(
                            create_provider, db_path, provider_name
                        )
                    if provider is None:
                        logger.warning(
                            f"commit_ingestion: could not match or create provider "
                            f"name='{provider_name}' chat_id={chat_id}; "
                            f"leaving encounter id={encounter_id} provider_id NULL"
                        )
                    else:
                        result = await asyncio.to_thread(
                            set_encounter_provider,
                            db_path,
                            encounter_id,
                            provider["id"],
                        )
                        if result is True:
                            logger.info(
                                f"commit_ingestion: linked provider id={provider['id']} "
                                f"name='{provider_name}' to encounter id={encounter_id} "
                                f"chat_id={chat_id}"
                            )
                        elif result is False:
                            logger.debug(
                                f"commit_ingestion: encounter id={encounter_id} "
                                f"already has a provider_id; skipping link to "
                                f"provider id={provider['id']}"
                            )
                        elif result is None:
                            logger.error(
                                f"commit_ingestion: failed to set encounter provider "
                                f"encounter_id={encounter_id} provider_id={provider['id']} "
                                f"chat_id={chat_id}"
                            )

                created_claim = await asyncio.to_thread(
                    create_claim,
                    db_path,
                    claim.service_date,
                    pid,
                    claim.billed_amount,
                    encounter_id,
                )
                if created_claim is None:
                    logger.error(
                        f"commit_ingestion: create_claim returned None for "
                        f"chat {chat_id} service_date={claim.service_date}"
                    )
                    continue
                claim_id = created_claim["id"]

            committed_claim_ids.append(claim_id)

            # Attach the source document to the claim.
            await asyncio.to_thread(
                attach_document, db_path, document_id, "claim", claim_id
            )

        # Apply adjudications to the most-recent claim by default. EOBs typically
        # carry adjudications; if the extraction provides them, attach to the
        # only/first committed claim. Multi-claim EOBs are handled by the LLM
        # producing one adjudication per claim; we pair them in order.
        adjudications = extraction.adjudications or []
        for idx, adj in enumerate(adjudications):
            if idx >= len(committed_claim_ids):
                logger.warning(
                    f"commit_ingestion: no committed claim to attach "
                    f"adjudication #{idx} for chat {chat_id}"
                )
                break
            await asyncio.to_thread(
                adjudicate_claim,
                db_path,
                committed_claim_ids[idx],
                adj.adjudication_date,
                adj.allowed_amount,
                adj.plan_paid,
                adj.member_owed,
                adj.paid_to_member,
                None,
            )

        logger.info(
            f"commit_ingestion: chat {chat_id} committed "
            f"{len(committed_claim_ids)} claim(s), {len(adjudications)} adjudication(s)"
        )

    except Exception:
        logger.error(
            f"commit_ingestion: unexpected failure for chat {chat_id}",
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Photo grouping
# ---------------------------------------------------------------------------

async def handle_photo_group(
    chat_id: int,
    media_group_id: str,
    file_bytes: bytes,
    db_path: str,
    documents_dir: str,
    context,
) -> None:
    """
    Append a photo to the in-memory album buffer and (re)schedule a flush job.

    Subsequent photos in the same media_group_id reset the timer so we wait for
    the full album before processing.
    """
    try:
        key = (chat_id, media_group_id)
        _photo_buffer.setdefault(key, []).append(file_bytes)

        # Cancel any prior flush job for this key — we want to extend the
        # window every time another photo arrives.
        prev_job = _photo_group_jobs.get(key)
        if prev_job is not None:
            try:
                prev_job.schedule_removal()
            except Exception:
                logger.warning(
                    f"handle_photo_group: failed to remove prior flush job for {key}",
                    exc_info=True,
                )

        new_job = None
        if context is not None and getattr(context, "job_queue", None) is not None:
            new_job = context.job_queue.run_once(
                _flush_photo_group,
                _PHOTO_GROUP_FLUSH_SECONDS,
                data=(chat_id, media_group_id, db_path, documents_dir),
            )
        if new_job is not None:
            _photo_group_jobs[key] = new_job

        logger.debug(
            f"handle_photo_group: chat {chat_id} media_group_id={media_group_id} "
            f"buffer_size={len(_photo_buffer[key])}"
        )
    except Exception:
        logger.error(
            f"handle_photo_group: unexpected failure for chat {chat_id} "
            f"media_group_id={media_group_id}",
            exc_info=True,
        )


async def _flush_photo_group(context) -> None:
    """
    Job callback: process the buffered photos for an album.

    v1: ingest only the first photo as a single document. Multi-page album
    handling (combining N photos into one logical document) is deferred.
    """
    try:
        data = context.job.data
        chat_id, media_group_id, db_path, documents_dir = data
        key = (chat_id, media_group_id)
        photos = _photo_buffer.pop(key, [])
        _photo_group_jobs.pop(key, None)

        if not photos:
            logger.debug(f"_flush_photo_group: no buffered photos for {key}")
            return

        # v1: pick the first photo as the document. Future: ship all to the
        # LLM as a multi-part vision call.
        first = photos[0]
        result = await ingest_document(
            db_path,
            documents_dir,
            chat_id,
            first,
            "photo.jpg",
            "image/jpeg",
            context,
        )
        try:
            await context.bot.send_message(chat_id=chat_id, text=result)
        except Exception:
            logger.error(
                f"_flush_photo_group: failed to send result to chat {chat_id}",
                exc_info=True,
            )
    except Exception:
        logger.error("_flush_photo_group: unexpected failure", exc_info=True)
