"""
Document storage and polymorphic linking helpers (Phase 3).

Tables managed here (schema owned by src.database.init_db()):
- documents
- document_links

Filesystem layout:
    {documents_dir}/{chat_id}/documents/{yyyy}/{mm}/{stem}_{uuid8}{ext}

The {yyyy}/{mm} segments come from `document_date` if provided (parsed as
ISO-8601 YYYY-MM-DD), otherwise from datetime.now(). A short uuid suffix is
appended to the filename stem to prevent collisions when the same original
filename arrives multiple times in the same month.

All public functions:
- are synchronous (callers should wrap in asyncio.to_thread() inside async code)
- return dict | None or list[dict]
- never raise — they catch exceptions, log with exc_info=True, and return None / []
"""

import logging
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from src.database import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal: filesystem path resolution
# ---------------------------------------------------------------------------

def _resolve_document_path(
    documents_dir: str,
    chat_id: int,
    document_date: Optional[str],
    original_name: str,
) -> str:
    """
    Compute the on-disk path for a new document.

    Layout: {documents_dir}/{chat_id}/documents/{yyyy}/{mm}/{stem}_{uuid8}{ext}

    - `document_date` is parsed as ISO-8601 YYYY-MM-DD; falls back to now() on
      None or parse failure.
    - A 8-char uuid suffix is appended to `original_name`'s stem so multiple
      uploads of the same filename in the same month do not collide.
    - Does NOT create the directory; the caller is responsible.
    """
    yyyy: str
    mm: str
    if document_date:
        try:
            parsed = datetime.strptime(document_date, "%Y-%m-%d")
            yyyy = f"{parsed.year:04d}"
            mm = f"{parsed.month:02d}"
        except ValueError:
            logger.warning(
                f"Could not parse document_date='{document_date}', falling back to now()"
            )
            now = datetime.now()
            yyyy = f"{now.year:04d}"
            mm = f"{now.month:02d}"
    else:
        now = datetime.now()
        yyyy = f"{now.year:04d}"
        mm = f"{now.month:02d}"

    stem, ext = os.path.splitext(original_name)
    if not stem:
        stem = "document"
    suffix = uuid.uuid4().hex[:8]
    filename = f"{stem}_{suffix}{ext}"

    return os.path.join(documents_dir, str(chat_id), "documents", yyyy, mm, filename)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def save_document(
    documents_dir: str,
    chat_id: int,
    db_path: str,
    file_bytes: bytes,
    original_name: str,
    mime_type: str,
    doc_type: str,
    document_date: Optional[str],
    notes: Optional[str],
) -> Optional[dict]:
    """
    Persist a document to disk and insert a row in the documents table.

    Returns the inserted row dict, or None on failure. On disk-write failure,
    no DB row is inserted. On DB-insert failure after a successful write, the
    file is left in place (best-effort) and we log + return None.
    """
    file_path = _resolve_document_path(documents_dir, chat_id, document_date, original_name)

    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(file_bytes)
    except Exception:
        logger.error(
            f"Failed to write document to disk path='{file_path}' "
            f"chat_id={chat_id} original_name='{original_name}'",
            exc_info=True,
        )
        return None

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO documents
                (chat_id, file_path, original_name, mime_type, doc_type, document_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (chat_id, file_path, original_name, mime_type, doc_type, document_date, notes),
        )
        document_id = cursor.lastrowid
        conn.commit()

        cursor.execute(
            """
            SELECT id, chat_id, file_path, original_name, mime_type,
                   doc_type, document_date, notes, created_at
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        )
        row = cursor.fetchone()
        logger.info(
            f"Saved document id={document_id} chat_id={chat_id} doc_type='{doc_type}' "
            f"path='{file_path}'"
        )
        return dict(row) if row else None
    except Exception:
        logger.error(
            f"Failed to insert documents row chat_id={chat_id} "
            f"original_name='{original_name}' path='{file_path}'",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def attach_document(
    db_path: str,
    document_id: int,
    entity_type: str,
    entity_id: int,
) -> Optional[dict]:
    """
    Link an existing document to an entity (claim/encounter/procedure/adjudication).

    Returns the inserted document_links row dict, or None on duplicate
    UNIQUE(document_id, entity_type, entity_id) / failure.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO document_links (document_id, entity_type, entity_id)
            VALUES (?, ?, ?)
            """,
            (document_id, entity_type, entity_id),
        )
        link_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            """
            SELECT id, document_id, entity_type, entity_id
            FROM document_links
            WHERE id = ?
            """,
            (link_id,),
        )
        row = cursor.fetchone()
        logger.info(
            f"Attached document_id={document_id} to entity_type='{entity_type}' "
            f"entity_id={entity_id} (link_id={link_id})"
        )
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        logger.debug(
            f"Duplicate document_link document_id={document_id} "
            f"entity_type='{entity_type}' entity_id={entity_id}, skipping"
        )
        return None
    except Exception:
        logger.error(
            f"Failed to attach document_id={document_id} to "
            f"entity_type='{entity_type}' entity_id={entity_id}",
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def list_documents_for_entity(
    db_path: str,
    entity_type: str,
    entity_id: int,
) -> list[dict]:
    """
    Return all documents linked to a given entity.

    Each row contains the full documents row plus a `link_id` column from the
    join table (so callers can detach a specific link if needed).
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.id            AS id,
                   d.chat_id       AS chat_id,
                   d.file_path     AS file_path,
                   d.original_name AS original_name,
                   d.mime_type     AS mime_type,
                   d.doc_type      AS doc_type,
                   d.document_date AS document_date,
                   d.notes         AS notes,
                   d.created_at    AS created_at,
                   dl.id           AS link_id
            FROM documents d
            JOIN document_links dl ON dl.document_id = d.id
            WHERE dl.entity_type = ? AND dl.entity_id = ?
            ORDER BY dl.id ASC
            """,
            (entity_type, entity_id),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(
            f"Retrieved {len(rows)} document(s) for "
            f"entity_type='{entity_type}' entity_id={entity_id}"
        )
        return rows
    except Exception:
        logger.error(
            f"Failed to list documents for entity_type='{entity_type}' entity_id={entity_id}",
            exc_info=True,
        )
        return []
    finally:
        conn.close()
