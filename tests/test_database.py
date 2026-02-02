import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from src.database import (
    get_connection, init_db, insert_message, get_recent_messages,
    get_active_schedules, seed_default_schedules
)

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    yield db_path
    os.unlink(db_path)

def test_init_db_creates_tables(temp_db):
    """Test that init_db creates the required tables."""
    init_db(temp_db)
    
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    
    # Check messages table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
    assert cursor.fetchone() is not None
    
    # Check schedules table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schedules'")
    assert cursor.fetchone() is not None
    
    # Check index exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_messages_timestamp'")
    assert cursor.fetchone() is not None
    
    conn.close()

def test_insert_message_returns_id(temp_db):
    """Test that insert_message returns the inserted message ID."""
    init_db(temp_db)
    
    message_id = insert_message(temp_db, 'inbound', 'Test message', 'SM123')
    assert isinstance(message_id, int)
    assert message_id > 0

def test_get_recent_messages_respects_limit(temp_db):
    """Test that get_recent_messages respects the limit parameter."""
    init_db(temp_db)
    
    # Insert 10 messages
    for i in range(10):
        insert_message(temp_db, 'inbound', f'Message {i}')
    
    # Request only 5 messages
    messages = get_recent_messages(temp_db, limit=5, hours=24)
    assert len(messages) == 5

def test_get_recent_messages_respects_time_window(temp_db):
    """Test that get_recent_messages respects the time window."""
    init_db(temp_db)
    
    # Insert messages with specific timestamps
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    
    # Insert an old message (more than 24 hours ago)
    old_time = (datetime.now() - timedelta(hours=25)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "INSERT INTO messages (direction, body, timestamp) VALUES (?, ?, ?)",
        ('inbound', 'Old message', old_time)
    )
    
    # Insert a recent message
    recent_time = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "INSERT INTO messages (direction, body, timestamp) VALUES (?, ?, ?)",
        ('inbound', 'Recent message', recent_time)
    )
    
    conn.commit()
    conn.close()
    
    # Get messages from last 24 hours
    messages = get_recent_messages(temp_db, limit=10, hours=24)
    assert len(messages) == 1
    assert messages[0]['body'] == 'Recent message'

def test_get_recent_messages_returns_lesser_of_limit_or_time(temp_db):
    """Test that get_recent_messages returns the lesser of limit or time-based results."""
    init_db(temp_db)
    
    # Insert 3 messages
    for i in range(3):
        insert_message(temp_db, 'inbound', f'Message {i}')
    
    # With limit=5 and hours=24, should return 3 (all messages, less than limit)
    messages = get_recent_messages(temp_db, limit=5, hours=24)
    assert len(messages) == 3
    
    # With limit=2 and hours=24, should return 2 (limit is smaller)
    messages = get_recent_messages(temp_db, limit=2, hours=24)
    assert len(messages) == 2

def test_seed_default_schedules_inserts_when_empty(temp_db):
    """Test that seed_default_schedules inserts defaults when table is empty."""
    init_db(temp_db)
    
    # Seed defaults
    seed_default_schedules(temp_db)
    
    # Check schedules were inserted
    schedules = get_active_schedules(temp_db)
    assert len(schedules) == 2
    
    # Verify the schedule contents
    morning_schedule = next(s for s in schedules if s['hour'] == 10)
    evening_schedule = next(s for s in schedules if s['hour'] == 20)
    
    assert morning_schedule['minute'] == 0
    assert "Good morning Shanelle" in morning_schedule['message_template']
    
    assert evening_schedule['minute'] == 0
    assert "Evening check-in" in evening_schedule['message_template']

def test_seed_default_schedules_does_nothing_when_populated(temp_db):
    """Test that seed_default_schedules does nothing when table already has data."""
    init_db(temp_db)
    
    # Manually insert a schedule
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO schedules (hour, minute, message_template, active) VALUES (?, ?, ?, ?)",
        (9, 30, "Custom schedule", True)
    )
    conn.commit()
    conn.close()
    
    # Seed defaults (should not add more)
    seed_default_schedules(temp_db)
    
    # Should still have only 1 schedule
    schedules = get_active_schedules(temp_db)
    assert len(schedules) == 1
    assert schedules[0]['hour'] == 9

def test_get_active_schedules_returns_only_active(temp_db):
    """Test that get_active_schedules returns only active schedules."""
    init_db(temp_db)
    
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    
    # Insert active and inactive schedules
    cursor.executemany(
        "INSERT INTO schedules (hour, minute, message_template, active) VALUES (?, ?, ?, ?)",
        [
            (8, 0, "Active morning", True),
            (12, 0, "Inactive noon", False),
            (18, 0, "Active evening", True)
        ]
    )
    conn.commit()
    conn.close()
    
    # Should return only active schedules
    schedules = get_active_schedules(temp_db)
    assert len(schedules) == 2
    assert all(s['active'] for s in schedules)
