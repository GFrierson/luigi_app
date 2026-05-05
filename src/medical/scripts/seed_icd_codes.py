"""
One-time seed script: download CMS ICD-10-CM codes and load into the icd_codes table.

Usage:
    python -m src.medical.scripts.seed_icd_codes --db-path data/123456789.db
    python -m src.medical.scripts.seed_icd_codes --db-path data/123456789.db \
        --source-url https://www.cms.gov/.../ICD10CM-2024.zip

Source:
    CMS ICD-10-CM annual release (FY codes).
    See: https://www.cms.gov/medicare/coding-billing/icd-10-codes
    The exact filename changes annually — pass --source-url to override the default.

Format (CMS ICD-10-CM "Code Descriptions" file, e.g. icd10cm_codes_YYYY.txt):
    Each line is fixed-width:
        cols 0–6   : ICD-10-CM code (up to 7 chars, no decimal point — left-aligned, blank-padded)
        cols 8+    : long description (rest of line, after stripping)
    Some releases use a single space rather than a fixed gap; this parser
    splits on the FIRST run of whitespace after column 7 to be tolerant.

Idempotency:
    INSERT OR IGNORE is used so re-running the script will not fail on existing rows.

Stdlib only — no third-party dependencies.
"""

import argparse
import io
import logging
import sqlite3
import sys
import urllib.request
import zipfile
from typing import Iterable

from src.database import init_db

logger = logging.getLogger(__name__)

# Default source URL — annual ICD-10-CM release. Override with --source-url.
DEFAULT_SOURCE_URL = (
    "https://www.cms.gov/files/zip/2024-code-descriptions-tabular-order-updated-02012024.zip"
)

DOWNLOAD_TIMEOUT_SECONDS = 60


def _download_zip(url: str) -> bytes:
    """Download a remote URL into memory. Returns the raw bytes."""
    logger.info(f"Downloading ICD-10-CM source: {url}")
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        return response.read()


def _parse_fixed_width(stream: io.TextIOBase) -> Iterable[tuple[str, str]]:
    """
    Parse the CMS ICD-10-CM code descriptions text file.

    Per the CMS layout, code occupies columns 0–6 (left-aligned, blank-padded to 7 chars)
    and the description begins after a whitespace gap. We split on the first whitespace
    run after column 7 to be robust to single-space and multi-space variants.
    """
    for raw_line in stream:
        line = raw_line.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        # The first 7 columns hold the code (left-aligned, padded with spaces).
        code = line[0:7].strip()
        if not code or not code[0].isalnum():
            continue
        description = line[7:].strip()
        yield code, description


def _extract_rows(zip_bytes: bytes) -> list[tuple[str, str]]:
    """
    Open the zip, find the first descriptions .txt file, and parse rows.
    Returns list of (code, description) tuples.
    """
    rows: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        logger.debug(f"Zip contents: {names}")

        # Prefer files whose names mention "codes" or "descriptions" and end in .txt
        candidates = [
            n for n in names
            if n.lower().endswith(".txt")
            and ("code" in n.lower() or "description" in n.lower())
        ]
        target = candidates[0] if candidates else next(
            (n for n in names if n.lower().endswith(".txt")), None
        )
        if target is None:
            logger.error("No .txt file found in ICD-10-CM zip")
            return rows

        logger.info(f"Parsing {target} from ICD-10-CM zip")
        with zf.open(target) as raw:
            text = io.TextIOWrapper(raw, encoding="latin-1", errors="replace")
            rows = list(_parse_fixed_width(text))

    logger.info(f"Parsed {len(rows)} ICD-10-CM row(s) from source")
    return rows


def _bulk_insert(db_path: str, rows: list[tuple[str, str]]) -> int:
    """
    Bulk-insert rows into icd_codes using INSERT OR IGNORE. Returns count of
    rows newly inserted (excluding duplicates already present).
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM icd_codes")
    before = cursor.fetchone()[0]

    try:
        cursor.executemany(
            "INSERT OR IGNORE INTO icd_codes (code, description) VALUES (?, ?)",
            rows,
        )
        conn.commit()
    except sqlite3.IntegrityError:
        logger.error("IntegrityError during bulk insert — partial rows may have been written", exc_info=True)
        conn.rollback()

    cursor.execute("SELECT COUNT(*) FROM icd_codes")
    after = cursor.fetchone()[0]
    conn.close()

    inserted = after - before
    return inserted


def seed(db_path: str, source_url: str) -> int:
    """Run the full seed pipeline. Returns count of rows newly inserted."""
    init_db(db_path)
    zip_bytes = _download_zip(source_url)
    rows = _extract_rows(zip_bytes)
    if not rows:
        logger.warning("No rows parsed from source — nothing to insert")
        return 0
    inserted = _bulk_insert(db_path, rows)
    logger.info(f"Inserted {inserted}/{len(rows)} new ICD-10-CM codes into {db_path}")
    return inserted


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Seed icd_codes table from CMS ICD-10-CM data.")
    parser.add_argument("--db-path", required=True, help="Path to user SQLite DB (e.g. data/123.db)")
    parser.add_argument(
        "--source-url",
        default=DEFAULT_SOURCE_URL,
        help=f"Override ICD-10-CM zip URL (default: {DEFAULT_SOURCE_URL})",
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
        seed(args.db_path, args.source_url)
        return 0
    except Exception:
        logger.error("Seed script failed", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
