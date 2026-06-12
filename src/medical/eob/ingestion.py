"""
EOB Telegram ingestion glue (Phase 5).

This module owns the *non-I/O* message formatting for the EOB confirm flow and
the commit path that persists a confirmed EOB. The Telegram handler
(``src.telegram_handler``) owns prompting, pending-state storage, and the TTL
job; ``process_eob`` / ``to_document`` stay pure per the EOB contract.

Pending-state shapes owned by the handler (documented here for reference):

    kind == "eob"  (a validated Extracted result awaiting confirm/cancel):
        {
            "kind": "eob",
            "result": Extracted,
            "artifacts": list[str],
            "source": PdfKind,        # PdfKind of the parsed Document (for persist_eob)
            "file_bytes": bytes,
            "original_name": str,
            "mime_type": str,
            "unknown_consented": bool,  # True if it came through the LLM consent path
        }

    kind == "eob_consent"  (an UnknownType awaiting yes/no AI-vision consent):
        {
            "kind": "eob_consent",
            "doc": Document,          # re-used so the consent path does not re-OCR
            "artifacts": list[str],
            "file_bytes": bytes,      # kept so a "no" can fall through to ingest_document
            "original_name": str,
            "mime_type": str,
        }

Public functions:
    - _format_eob_confirm(...)   — pure subtype-aware confirm text
    - _format_consent_prompt(...) — pure unknown-issuer consent prompt
    - commit_eob_ingestion(...)  — async; persists + bridges a confirmed EOB
"""

import asyncio
import logging

from src.medical.documents import save_document
from src.medical.eob.bridge import bridge_eob_to_claims
from src.medical.eob.corpus import log_unknown
from src.medical.eob.persist import persist_eob
from src.medical.eob.types import (
    Document,
    EOBDocument,
    ValidationResult,
)

logger = logging.getLogger(__name__)


def _format_eob_confirm(
    eob: EOBDocument,
    artifacts: list[str],
    validation: ValidationResult,
) -> str:
    """
    Build the subtype-aware confirmation message for a parsed EOB.

    Pure: no I/O. Lists each claim and any detected PDF artifacts, then asks the
    user to reply confirm/cancel.
    """
    n = len(eob.claims)
    subscriber = eob.subscriber or "unknown subscriber"

    if eob.subtype == "summary":
        header = f"EOB received: {n} claim(s) processed for {subscriber}."
    elif eob.subtype == "payment_notice":
        header = f"EOB received: payment notice — {subscriber}, {n} claim(s)."
    elif eob.subtype == "denial":
        header = (
            f"EOB received: DENIAL — {n} claim(s) denied for {subscriber}. "
            f"You may owe money."
        )
    elif eob.subtype == "duplicate_notice":
        header = f"EOB received: duplicate notice — {n} claim(s), {subscriber}."
    else:
        header = f"EOB received: {n} claim(s) for {subscriber}."

    lines = [header]
    for claim in eob.claims:
        provider = claim.provider or "unknown provider"
        date = claim.received_date or "no date"
        bullet = f"  • {provider} ({date}) — you owe {claim.patient_owes}"
        if eob.subtype == "denial":
            bullet += " [DENIED]"
        lines.append(bullet)

    if artifacts:
        lines.append(f"Note: {', '.join(artifacts)} detected in this PDF.")

    lines.append("Reply confirm to save, or cancel to discard.")
    return "\n".join(lines)


def _format_consent_prompt(doc: Document) -> str:
    """
    Build the unknown-issuer AI-vision consent prompt.

    Pure: ``doc`` is accepted for symmetry / future per-document detail but is
    not currently interpolated.
    """
    return (
        "I don't recognize this insurer. Would you like me to use AI vision to "
        "extract the data?\n"
        "This sends the document pages to an AI model. Reply yes to continue, or "
        "no to process it as a regular document instead."
    )


async def commit_eob_ingestion(
    db_path: str,
    documents_dir: str,
    chat_id: int,
    pending: dict,
) -> None:
    """
    Persist a confirmed EOB: save the source PDF, persist eob_* rows, optionally
    flag an unknown issuer, then bridge each claim into the claims/adjudications
    lifecycle.

    All sync DB work is wrapped in asyncio.to_thread to avoid blocking the event
    loop. Never raises — failures are logged with exc_info=True.
    """
    try:
        saved = await asyncio.to_thread(
            save_document,
            documents_dir,
            chat_id,
            db_path,
            pending["file_bytes"],
            pending["original_name"],
            pending["mime_type"],
            "eob",
            None,
            None,
        )
        if saved is None:
            logger.error(
                f"commit_eob_ingestion: save_document returned None for chat {chat_id} "
                f"original_name='{pending.get('original_name')}'"
            )
            return

        source_document_id = saved["id"]
        eob_result = pending["result"]

        eob_document_id = await asyncio.to_thread(
            persist_eob,
            eob_result.eob,
            pending["source"],
            eob_result.extractor,
            source_document_id,
            db_path,
        )
        if eob_document_id is None:
            logger.error(
                f"commit_eob_ingestion: persist_eob returned None for chat {chat_id} "
                f"source_document_id={source_document_id}"
            )
            return

        if pending.get("unknown_consented"):
            await asyncio.to_thread(log_unknown, source_document_id, db_path)

        await asyncio.to_thread(
            bridge_eob_to_claims, eob_result.eob, db_path, eob_document_id
        )

        logger.info(
            f"commit_eob_ingestion: chat {chat_id} saved EOB document "
            f"id={eob_document_id} with {len(eob_result.eob.claims)} claim(s)"
        )
    except Exception:
        logger.error(
            f"commit_eob_ingestion: unexpected failure for chat {chat_id}",
            exc_info=True,
        )
