import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from src.database import (
    get_connection, init_db, insert_message, get_recent_messages,
    get_active_schedules, seed_default_schedules, get_user_db_path,
    get_all_user_databases, deactivate_all_schedules,
    set_telegram_name, set_preferred_name, get_display_name,
)

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    yield db_path
    os.unlink(db_path)

@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing multi-user databases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

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

    # Check user_profile table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_profile'")
    assert cursor.fetchone() is not None

    # Check index exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_messages_timestamp'")
    assert cursor.fetchone() is not None

    conn.close()

def test_init_db_creates_parent_directory(temp_dir):
    """Test that init_db creates parent directories if they don't exist."""
    nested_path = os.path.join(temp_dir, "nested", "dir", "test.db")
    init_db(nested_path)

    assert os.path.exists(nested_path)
    os.unlink(nested_path)

def test_insert_message_returns_id(temp_db):
    """Test that insert_message returns the inserted message ID."""
    init_db(temp_db)

    message_id = insert_message(temp_db, 'inbound', 'Test message', 123)
    assert isinstance(message_id, int)
    assert message_id > 0

def test_insert_message_with_telegram_message_id(temp_db):
    """Test that insert_message stores Telegram message_id correctly."""
    init_db(temp_db)

    telegram_msg_id = 42
    row_id = insert_message(temp_db, 'inbound', 'Test message', telegram_msg_id)

    conn = get_connection(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT message_id FROM messages WHERE id = ?", (row_id,))
    stored_msg_id = cursor.fetchone()['message_id']
    conn.close()

    assert stored_msg_id == telegram_msg_id

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
    assert "Good morning" in morning_schedule['message_template']

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

def test_deactivate_all_schedules(temp_db):
    """Test that deactivate_all_schedules deactivates all active schedules."""
    init_db(temp_db)

    # Insert some active schedules
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO schedules (hour, minute, message_template, active) VALUES (?, ?, ?, ?)",
        [
            (8, 0, "Morning", True),
            (12, 0, "Noon", True),
            (18, 0, "Evening", True)
        ]
    )
    conn.commit()
    conn.close()

    # Verify they're active
    schedules = get_active_schedules(temp_db)
    assert len(schedules) == 3

    # Deactivate all
    deactivate_all_schedules(temp_db)

    # Verify none are active
    schedules = get_active_schedules(temp_db)
    assert len(schedules) == 0


# Multi-user database tests

def test_get_user_db_path():
    """Test that get_user_db_path returns correct path format."""
    db_path = get_user_db_path("data/", 123456789)
    assert db_path == "data/123456789.db"

def test_get_user_db_path_with_trailing_slash():
    """Test get_user_db_path handles paths with and without trailing slash."""
    path1 = get_user_db_path("data/", 123)
    path2 = get_user_db_path("data", 123)

    # Both should produce valid paths
    assert "123.db" in path1
    assert "123.db" in path2

def test_get_all_user_databases_empty_dir(temp_dir):
    """Test get_all_user_databases returns empty list for empty directory."""
    result = get_all_user_databases(temp_dir)
    assert result == []

def test_get_all_user_databases_nonexistent_dir():
    """Test get_all_user_databases returns empty list for nonexistent directory."""
    result = get_all_user_databases("/nonexistent/path/that/does/not/exist")
    assert result == []

def test_get_all_user_databases_finds_user_dbs(temp_dir):
    """Test get_all_user_databases finds all user database files."""
    # Create some user databases
    chat_ids = [123456789, 987654321, 555555555]
    for chat_id in chat_ids:
        db_path = os.path.join(temp_dir, f"{chat_id}.db")
        init_db(db_path)

    result = get_all_user_databases(temp_dir)

    assert len(result) == 3
    found_chat_ids = [chat_id for chat_id, _ in result]
    for chat_id in chat_ids:
        assert chat_id in found_chat_ids

def test_get_all_user_databases_ignores_non_numeric_files(temp_dir):
    """Test get_all_user_databases ignores files that don't match numeric pattern."""
    # Create a valid user database
    valid_db = os.path.join(temp_dir, "123456789.db")
    init_db(valid_db)

    # Create a non-numeric db file (should be ignored)
    invalid_db = os.path.join(temp_dir, "health_tracker.db")
    init_db(invalid_db)

    # Create another non-numeric file
    other_file = os.path.join(temp_dir, "config.db")
    with open(other_file, 'w') as f:
        f.write("test")

    result = get_all_user_databases(temp_dir)

    assert len(result) == 1
    assert result[0][0] == 123456789

def test_get_all_user_databases_returns_correct_paths(temp_dir):
    """Test get_all_user_databases returns correct full paths."""
    chat_id = 123456789
    db_path = os.path.join(temp_dir, f"{chat_id}.db")
    init_db(db_path)

    result = get_all_user_databases(temp_dir)

    assert len(result) == 1
    returned_chat_id, returned_path = result[0]
    assert returned_chat_id == chat_id
    assert returned_path == db_path
    assert os.path.exists(returned_path)


# User profile tests

def test_get_display_name_returns_none_for_new_user(temp_db):
    """get_display_name returns None when no name is set."""
    init_db(temp_db)
    assert get_display_name(temp_db) is None


def test_set_telegram_name_sets_display_name(temp_db):
    """set_telegram_name stores the Telegram name and get_display_name returns it."""
    init_db(temp_db)
    set_telegram_name(temp_db, "Alice")
    assert get_display_name(temp_db) == "Alice"


def test_set_preferred_name_overrides_telegram_name(temp_db):
    """get_display_name returns preferred name over telegram_name."""
    init_db(temp_db)
    set_telegram_name(temp_db, "Alice")
    set_preferred_name(temp_db, "Nel")
    assert get_display_name(temp_db) == "Nel"


def test_get_display_name_falls_back_to_telegram_name(temp_db):
    """get_display_name returns telegram_name when preferred name is not set."""
    init_db(temp_db)
    set_telegram_name(temp_db, "Bob")
    assert get_display_name(temp_db) == "Bob"


def test_set_preferred_name_updates_existing(temp_db):
    """set_preferred_name updates an already-set preferred name."""
    init_db(temp_db)
    set_preferred_name(temp_db, "Alice")
    assert get_display_name(temp_db) == "Alice"
    set_preferred_name(temp_db, "Bob")
    assert get_display_name(temp_db) == "Bob"


def test_init_db_migration_adds_telegram_name_column(temp_db):
    """init_db adds telegram_name column to existing DBs without it."""
    # Create a DB with the old schema (no telegram_name column)
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            name TEXT
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO user_profile (id, name) VALUES (1, NULL)")
    conn.commit()
    conn.close()

    # Running init_db should add the column without error
    init_db(temp_db)

    # Column should now exist
    conn = get_connection(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_name FROM user_profile WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    assert row is not None
