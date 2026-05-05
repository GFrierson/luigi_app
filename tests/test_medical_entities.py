"""Tests for src/medical/entities.py and the medical schema in init_db()."""

import sqlite3

import pytest

from src.database import init_db
from src.medical.entities import (
    add_practice_alias,
    add_procedure,
    add_provider_alias,
    affiliate_provider,
    create_encounter,
    create_practice,
    create_provider,
    get_procedures_for_encounter,
    get_provider_practices,
    resolve_entity_to_practice,
    resolve_practice,
    resolve_provider,
)


@pytest.fixture
def db_path(tmp_path):
    """Create an isolated SQLite DB initialized with init_db()."""
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_init_db_creates_medical_tables(db_path):
    """All 10 medical tables exist after init_db()."""
    expected = {
        "insurers",
        "cpt_codes",
        "icd_codes",
        "practices",
        "practice_aliases",
        "providers",
        "provider_aliases",
        "provider_practice_affiliations",
        "encounters",
        "procedures",
    }
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    actual = {row[0] for row in cursor.fetchall()}
    conn.close()
    missing = expected - actual
    assert not missing, f"Missing medical tables: {missing}"


# ---------------------------------------------------------------------------
# Practices
# ---------------------------------------------------------------------------

def test_create_practice_returns_dict(db_path):
    result = create_practice(db_path, "Manhattan Pain Medicine")
    assert result is not None
    assert "id" in result
    assert result["name"] == "Manhattan Pain Medicine"


def test_add_practice_alias_returns_none_on_duplicate(db_path):
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    first = add_practice_alias(db_path, practice["id"], "MPM")
    assert first is not None
    assert first["alias"] == "MPM"

    second = add_practice_alias(db_path, practice["id"], "MPM")
    assert second is None


def test_resolve_practice_by_name(db_path):
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    resolved = resolve_practice(db_path, "manhattan pain medicine")
    assert resolved is not None
    assert resolved["id"] == practice["id"]
    assert resolved["name"] == "Manhattan Pain Medicine"


def test_resolve_practice_by_alias(db_path):
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    add_practice_alias(db_path, practice["id"], "MPM")
    resolved = resolve_practice(db_path, "MPM")
    assert resolved is not None
    assert resolved["id"] == practice["id"]
    assert resolved["name"] == "Manhattan Pain Medicine"


def test_resolve_practice_returns_none_for_unknown(db_path):
    create_practice(db_path, "Manhattan Pain Medicine")
    assert resolve_practice(db_path, "Nonexistent Clinic") is None


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def test_create_provider_returns_dict(db_path):
    result = create_provider(db_path, "Dr. Deborah Barbiere")
    assert result is not None
    assert "id" in result
    assert result["name"] == "Dr. Deborah Barbiere"


def test_resolve_provider_by_alias(db_path):
    provider = create_provider(db_path, "Dr. Deborah Barbiere")
    add_provider_alias(db_path, provider["id"], "Dr. Deborah Barbiere Psy.D., L.Ac.")
    resolved = resolve_provider(db_path, "Dr. Deborah Barbiere Psy.D., L.Ac.")
    assert resolved is not None
    assert resolved["id"] == provider["id"]


# ---------------------------------------------------------------------------
# Affiliations
# ---------------------------------------------------------------------------

def test_affiliate_provider_returns_none_on_duplicate(db_path):
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    provider = create_provider(db_path, "Dr. Deborah Barbiere")
    first = affiliate_provider(db_path, provider["id"], practice["id"])
    assert first is not None

    second = affiliate_provider(db_path, provider["id"], practice["id"])
    assert second is None


def test_get_provider_practices_returns_affiliated_practice(db_path):
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    provider = create_provider(db_path, "Dr. Deborah Barbiere")
    affiliate_provider(db_path, provider["id"], practice["id"])

    practices = get_provider_practices(db_path, provider["id"])
    assert len(practices) == 1
    assert practices[0]["id"] == practice["id"]
    assert practices[0]["name"] == "Manhattan Pain Medicine"


def test_resolve_entity_to_practice_via_provider_name(db_path):
    """The headline use case: resolve a provider alias to its practice."""
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    provider = create_provider(db_path, "Dr. Deborah Barbiere")
    add_provider_alias(db_path, provider["id"], "Dr. Deborah Barbiere Psy.D., L.Ac.")
    affiliate_provider(db_path, provider["id"], practice["id"])

    resolved = resolve_entity_to_practice(db_path, "Dr. Deborah Barbiere Psy.D., L.Ac.")
    assert resolved is not None
    assert resolved["id"] == practice["id"]
    assert resolved["name"] == "Manhattan Pain Medicine"


# ---------------------------------------------------------------------------
# Encounters & procedures
# ---------------------------------------------------------------------------

def test_create_encounter_links_practice_and_provider(db_path):
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    provider = create_provider(db_path, "Dr. Deborah Barbiere")
    affiliate_provider(db_path, provider["id"], practice["id"])

    encounter = create_encounter(
        db_path,
        service_date="2025-09-23",
        practice_id=practice["id"],
        provider_id=provider["id"],
        notes="Initial consult",
    )
    assert encounter is not None
    assert encounter["service_date"] == "2025-09-23"
    assert encounter["practice_id"] == practice["id"]
    assert encounter["provider_id"] == provider["id"]
    assert encounter["notes"] == "Initial consult"


def test_add_procedure_with_cpt_and_icd(db_path):
    """Pre-seed cpt_codes and icd_codes directly, then add a procedure that references them."""
    # Seed lookup tables directly via sqlite3 — these are normally populated by seed scripts.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("INSERT INTO cpt_codes (code, description) VALUES (?, ?)", ("99213", "Office visit"))
    conn.execute("INSERT INTO icd_codes (code, description) VALUES (?, ?)", ("M54.5", "Low back pain"))
    conn.commit()
    conn.close()

    practice = create_practice(db_path, "Manhattan Pain Medicine")
    encounter = create_encounter(
        db_path,
        service_date="2025-09-23",
        practice_id=practice["id"],
        provider_id=None,
        notes=None,
    )

    procedure = add_procedure(
        db_path,
        encounter_id=encounter["id"],
        cpt_code="99213",
        icd_code="M54.5",
        billed_amount=250.00,
        notes="Follow-up",
    )
    assert procedure is not None
    assert procedure["encounter_id"] == encounter["id"]
    assert procedure["cpt_code"] == "99213"
    assert procedure["icd_code"] == "M54.5"
    assert procedure["billed_amount"] == 250.00
    assert procedure["notes"] == "Follow-up"


def test_get_procedures_for_encounter_returns_all(db_path):
    practice = create_practice(db_path, "Manhattan Pain Medicine")
    encounter = create_encounter(
        db_path,
        service_date="2025-09-23",
        practice_id=practice["id"],
        provider_id=None,
        notes=None,
    )

    add_procedure(db_path, encounter["id"], None, None, 100.00, "First")
    add_procedure(db_path, encounter["id"], None, None, 200.00, "Second")

    procedures = get_procedures_for_encounter(db_path, encounter["id"])
    assert len(procedures) == 2
    notes = {p["notes"] for p in procedures}
    assert notes == {"First", "Second"}
