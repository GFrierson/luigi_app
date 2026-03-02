import sqlite3
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

def get_connection(db_path: str) -> sqlite3.Connection:
    """Get a SQLite database connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Return rows as dict-like objects
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

    # Insert default profile row if not exists
    cursor.execute("""
        INSERT OR IGNORE INTO user_profile (id, name) VALUES (1, NULL)
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
    cutoff_time = datetime.now() - timedelta(hours=hours)
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
