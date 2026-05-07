"""
CRUD and alias-resolution helpers for medical billing entities.

Tables managed here:
- practices, practice_aliases
- providers, provider_aliases
- provider_practice_affiliations
- encounters, procedures

Schema is owned by src.database.init_db() — this module only reads/writes rows.

All public functions:
- are synchronous (callers should wrap in asyncio.to_thread() inside async code)
- return dict | None or list[dict]
- never raise — they catch exceptions, log with exc_info=True, and return None / []
"""

import logging
import sqlite3
from typing import Optional

from src.database import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Practices
# ---------------------------------------------------------------------------

def create_practice(db_path: str, name: str) -> Optional[dict]:
    """Insert a new practice. Returns the created row dict, or None on failure."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO practices (name) VALUES (?)",
            (name,),
        )
        practice_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            "SELECT id, name FROM practices WHERE id = ?",
            (practice_id,),
        )
        row = cursor.fetchone()
        logger.info(f"Created practice id={practice_id} name='{name}'")
        return dict(row) if row else None
    except Exception:
        logger.error(f"Failed to create practice name='{name}'", exc_info=True)
        return None
    finally:
        conn.close()


def add_practice_alias(db_path: str, practice_id: int, alias: str) -> Optional[dict]:
    """Insert a practice alias. Returns the row dict, or None on duplicate/failure."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO practice_aliases (practice_id, alias) VALUES (?, ?)",
            (practice_id, alias),
        )
        alias_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            "SELECT id, practice_id, alias FROM practice_aliases WHERE id = ?",
            (alias_id,),
        )
        row = cursor.fetchone()
        logger.info(f"Added practice alias id={alias_id} practice_id={practice_id} alias='{alias}'")
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        logger.debug(f"Duplicate practice alias '{alias}' for practice_id={practice_id}, skipping")
        return None
    except Exception:
        logger.error(
            f"Failed to add practice alias practice_id={practice_id} alias='{alias}'",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def resolve_practice(db_path: str, query: str) -> Optional[dict]:
    """
    Resolve a free-text query to a practice row.

    Strategy:
      1. Exact case-insensitive match on practices.name
      2. Exact case-insensitive match on practice_aliases.alias → join to practices
      3. Return None
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()

        # Step 1: name match
        cursor.execute(
            "SELECT id, name FROM practices WHERE LOWER(name) = LOWER(?)",
            (query,),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(f"Resolved practice by name: '{query}' -> id={row['id']}")
            return dict(row)

        # Step 2: alias match
        cursor.execute(
            """
            SELECT p.id AS id, p.name AS name
            FROM practice_aliases pa
            JOIN practices p ON p.id = pa.practice_id
            WHERE LOWER(pa.alias) = LOWER(?)
            """,
            (query,),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(f"Resolved practice by alias: '{query}' -> id={row['id']}")
            return dict(row)

        logger.debug(f"No practice resolved for query='{query}'")
        return None
    except Exception:
        logger.error(f"Failed to resolve practice for query='{query}'", exc_info=True)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def create_provider(db_path: str, name: str) -> Optional[dict]:
    """Insert a new provider. Returns the created row dict, or None on failure
    or when a provider with the same name already exists (UNIQUE name index)."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO providers (name) VALUES (?)",
            (name,),
        )
        provider_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            "SELECT id, name FROM providers WHERE id = ?",
            (provider_id,),
        )
        row = cursor.fetchone()
        logger.info(f"Created provider id={provider_id} name='{name}'")
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        logger.debug(f"Duplicate provider name='{name}', skipping")
        return None
    except Exception:
        logger.error(f"Failed to create provider name='{name}'", exc_info=True)
        return None
    finally:
        conn.close()


def add_provider_alias(db_path: str, provider_id: int, alias: str) -> Optional[dict]:
    """Insert a provider alias. Returns the row dict, or None on duplicate/failure."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO provider_aliases (provider_id, alias) VALUES (?, ?)",
            (provider_id, alias),
        )
        alias_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            "SELECT id, provider_id, alias FROM provider_aliases WHERE id = ?",
            (alias_id,),
        )
        row = cursor.fetchone()
        logger.info(f"Added provider alias id={alias_id} provider_id={provider_id} alias='{alias}'")
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        logger.debug(f"Duplicate provider alias '{alias}' for provider_id={provider_id}, skipping")
        return None
    except Exception:
        logger.error(
            f"Failed to add provider alias provider_id={provider_id} alias='{alias}'",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def resolve_provider(db_path: str, query: str) -> Optional[dict]:
    """
    Resolve a free-text query to a provider row.

    Strategy:
      1. Exact case-insensitive match on providers.name
      2. Exact case-insensitive match on provider_aliases.alias → join to providers
      3. Return None
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()

        # Step 1: name match
        cursor.execute(
            "SELECT id, name FROM providers WHERE LOWER(name) = LOWER(?)",
            (query,),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(f"Resolved provider by name: '{query}' -> id={row['id']}")
            return dict(row)

        # Step 2: alias match
        cursor.execute(
            """
            SELECT p.id AS id, p.name AS name
            FROM provider_aliases pa
            JOIN providers p ON p.id = pa.provider_id
            WHERE LOWER(pa.alias) = LOWER(?)
            """,
            (query,),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(f"Resolved provider by alias: '{query}' -> id={row['id']}")
            return dict(row)

        logger.debug(f"No provider resolved for query='{query}'")
        return None
    except Exception:
        logger.error(f"Failed to resolve provider for query='{query}'", exc_info=True)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Provider <-> Practice affiliation
# ---------------------------------------------------------------------------

def affiliate_provider(db_path: str, provider_id: int, practice_id: int) -> Optional[dict]:
    """
    Affiliate a provider with a practice.

    Returns the affiliation row dict, or None on duplicate UNIQUE(provider_id, practice_id)
    or other failure.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO provider_practice_affiliations (provider_id, practice_id)
            VALUES (?, ?)
            """,
            (provider_id, practice_id),
        )
        affiliation_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            """
            SELECT id, provider_id, practice_id
            FROM provider_practice_affiliations
            WHERE id = ?
            """,
            (affiliation_id,),
        )
        row = cursor.fetchone()
        logger.info(
            f"Affiliated provider_id={provider_id} with practice_id={practice_id} "
            f"(affiliation_id={affiliation_id})"
        )
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        logger.debug(
            f"Duplicate affiliation provider_id={provider_id} practice_id={practice_id}, skipping"
        )
        return None
    except Exception:
        logger.error(
            f"Failed to affiliate provider_id={provider_id} with practice_id={practice_id}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def get_provider_practices(db_path: str, provider_id: int) -> list[dict]:
    """Return all practices a provider is affiliated with."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.id AS id, p.name AS name
            FROM provider_practice_affiliations ppa
            JOIN practices p ON p.id = ppa.practice_id
            WHERE ppa.provider_id = ?
            ORDER BY ppa.id ASC
            """,
            (provider_id,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(f"Retrieved {len(rows)} practice(s) for provider_id={provider_id}")
        return rows
    except Exception:
        logger.error(
            f"Failed to get practices for provider_id={provider_id}",
            exc_info=True,
        )
        return []
    finally:
        conn.close()


def resolve_entity_to_practice(db_path: str, query: str) -> Optional[dict]:
    """
    Resolve a free-text query to a practice — accepting either a practice name/alias
    or a provider name/alias. If a provider matches, return their first affiliated practice.

    Strategy:
      1. resolve_practice(query) → return if found
      2. resolve_provider(query) → if found, return first practice from get_provider_practices
      3. Return None
    """
    practice = resolve_practice(db_path, query)
    if practice:
        return practice

    provider = resolve_provider(db_path, query)
    if provider:
        practices = get_provider_practices(db_path, provider["id"])
        if practices:
            logger.debug(
                f"Resolved query='{query}' via provider id={provider['id']} "
                f"-> practice id={practices[0]['id']}"
            )
            return practices[0]
        logger.debug(
            f"Resolved query='{query}' to provider id={provider['id']} but no affiliated practice"
        )
        return None

    logger.debug(f"resolve_entity_to_practice: no match for query='{query}'")
    return None


# ---------------------------------------------------------------------------
# Encounters & procedures
# ---------------------------------------------------------------------------

def create_encounter(
    db_path: str,
    service_date: str,
    practice_id: int,
    provider_id: Optional[int],
    notes: Optional[str],
) -> Optional[dict]:
    """Insert an encounter. Returns the created row dict, or None on failure."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO encounters (service_date, practice_id, provider_id, notes)
            VALUES (?, ?, ?, ?)
            """,
            (service_date, practice_id, provider_id, notes),
        )
        encounter_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            """
            SELECT id, service_date, practice_id, provider_id, notes
            FROM encounters
            WHERE id = ?
            """,
            (encounter_id,),
        )
        row = cursor.fetchone()
        logger.info(
            f"Created encounter id={encounter_id} service_date={service_date} "
            f"practice_id={practice_id} provider_id={provider_id}"
        )
        return dict(row) if row else None
    except Exception:
        logger.error(
            f"Failed to create encounter service_date={service_date} "
            f"practice_id={practice_id} provider_id={provider_id}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def find_encounter_by_date_and_practice(
    db_path: str,
    service_date: str,
    practice_id: int,
) -> Optional[dict]:
    """Look up an encounter by (service_date, practice_id). Returns row dict or None."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, service_date, practice_id, provider_id, notes
            FROM encounters
            WHERE service_date = ? AND practice_id = ?
            """,
            (service_date, practice_id),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(
                f"Found encounter id={row['id']} service_date={service_date} "
                f"practice_id={practice_id}"
            )
            return dict(row)
        logger.debug(
            f"No encounter found for service_date={service_date} practice_id={practice_id}"
        )
        return None
    except Exception:
        logger.error(
            f"Failed to find encounter service_date={service_date} practice_id={practice_id}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def set_encounter_provider(
    db_path: str,
    encounter_id: int,
    provider_id: int,
) -> Optional[bool]:
    """
    Set encounters.provider_id when it is currently NULL — never overwrite a
    previously-set provider.

    Returns:
        True  — the encounter was updated.
        False — the encounter already had a provider_id set (no-op).
        None  — the call failed (logged with exc_info).
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE encounters
            SET provider_id = ?
            WHERE id = ? AND provider_id IS NULL
            """,
            (provider_id, encounter_id),
        )
        rowcount = cursor.rowcount
        conn.commit()
        if rowcount == 1:
            logger.info(
                f"Set encounter.provider_id encounter_id={encounter_id} "
                f"provider_id={provider_id}"
            )
            return True
        logger.debug(
            f"set_encounter_provider no-op: encounter_id={encounter_id} "
            f"already has a provider_id (or does not exist)"
        )
        return False
    except Exception:
        logger.error(
            f"Failed to set encounter provider encounter_id={encounter_id} "
            f"provider_id={provider_id}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def add_procedure(
    db_path: str,
    encounter_id: int,
    cpt_code: Optional[str],
    icd_code: Optional[str],
    billed_amount: Optional[float],
    notes: Optional[str],
) -> Optional[dict]:
    """Insert a procedure attached to an encounter. Returns the row dict, or None on failure."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO procedures (encounter_id, cpt_code, icd_code, billed_amount, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (encounter_id, cpt_code, icd_code, billed_amount, notes),
        )
        procedure_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            """
            SELECT id, encounter_id, cpt_code, icd_code, billed_amount, notes
            FROM procedures
            WHERE id = ?
            """,
            (procedure_id,),
        )
        row = cursor.fetchone()
        logger.info(
            f"Added procedure id={procedure_id} encounter_id={encounter_id} "
            f"cpt={cpt_code} icd={icd_code} billed={billed_amount}"
        )
        return dict(row) if row else None
    except Exception:
        logger.error(
            f"Failed to add procedure encounter_id={encounter_id} "
            f"cpt={cpt_code} icd={icd_code}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def get_procedures_for_encounter(db_path: str, encounter_id: int) -> list[dict]:
    """Return all procedures linked to an encounter."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, encounter_id, cpt_code, icd_code, billed_amount, notes
            FROM procedures
            WHERE encounter_id = ?
            ORDER BY id ASC
            """,
            (encounter_id,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(f"Retrieved {len(rows)} procedure(s) for encounter_id={encounter_id}")
        return rows
    except Exception:
        logger.error(
            f"Failed to get procedures for encounter_id={encounter_id}",
            exc_info=True,
        )
        return []
    finally:
        conn.close()
