"""
Bridge parsed EOB claims into the canonical claims/adjudications lifecycle (Phase 4).

``bridge_eob_to_claims`` mirrors each ``Claim`` in an ``EOBDocument`` into a
``claims`` row plus an ``adjudications`` row, resolving (or creating placeholder)
provider/practice/encounter rows as needed. It is always-insert (no dedup): the
``eob_document_id`` passed to ``create_claim`` participates in the claims unique
index (via COALESCE(eob_document_id, 0)), so resending the same claim produces a
fresh row rather than colliding.

After a claim is created, the matching ``eob_claims.claim_id`` is backfilled so
the EOB record points at the canonical claim.

All functions never raise — failures for an individual claim are logged with
exc_info=True and that claim is skipped; the rest of the batch continues.
"""

import datetime
import logging

from src.database import get_connection
from src.medical.claims import add_external_id, adjudicate_claim, create_claim
from src.medical.entities import (
    affiliate_provider,
    create_encounter,
    create_practice,
    create_provider,
    find_encounter_by_date_and_practice,
    resolve_entity_to_practice,
)
from src.medical.eob.types import Claim, EOBDocument
from src.medical.eob.validate import _parse_amount
from src.medical.matching import match_provider

logger = logging.getLogger(__name__)


def _derive_service_date(claim: Claim) -> str:
    if claim.line_items:
        return claim.line_items[0].service_date
    if claim.received_date:
        return claim.received_date
    return datetime.date.today().isoformat()


def _backfill_eob_claim_id(db_path: str, eob_document_id: int, claim_number: str, claim_id: int) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE eob_claims SET claim_id = ? WHERE document_id = ? AND claim_number = ?",
            (claim_id, eob_document_id, claim_number),
        )
        conn.commit()
    except Exception:
        logger.error("_backfill_eob_claim_id failed", exc_info=True)
    finally:
        conn.close()


def bridge_eob_to_claims(eob: EOBDocument, db_path: str, eob_document_id: int) -> list[int]:
    """Mirror each EOB Claim into claims+adjudications. Always-insert, no dedup. Returns created claim_ids."""
    created_ids: list[int] = []
    for claim in eob.claims:
        try:
            # --- Provider ---
            provider_row = match_provider(db_path, claim.provider)
            if provider_row is None:
                provider_row = create_provider(db_path, claim.provider)
            if provider_row is None:
                logger.warning(
                    f"bridge: could not resolve provider '{claim.provider}', "
                    f"skipping claim {claim.claim_number}"
                )
                continue
            provider_id = provider_row["id"]

            # --- Practice (rendering-doctor placeholder) ---
            practice_row = resolve_entity_to_practice(db_path, claim.provider)
            if practice_row is None:
                practice_row = create_practice(db_path, claim.provider)
                if practice_row is not None:
                    logger.info(
                        f"bridge: created placeholder practice '{claim.provider}' "
                        f"for eob_document_id={eob_document_id}"
                    )
                    affiliate_provider(db_path, provider_id, practice_row["id"])
            if practice_row is None:
                logger.warning(
                    f"bridge: could not resolve practice for '{claim.provider}', "
                    f"skipping claim {claim.claim_number}"
                )
                continue
            practice_id = practice_row["id"]

            # --- Service date ---
            service_date = _derive_service_date(claim)

            # --- Amounts: billed=doctor_charges, allowed=allowed, plan_paid=anthem_paid ---
            billed = sum((_parse_amount(li.doctor_charges) or 0.0) for li in claim.line_items)
            allowed = sum((_parse_amount(li.allowed) or 0.0) for li in claim.line_items)
            plan_paid = sum((_parse_amount(li.anthem_paid) or 0.0) for li in claim.line_items)
            member_owed = _parse_amount(claim.patient_owes) or 0.0

            # --- Encounter ---
            encounter = find_encounter_by_date_and_practice(db_path, service_date, practice_id)
            if encounter is None:
                encounter = create_encounter(db_path, service_date, practice_id, provider_id, None)
            if encounter is None:
                logger.error(f"bridge: could not create encounter for claim {claim.claim_number}")
                continue
            encounter_id = encounter["id"]

            # --- Claim (pass eob_document_id to break the UNIQUE key for no-dedup) ---
            claim_row = create_claim(
                db_path,
                service_date=service_date,
                billing_practice_id=practice_id,
                billed_amount=billed,
                encounter_id=encounter_id,
                eob_document_id=eob_document_id,
            )
            if claim_row is None:
                logger.error(f"bridge: create_claim failed for claim {claim.claim_number}")
                continue
            claim_id = claim_row["id"]

            # --- External ID ---
            add_external_id(db_path, claim_id, "anthem_eob", claim.claim_number)

            # --- Adjudication ---
            adjudication_date = claim.received_date or datetime.date.today().isoformat()
            adjudicate_claim(
                db_path,
                claim_id,
                adjudication_date,
                allowed_amount=allowed,
                plan_paid=plan_paid,
                member_owed=member_owed,
            )

            # --- Backfill eob_claims.claim_id ---
            _backfill_eob_claim_id(db_path, eob_document_id, claim.claim_number, claim_id)

            created_ids.append(claim_id)
        except Exception:
            logger.error(f"bridge: unhandled error for claim {claim.claim_number}", exc_info=True)
            continue

    return created_ids
