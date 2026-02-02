import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

def get_connection(db_path: str) -> sqlite3.Connection:
    """Get a SQLite database connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Return rows as dict-like objects
    return conn

def init_db(db_path: str) -> None:
    """Initialize database with required tables."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    # Create messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
            body TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            twilio_sid TEXT
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
    
    conn.commit()
    conn.close()
    logger.info("Database tables created/verified")

def insert_message(db_path: str, direction: str, body: str, twilio_sid: Optional[str] = None) -> int:
    """Insert a message into the database and return its ID."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO messages (direction, body, twilio_sid)
        VALUES (?, ?, ?)
    """, (direction, body, twilio_sid))
    
    message_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    logger.debug(f"Inserted {direction} message with ID {message_id}")
    return message_id

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

def seed_default_schedules(db_path: str) -> None:
    """Insert default schedules if the schedules table is empty."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    # Check if schedules table is empty
    cursor.execute("SELECT COUNT(*) as count FROM schedules")
    count = cursor.fetchone()['count']
    
    if count == 0:
        default_schedules = [
            (10, 0, "Good morning Shanelle! How are you feeling today?", True),
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
