"""
Persistence layer for parsed EOB documents (Phase 4).

``persist_eob`` writes one ``eob_documents`` row plus one ``eob_claims`` row per
claim. The ``eob_claims.claim_id`` link is left NULL here; the bridge layer
(``src.medical.eob.bridge``) backfills it after mirroring each claim into the
``claims``/``adjudications`` lifecycle.

``get_latest_eob_claim`` / ``get_eob_claim_history`` are read helpers for claim
history by payer claim number.

All public functions:
- are synchronous (callers should wrap in asyncio.to_thread() inside async code)
- never raise — they catch exceptions, log with exc_info=True, return None / []
"""

import dataclasses
import json
import logging
from typing import Optional

from src.database import get_connection
from src.medical.eob.types import EOBDocument, PdfKind

logger = logging.getLogger(__name__)


def persist_eob(
    eob: EOBDocument,
    source: PdfKind,
    extractor: str,
    source_document_id: Optional[int],
    db_path: str,
) -> Optional[int]:
    """Insert eob_documents row + one eob_claims row per claim. Returns eob_document_id."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO eob_documents (issuer, subtype, subscriber, source, extractor, source_document_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (eob.issuer, eob.subtype, eob.subscriber, source.value, extractor, source_document_id),
        )
        eob_document_id = cursor.lastrowid
        for claim in eob.claims:
            cursor.execute(
                """INSERT INTO eob_claims
                   (document_id, claim_id, claim_number, patient, provider,
                    in_network, received_date, patient_owes, line_items_json)
                   VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    eob_document_id,
                    claim.claim_number,
                    claim.patient,
                    claim.provider,
                    claim.in_network,
                    claim.received_date,
                    claim.patient_owes,
                    json.dumps([dataclasses.asdict(li) for li in claim.line_items]),
                ),
            )
        conn.commit()
        logger.info(
            f"Persisted EOB document id={eob_document_id} issuer='{eob.issuer}' "
            f"subtype='{eob.subtype}' claims={len(eob.claims)}"
        )
        return eob_document_id
    except Exception:
        logger.error("persist_eob failed", exc_info=True)
        return None
    finally:
        conn.close()


def get_latest_eob_claim(db_path: str, claim_number: str) -> Optional[dict]:
    """Return the newest eob_claims row for claim_number, joined with eob_documents fields."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT ec.*, ed.subtype, ed.subscriber, ed.issuer, ed.processed_at
               FROM eob_claims ec
               JOIN eob_documents ed ON ed.id = ec.document_id
               WHERE ec.claim_number = ?
               ORDER BY ec.id DESC
               LIMIT 1""",
            (claim_number,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception:
        logger.error("get_latest_eob_claim failed", exc_info=True)
        return None
    finally:
        conn.close()


def get_eob_claim_history(db_path: str, claim_number: str) -> list[dict]:
    """Return all eob_claims rows for claim_number, oldest first, joined with eob_documents."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT ec.*, ed.subtype, ed.subscriber, ed.issuer, ed.processed_at
               FROM eob_claims ec
               JOIN eob_documents ed ON ed.id = ec.document_id
               WHERE ec.claim_number = ?
               ORDER BY ec.id ASC""",
            (claim_number,),
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception:
        logger.error("get_eob_claim_history failed", exc_info=True)
        return []
    finally:
        conn.close()
