"""
One-time seed script: load the Sep 23, 2025 Siefferman + Mikaberidze fixture data.

Usage:
    python -m src.medical.scripts.seed_sep23_fixture --db-path data/123456789.db

This script populates two reference claims plus their adjudications, then prints
the contents of the v_claim_obligation, v_member_holds, and v_encounter_balance
views so you can verify the math against the source EOB/statement PDFs.

IMPORTANT — fixture data:
    Dollar values and dates are LITERALS extracted by hand from the source EOB
    and statement PDFs. This script does NOT parse PDF files. PDF ingestion is
    Phase 4 of the medical-bill-tracking roadmap. The literals here are
    placeholders chosen to produce the headline figures referenced in the
    roadmap ($4,501.50 outstanding, $5,275.50 member-held); refine once the
    actual PDFs are parsed in Phase 4.

Source documents (referenced in docs/roadmaps/roadmap_medical_bill_tracking.md):
    - EOB_20251106_-_923_2.pdf  (Siefferman, adjudicated 2025-11-06)
    - EOB_20251028_-_923_1.pdf  (Mikaberidze, initial adjudication 2025-10-28)
    - 923_statement_office.pdf  (Sep 23 office statement)

Idempotency:
    Uses find_by_match_key() to detect already-seeded claims and skips re-insert.
    Adjudications are NOT idempotent — re-running this script will append additional
    revisions. Run against a fresh DB or expect superseded chains to grow.

Stdlib + project modules only.
"""

import argparse
import logging
import sqlite3
import sys
from typing import Optional

from src.database import init_db
from src.medical.claims import (
    adjudicate_claim,
    create_claim,
    find_by_match_key,
)
from src.medical.entities import (
    create_encounter,
    create_practice,
    find_encounter_by_date_and_practice,
    resolve_practice,
)
from src.medical.payments import apply_payment, record_payment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fixture data — hand-transcribed from EOB/statement PDFs.
# Adjust here as the real PDFs are reconciled in Phase 4.
# ---------------------------------------------------------------------------

PRACTICE_NAME = "Manhattan Pain Medicine"

# Siefferman claim — originally $4,501.50 outstanding to the practice.
SIEFFERMAN = {
    "service_date": "2025-09-23",
    "billed_amount": 5_625.00,
    "adjudication": {
        "date": "2025-11-06",
        "allowed_amount": 4_501.50,
        "plan_paid": 0.00,
        "member_owed": 4_501.50,
        "paid_to_member": 0.00,
    },
}

# Mikaberidze claim — re-adjudicated; insurer paid the member $5,275.50
# (member-hold scenario). The first adjudication has a smaller member balance;
# the readjudication revises the math and the insurer cuts a check to the member.
MIKABERIDZE = {
    "service_date": "2025-09-23",
    "billed_amount": 6_500.00,
    "adjudication_1": {
        "date": "2025-10-28",
        "allowed_amount": 5_500.00,
        "plan_paid": 5_275.50,
        "member_owed": 224.50,
        "paid_to_member": 0.00,
    },
    "adjudication_2": {
        "date": "2026-01-15",
        "allowed_amount": 5_500.00,
        "plan_paid": 0.00,
        "member_owed": 5_500.00,
        # insurer cuts a check to the member as a refund of the original plan_paid:
        "paid_to_member": 5_275.50,
    },
    # Member then receives the insurer→member transfer:
    "member_hold_payment": {
        "date": "2026-01-20",
        "amount": 5_275.50,
        "from_party": "insurer",
        "to_party": "member",
        "method": "check",
        "reference": "MEM-REFUND-001",
    },
}


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------

def _ensure_practice(db_path: str, name: str) -> int:
    """Return practice id, creating the row if not already present."""
    existing = resolve_practice(db_path, name)
    if existing:
        logger.info(f"Practice '{name}' already exists id={existing['id']}, reusing")
        return existing["id"]
    created = create_practice(db_path, name)
    if created is None:
        raise RuntimeError(f"Failed to create practice '{name}'")
    logger.info(f"Created practice '{name}' id={created['id']}")
    return created["id"]


def _ensure_claim(
    db_path: str,
    service_date: str,
    practice_id: int,
    billed_amount: float,
    encounter_id: Optional[int],
) -> int:
    """Return claim id, creating it if no match-key collision exists."""
    existing = find_by_match_key(db_path, service_date, practice_id, billed_amount)
    if existing:
        logger.info(
            f"Claim already exists id={existing['id']} "
            f"service_date={service_date} amount={billed_amount}, reusing"
        )
        return existing["id"]
    created = create_claim(
        db_path,
        service_date=service_date,
        billing_practice_id=practice_id,
        billed_amount=billed_amount,
        encounter_id=encounter_id,
    )
    if created is None:
        raise RuntimeError(
            f"Failed to create claim service_date={service_date} amount={billed_amount}"
        )
    logger.info(f"Created claim id={created['id']} service_date={service_date}")
    return created["id"]


def _seed_siefferman(db_path: str, practice_id: int) -> int:
    """Seed the Siefferman claim and its single adjudication. Returns claim id."""
    encounter = find_encounter_by_date_and_practice(
        db_path, SIEFFERMAN["service_date"], practice_id
    )
    if encounter is None:
        encounter = create_encounter(
            db_path,
            service_date=SIEFFERMAN["service_date"],
            practice_id=practice_id,
            provider_id=None,
            notes="Siefferman 2025-09-23 office visit (fixture)",
        )
    encounter_id = encounter["id"] if encounter else None

    claim_id = _ensure_claim(
        db_path,
        service_date=SIEFFERMAN["service_date"],
        practice_id=practice_id,
        billed_amount=SIEFFERMAN["billed_amount"],
        encounter_id=encounter_id,
    )

    adj = SIEFFERMAN["adjudication"]
    adjudicate_claim(
        db_path,
        claim_id=claim_id,
        adjudication_date=adj["date"],
        allowed_amount=adj["allowed_amount"],
        plan_paid=adj["plan_paid"],
        member_owed=adj["member_owed"],
        paid_to_member=adj["paid_to_member"],
        notes="Siefferman EOB 2025-11-06 (fixture)",
    )
    return claim_id


def _seed_mikaberidze(db_path: str, practice_id: int) -> int:
    """Seed the Mikaberidze claim with two adjudications + a member-hold payment."""
    encounter = find_encounter_by_date_and_practice(
        db_path, MIKABERIDZE["service_date"], practice_id
    )
    if encounter is None:
        encounter = create_encounter(
            db_path,
            service_date=MIKABERIDZE["service_date"],
            practice_id=practice_id,
            provider_id=None,
            notes="Mikaberidze 2025-09-23 office visit (fixture)",
        )
    encounter_id = encounter["id"] if encounter else None

    claim_id = _ensure_claim(
        db_path,
        service_date=MIKABERIDZE["service_date"],
        practice_id=practice_id,
        billed_amount=MIKABERIDZE["billed_amount"],
        encounter_id=encounter_id,
    )

    adj1 = MIKABERIDZE["adjudication_1"]
    adjudicate_claim(
        db_path,
        claim_id=claim_id,
        adjudication_date=adj1["date"],
        allowed_amount=adj1["allowed_amount"],
        plan_paid=adj1["plan_paid"],
        member_owed=adj1["member_owed"],
        paid_to_member=adj1["paid_to_member"],
        notes="Mikaberidze EOB 2025-10-28 initial adjudication (fixture)",
    )

    adj2 = MIKABERIDZE["adjudication_2"]
    adjudicate_claim(
        db_path,
        claim_id=claim_id,
        adjudication_date=adj2["date"],
        allowed_amount=adj2["allowed_amount"],
        plan_paid=adj2["plan_paid"],
        member_owed=adj2["member_owed"],
        paid_to_member=adj2["paid_to_member"],
        notes="Mikaberidze re-adjudication (fixture)",
    )

    # Insurer→member refund payment, applied to this claim — this is what
    # surfaces in v_member_holds until the member forwards it to the practice.
    hold = MIKABERIDZE["member_hold_payment"]
    payment = record_payment(
        db_path,
        payment_date=hold["date"],
        amount=hold["amount"],
        from_party=hold["from_party"],
        to_party=hold["to_party"],
        method=hold["method"],
        reference=hold["reference"],
        notes="Insurer refund check to member (fixture)",
    )
    if payment is not None:
        apply_payment(db_path, payment["id"], claim_id, hold["amount"])

    return claim_id


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_view(db_path: str, view_name: str) -> None:
    """Read a view and print every row to stdout."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {view_name}")
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    print(f"\n=== {view_name} ({len(rows)} row(s)) ===")
    for row in rows:
        print(row)


def seed(db_path: str) -> None:
    """Run the full Sep 23 fixture seed pipeline."""
    init_db(db_path)
    practice_id = _ensure_practice(db_path, PRACTICE_NAME)
    siefferman_claim_id = _seed_siefferman(db_path, practice_id)
    mikaberidze_claim_id = _seed_mikaberidze(db_path, practice_id)
    logger.info(
        f"Seeded Sep 23 fixture: siefferman_claim_id={siefferman_claim_id} "
        f"mikaberidze_claim_id={mikaberidze_claim_id}"
    )

    _print_view(db_path, "v_claim_obligation")
    _print_view(db_path, "v_member_holds")
    _print_view(db_path, "v_encounter_balance")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the Sep 23 Siefferman + Mikaberidze fixture into a user DB.",
    )
    parser.add_argument(
        "--db-path",
        required=True,
        help="Path to user SQLite DB (e.g. data/123.db)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Log level (DEBUG, INFO, WARNING, ERROR). Default INFO.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        seed(args.db_path)
        return 0
    except Exception:
        logger.error("Sep 23 fixture seed script failed", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
