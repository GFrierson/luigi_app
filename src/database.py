import sqlite3
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

MAX_SCHEDULES = 10

def get_connection(db_path: str) -> sqlite3.Connection:
    """Get a SQLite database connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Return rows as dict-like objects
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def get_user_db_path(database_dir: str, chat_id: int) -> str:
    """
    Get the database path for a specific user.

    Args:
        database_dir: Base directory for databases
        chat_id: Telegram chat ID

    Returns:
        Path to the user's database file (e.g., data/123456789.db)
    """
    return os.path.join(database_dir, f"{chat_id}.db")

def get_all_user_databases(database_dir: str) -> list[tuple[int, str]]:
    """
    Get all user databases in the database directory.

    Args:
        database_dir: Base directory for databases

    Returns:
        List of (chat_id, db_path) tuples
    """
    if not os.path.exists(database_dir):
        return []

    user_dbs = []
    for filename in os.listdir(database_dir):
        if filename.endswith('.db'):
            # Extract chat_id from filename (e.g., "123456789.db" -> 123456789)
            try:
                chat_id = int(filename[:-3])  # Remove .db extension
                db_path = os.path.join(database_dir, filename)
                user_dbs.append((chat_id, db_path))
            except ValueError:
                # Skip files that don't have numeric names
                logger.debug(f"Skipping non-user database file: {filename}")
                continue

    logger.debug(f"Found {len(user_dbs)} user databases")
    return user_dbs

def init_db(db_path: str) -> None:
    """Initialize database with required tables."""
    # Ensure parent directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)

    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Create messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
            body TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            message_id INTEGER
        )
    """)

    # Create index on timestamp
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_timestamp
        ON messages(timestamp)
    """)

    # Create schedules table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hour INTEGER NOT NULL CHECK (hour >= 0 AND hour <= 23),
            minute INTEGER NOT NULL CHECK (minute >= 0 AND minute <= 59),
            message_template TEXT NOT NULL,
            active BOOLEAN DEFAULT TRUE
        )
    """)

    # Create user_profile table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            name TEXT,
            telegram_name TEXT
        )
    """)

    # Migration: add telegram_name to existing DBs that lack it
    try:
        cursor.execute("ALTER TABLE user_profile ADD COLUMN telegram_name TEXT")
    except Exception:
        pass  # Column already exists

    # Migration: add unique index on schedules(hour, minute)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_schedules_hour_minute ON schedules(hour, minute)
    """)

    # Create medication_groups table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS medication_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            aliases TEXT,
            schedule_hour INTEGER CHECK (schedule_hour >= 0 AND schedule_hour <= 23),
            schedule_minute INTEGER CHECK (schedule_minute >= 0 AND schedule_minute <= 59),
            interval_days INTEGER DEFAULT 1,
            start_date DATE,
            reminder_active BOOLEAN DEFAULT TRUE
        )
    """)

    # Create medications table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            dosage TEXT,
            type TEXT NOT NULL CHECK (type IN ('scheduled', 'as_needed')),
            group_id INTEGER REFERENCES medication_groups(id),
            active BOOLEAN DEFAULT TRUE
        )
    """)

    # Create medication_events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS medication_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medication_id INTEGER NOT NULL REFERENCES medications(id),
            taken_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL CHECK (status IN ('taken', 'skipped')),
            notes TEXT,
            source TEXT NOT NULL CHECK (source IN ('user', 'reminder'))
        )
    """)

    # Create index on medication_events(taken_at)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_medication_events_taken_at ON medication_events(taken_at)
    """)

    # Insert default profile row if not exists
    cursor.execute("""
        INSERT OR IGNORE INTO user_profile (id, name) VALUES (1, NULL)
    """)

    # Medical billing entities (Phase 1)
    # NOTE: practices must be created before claims (Phase 2) — claims.billing_practice_id FKs here
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS insurers (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cpt_codes (
            code        TEXT PRIMARY KEY,
            description TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS icd_codes (
            code        TEXT PRIMARY KEY,
            description TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS practices (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS practice_aliases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            practice_id INTEGER NOT NULL REFERENCES practices(id),
            alias       TEXT    NOT NULL,
            UNIQUE(alias)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS provider_aliases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL REFERENCES providers(id),
            alias       TEXT    NOT NULL,
            UNIQUE(alias)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS provider_practice_affiliations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL REFERENCES providers(id),
            practice_id INTEGER NOT NULL REFERENCES practices(id),
            UNIQUE(provider_id, practice_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS encounters (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            service_date DATE    NOT NULL,
            practice_id  INTEGER NOT NULL REFERENCES practices(id),
            provider_id  INTEGER REFERENCES providers(id),
            notes        TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS procedures (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            encounter_id  INTEGER NOT NULL REFERENCES encounters(id),
            cpt_code      TEXT    REFERENCES cpt_codes(code),
            icd_code      TEXT    REFERENCES icd_codes(code),
            billed_amount REAL,
            notes         TEXT
        )
    """)

    # --- Medical billing (Phase 2) ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_date DATE NOT NULL,
            billing_practice_id INTEGER NOT NULL REFERENCES practices(id),
            encounter_id INTEGER REFERENCES encounters(id),
            insurer_id INTEGER REFERENCES insurers(id),
            billed_amount REAL NOT NULL,
            current_status TEXT NOT NULL DEFAULT 'submitted'
                CHECK (current_status IN ('submitted','adjudicated','readjudicated','denied','void')),
            UNIQUE(service_date, billing_practice_id, billed_amount)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS claim_external_ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            system TEXT NOT NULL,
            external_id TEXT NOT NULL,
            UNIQUE(system, external_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS charges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            procedure_id INTEGER REFERENCES procedures(id),
            cpt_code TEXT REFERENCES cpt_codes(code),
            billed_amount REAL,
            notes TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS adjudications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            adjudication_date DATE NOT NULL,
            allowed_amount REAL,
            plan_paid REAL,
            member_owed REAL,
            paid_to_member REAL,
            revision INTEGER NOT NULL DEFAULT 1,
            superseded_by INTEGER REFERENCES adjudications(id),
            notes TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS claim_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER NOT NULL REFERENCES claims(id),
            event_type TEXT NOT NULL CHECK (event_type IN ('created','adjudicated','readjudicated','status_changed','external_id_added')),
            payload TEXT,
            occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- Medical billing (Phase 3) ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id       INTEGER NOT NULL,
            file_path     TEXT    NOT NULL,
            original_name TEXT,
            mime_type     TEXT,
            doc_type      TEXT    NOT NULL CHECK (doc_type IN ('eob','statement','receipt','other')),
            document_date DATE,
            notes         TEXT,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # document_links: polymorphic — entity_type is constrained by CHECK,
    # entity_id is NOT a SQL FK (would have to vary per entity_type). Mirrors
    # the claim_events.event_type CHECK pattern.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_links (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id   INTEGER NOT NULL REFERENCES documents(id),
            entity_type   TEXT    NOT NULL CHECK (entity_type IN ('claim','encounter','procedure','adjudication')),
            entity_id     INTEGER NOT NULL,
            UNIQUE(document_id, entity_type, entity_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_date  DATE    NOT NULL,
            amount        REAL    NOT NULL,
            from_party    TEXT    NOT NULL CHECK (from_party IN ('insurer','member','hsa','fsa','practice')),
            to_party      TEXT    NOT NULL CHECK (to_party IN ('insurer','member','hsa','fsa','practice')),
            method        TEXT,
            reference     TEXT,
            notes         TEXT,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payment_applications (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id     INTEGER NOT NULL REFERENCES payments(id),
            claim_id       INTEGER NOT NULL REFERENCES claims(id),
            applied_amount REAL    NOT NULL,
            UNIQUE(payment_id, claim_id)
        )
    """)

    # --- Medical billing views (Phase 3) ---
    # v_claim_obligation: net amount the member still owes the practice for each claim.
    # - LEFT JOIN adjudications + filter to a.superseded_by IS NULL so unadjudicated
    #   claims still appear (member_owed = 0) and only the current adjudication is used.
    # - Sum only payments member→practice toward payments_applied (insurer→member
    #   transfers are tracked separately in v_member_holds).
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS v_claim_obligation AS
        SELECT
            c.id                                        AS claim_id,
            c.service_date                              AS service_date,
            c.billing_practice_id                       AS billing_practice_id,
            c.billed_amount                             AS billed_amount,
            COALESCE(a.member_owed, 0.0)                AS member_owed,
            COALESCE(
                SUM(CASE
                    WHEN p.from_party = 'member' AND p.to_party = 'practice'
                    THEN pa.applied_amount ELSE 0
                END), 0.0
            )                                           AS payments_applied,
            COALESCE(a.member_owed, 0.0)
                - COALESCE(
                    SUM(CASE
                        WHEN p.from_party = 'member' AND p.to_party = 'practice'
                        THEN pa.applied_amount ELSE 0
                    END), 0.0
                )                                       AS net_obligation
        FROM claims c
        LEFT JOIN adjudications a
            ON a.claim_id = c.id AND a.superseded_by IS NULL
        LEFT JOIN payment_applications pa ON pa.claim_id = c.id
        LEFT JOIN payments p ON p.id = pa.payment_id
        GROUP BY c.id, a.id
    """)

    # v_member_holds: insurer→member payments — money the insurer paid to the
    # member that should be forwarded on to a practice.
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS v_member_holds AS
        SELECT
            pa.claim_id                                    AS claim_id,
            p.id                                           AS payment_id,
            p.payment_date                                 AS payment_date,
            p.amount                                       AS held_amount,
            julianday('now') - julianday(p.payment_date)   AS days_held
        FROM payments p
        JOIN payment_applications pa ON pa.payment_id = p.id
        WHERE p.from_party = 'insurer'
          AND p.to_party   = 'member'
    """)

    # v_encounter_balance: rolls up net_obligation across all claims for an encounter.
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS v_encounter_balance AS
        SELECT
            e.id                   AS encounter_id,
            e.service_date         AS service_date,
            e.practice_id          AS practice_id,
            SUM(vc.net_obligation) AS total_net_obligation
        FROM encounters e
        JOIN claims c ON c.encounter_id = e.id
        JOIN v_claim_obligation vc ON vc.claim_id = c.id
        GROUP BY e.id
    """)

    conn.commit()
    conn.close()
    logger.info("Database tables created/verified")

def insert_message(db_path: str, direction: str, body: str, message_id: Optional[int] = None) -> int:
    """Insert a message into the database and return its ID."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO messages (direction, body, message_id)
        VALUES (?, ?, ?)
    """, (direction, body, message_id))

    row_id = cursor.lastrowid
    conn.commit()
    conn.close()

    logger.debug(f"Inserted {direction} message with ID {row_id}")
    return row_id

def get_recent_messages(db_path: str, limit: int = 5, hours: int = 24) -> list[dict]:
    """
    Get recent messages, returning the lesser of:
    - Messages from last `hours` hours, OR
    - Last `limit` messages
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Calculate cutoff time for hours-based query
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    cutoff_str = cutoff_time.strftime('%Y-%m-%d %H:%M:%S')

    # First, get messages from last X hours
    cursor.execute("""
        SELECT direction, body, timestamp
        FROM messages
        WHERE timestamp >= ?
        ORDER BY timestamp ASC
    """, (cutoff_str,))

    time_based_messages = [dict(row) for row in cursor.fetchall()]

    # Then, get last Y messages
    cursor.execute("""
        SELECT direction, body, timestamp
        FROM messages
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))

    limit_based_messages = [dict(row) for row in cursor.fetchall()]
    limit_based_messages.reverse()  # Oldest first for conversation flow

    conn.close()

    # Return the lesser of the two sets
    if len(time_based_messages) < len(limit_based_messages):
        result = time_based_messages
        logger.debug(f"Returning {len(result)} messages from last {hours} hours")
    else:
        result = limit_based_messages
        logger.debug(f"Returning {len(result)} most recent messages (limit: {limit})")

    return result

def get_active_schedules(db_path: str) -> list[dict]:
    """Get all active schedules from the database."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, hour, minute, message_template, active
        FROM schedules
        WHERE active = TRUE
        ORDER BY hour, minute
    """)

    schedules = [dict(row) for row in cursor.fetchall()]
    conn.close()

    logger.debug(f"Retrieved {len(schedules)} active schedules")
    return schedules

def deactivate_all_schedules(db_path: str) -> None:
    """Deactivate all schedules in the database."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE schedules
        SET active = FALSE
        WHERE active = TRUE
    """)

    conn.commit()
    conn.close()
    logger.info("Deactivated all schedules")

def get_all_schedules(db_path: str) -> list[dict]:
    """Get all schedules (active and inactive) from the database."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, hour, minute, message_template, active
        FROM schedules
        ORDER BY hour, minute
    """)

    schedules = [dict(row) for row in cursor.fetchall()]
    conn.close()

    logger.debug(f"Retrieved {len(schedules)} schedules (all)")
    return schedules


def add_schedule(db_path: str, hour: int, minute: int, message_template: str) -> dict | None:
    """
    Insert a new schedule. Returns created row dict or None on duplicate/max limit.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Enforce max schedules
    cursor.execute("SELECT COUNT(*) as count FROM schedules")
    if cursor.fetchone()['count'] >= MAX_SCHEDULES:
        conn.close()
        logger.warning(f"Max schedules ({MAX_SCHEDULES}) reached, cannot add {hour:02d}:{minute:02d}")
        return None

    try:
        cursor.execute("""
            INSERT INTO schedules (hour, minute, message_template, active)
            VALUES (?, ?, ?, TRUE)
        """, (hour, minute, message_template))
        row_id = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"Added schedule at {hour:02d}:{minute:02d}")
        return {"id": row_id, "hour": hour, "minute": minute, "message_template": message_template, "active": True}
    except sqlite3.IntegrityError:
        conn.close()
        logger.debug(f"Duplicate schedule at {hour:02d}:{minute:02d}, skipping")
        return None


def remove_schedule(db_path: str, hour: int, minute: int) -> bool:
    """Delete schedule by time. Returns True if a row was deleted."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM schedules WHERE hour = ? AND minute = ?", (hour, minute))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()

    if deleted:
        logger.info(f"Removed schedule at {hour:02d}:{minute:02d}")
    return deleted


def update_schedule_time(db_path: str, old_hour: int, old_minute: int, new_hour: int, new_minute: int) -> bool:
    """Update a schedule's time. Returns True on success, False on not-found or duplicate target."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE schedules SET hour = ?, minute = ?
            WHERE hour = ? AND minute = ?
        """, (new_hour, new_minute, old_hour, old_minute))
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        if updated:
            logger.info(f"Updated schedule from {old_hour:02d}:{old_minute:02d} to {new_hour:02d}:{new_minute:02d}")
        return updated
    except sqlite3.IntegrityError:
        conn.close()
        logger.debug(f"Cannot update schedule to {new_hour:02d}:{new_minute:02d}, duplicate exists")
        return False


def reactivate_all_schedules(db_path: str) -> int:
    """Set all schedules to active=TRUE. Returns count of updated rows."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("UPDATE schedules SET active = TRUE WHERE active = FALSE")
    count = cursor.rowcount
    conn.commit()
    conn.close()

    logger.info(f"Reactivated {count} schedules")
    return count


def set_telegram_name(db_path: str, name: str) -> None:
    """Set the Telegram-provided first name in the profile."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE user_profile SET telegram_name = ? WHERE id = 1
    """, (name,))

    conn.commit()
    conn.close()
    logger.debug(f"Set telegram_name to: {name}")


def set_preferred_name(db_path: str, name: str) -> None:
    """Set the user's preferred (explicitly chosen) name in the profile."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE user_profile SET name = ? WHERE id = 1
    """, (name,))

    conn.commit()
    conn.close()
    logger.info(f"Set preferred name to: {name}")


def get_display_name(db_path: str) -> Optional[str]:
    """Return preferred name if set, else telegram_name, else None."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT name, telegram_name FROM user_profile WHERE id = 1")
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None
    return row['name'] or row['telegram_name']


def create_medication_group(
    db_path: str,
    name: str,
    aliases: Optional[str],
    schedule_hour: Optional[int],
    schedule_minute: Optional[int],
    interval_days: int = 1,
    start_date: Optional[str] = None,
) -> int:
    """Insert a new medication group and return its id."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO medication_groups (name, aliases, schedule_hour, schedule_minute, interval_days, start_date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name, aliases, schedule_hour, schedule_minute, interval_days, start_date))
    group_id = cursor.lastrowid
    conn.commit()
    conn.close()
    logger.info(f"Created medication group '{name}' with id {group_id}")
    return group_id


def create_medication(
    db_path: str,
    name: str,
    dosage: Optional[str],
    med_type: str,
    group_id: Optional[int] = None,
) -> int:
    """Insert a new medication and return its id."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO medications (name, dosage, type, group_id)
        VALUES (?, ?, ?, ?)
    """, (name, dosage, med_type, group_id))
    med_id = cursor.lastrowid
    conn.commit()
    conn.close()
    logger.info(f"Created medication '{name}' (type={med_type}) with id {med_id}")
    return med_id


def get_medications_by_group(db_path: str, group_id: int) -> list:
    """Return all active medications in a group."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, dosage, type, group_id, active
        FROM medications
        WHERE group_id = ? AND active = TRUE
    """, (group_id,))
    meds = [dict(row) for row in cursor.fetchall()]
    conn.close()
    logger.debug(f"Retrieved {len(meds)} active medications for group {group_id}")
    return meds


def get_all_active_medication_groups(db_path: str) -> list:
    """Return all medication groups where reminder_active=TRUE."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, aliases, schedule_hour, schedule_minute, interval_days, start_date, reminder_active
        FROM medication_groups
        WHERE reminder_active = TRUE
    """)
    groups = [dict(row) for row in cursor.fetchall()]
    conn.close()
    logger.debug(f"Retrieved {len(groups)} active medication groups")
    return groups


def find_medication_group(db_path: str, query: str) -> Optional[dict]:
    """Match query against group name or aliases (case-insensitive). Returns first match or None."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, aliases, schedule_hour, schedule_minute, interval_days, start_date, reminder_active
        FROM medication_groups
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    query_lower = query.lower().strip()
    for row in rows:
        if row['name'].lower() == query_lower:
            return row
        if row['aliases']:
            for alias in row['aliases'].split(','):
                if alias.strip().lower() == query_lower:
                    return row
    logger.debug(f"No medication group found matching '{query}'")
    return None


def get_all_medications(db_path: str) -> list:
    """Return all active medications (both scheduled and as-needed)."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, dosage, type, group_id, active
        FROM medications
        WHERE active = TRUE
    """)
    meds = [dict(row) for row in cursor.fetchall()]
    conn.close()
    logger.debug(f"Retrieved {len(meds)} active medications")
    return meds


def get_medication_by_name(db_path: str, name: str) -> Optional[dict]:
    """Case-insensitive lookup by medication name. Returns first active match or None."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, dosage, type, group_id, active
        FROM medications
        WHERE LOWER(name) = LOWER(?) AND active = TRUE
    """, (name,))
    row = cursor.fetchone()
    conn.close()
    if row:
        logger.debug(f"Found medication '{name}'")
        return dict(row)
    logger.debug(f"No medication found with name '{name}'")
    return None


def log_medication_event(
    db_path: str,
    medication_id: int,
    status: str,
    source: str = 'user',
    notes: Optional[str] = None,
) -> int:
    """Insert a medication event and return its id."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO medication_events (medication_id, status, source, notes)
        VALUES (?, ?, ?, ?)
    """, (medication_id, status, source, notes))
    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    logger.info(f"Logged medication event: medication_id={medication_id}, status={status}, source={source}")
    return event_id


def log_group_events(
    db_path: str,
    group_id: int,
    taken_ids: list,
    skipped_ids: list,
    source: str = 'user',
) -> list:
    """
    Insert one event row per medication in the group.
    taken_ids get status='taken', skipped_ids get status='skipped'.
    Returns list of event ids.
    """
    event_ids = []
    for med_id in taken_ids:
        event_ids.append(log_medication_event(db_path, med_id, 'taken', source))
    for med_id in skipped_ids:
        event_ids.append(log_medication_event(db_path, med_id, 'skipped', source))
    logger.info(f"Logged {len(event_ids)} events for group {group_id}: {len(taken_ids)} taken, {len(skipped_ids)} skipped")
    return event_ids


def deactivate_medication(db_path: str, medication_id: int) -> None:
    """Set active=FALSE for a medication."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("UPDATE medications SET active = FALSE WHERE id = ?", (medication_id,))
    conn.commit()
    conn.close()
    logger.info(f"Deactivated medication id={medication_id}")


def seed_default_schedules(db_path: str) -> None:
    """Insert default schedules if the schedules table is empty."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Check if schedules table is empty
    cursor.execute("SELECT COUNT(*) as count FROM schedules")
    count = cursor.fetchone()['count']

    if count == 0:
        default_schedules = [
            (10, 0, "Good morning! How are you feeling today?", True),
            (20, 0, "Evening check-in: How was your day? Any symptoms or notes to share?", True)
        ]

        cursor.executemany("""
            INSERT INTO schedules (hour, minute, message_template, active)
            VALUES (?, ?, ?, ?)
        """, default_schedules)

        conn.commit()
        logger.info("Seeded default schedules")
    else:
        logger.debug("Schedules table already populated, skipping seed")

    conn.close()
