"""
One-time seed script: download CMS HCPCS/CPT codes and load into the cpt_codes table.

Usage:
    python -m src.medical.scripts.seed_cpt_codes --db-path data/123456789.db
    python -m src.medical.scripts.seed_cpt_codes --db-path data/123456789.db \
        --source-url https://www.cms.gov/.../HCPCS_RELEASE_2024.zip

Source:
    CMS HCPCS Quarterly Update (annual full release).
    See: https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update
    The exact filename changes annually — pass --source-url to override the default.

Format (CMS HCPCS Record Layout, Anweb_AnnualUpdate.txt):
    The annual zip contains a fixed-width text file. The HCPCS code is the first
    5 columns of each row; long description begins at column 92 (length 80).
    This script auto-detects whether the URL points at a CSV or a fixed-width file
    by inspecting the file extension after unzipping.

Idempotency:
    INSERT OR IGNORE is used so re-running the script will not fail on existing rows.

Stdlib only — no third-party dependencies.
"""

import argparse
import csv
import io
import logging
import sqlite3
import sys
import urllib.request
import zipfile
from typing import Iterable

from src.database import init_db

logger = logging.getLogger(__name__)

# Default source URL — annual HCPCS release. Override with --source-url.
DEFAULT_SOURCE_URL = (
    "https://www.cms.gov/files/zip/2024-alpha-numeric-hcpcs-file.zip"
)

DOWNLOAD_TIMEOUT_SECONDS = 60


def _download_zip(url: str) -> bytes:
    """Download a remote URL into memory. Returns the raw bytes."""
    logger.info(f"Downloading HCPCS source: {url}")
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        return response.read()


def _parse_csv(stream: io.TextIOBase) -> Iterable[tuple[str, str]]:
    """
    Parse a CSV stream where the first column is the HCPCS code and the second
    is its long description. Header row is auto-skipped if detected.
    """
    reader = csv.reader(stream)
    first = True
    for row in reader:
        if not row:
            continue
        if first:
            first = False
            # Skip header row if first column is non-alphanumeric or looks like a label
            if not row[0].strip() or not row[0].strip()[0].isalnum():
                continue
            if row[0].strip().lower() in ("code", "hcpcs", "hcpc"):
                continue
        code = row[0].strip()
        description = row[1].strip() if len(row) > 1 else ""
        if code:
            yield code, description


def _parse_fixed_width(stream: io.TextIOBase) -> Iterable[tuple[str, str]]:
    """
    Parse the CMS HCPCS fixed-width annual update text file.

    Layout (per CMS record layout):
        cols 0–4   : HCPCS code (5 chars)
        cols 91–171: long description (80 chars)
    The description column is sometimes shorter on truncated rows; we strip().
    """
    for line in stream:
        if len(line) < 5:
            continue
        code = line[0:5].strip()
        if not code or not code[0].isalnum():
            continue
        description = line[91:171].strip() if len(line) >= 92 else ""
        yield code, description


def _extract_rows(zip_bytes: bytes) -> list[tuple[str, str]]:
    """
    Open the zip, find the first .csv or .txt file, and parse rows out of it.
    Returns list of (code, description) tuples.
    """
    rows: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        logger.debug(f"Zip contents: {names}")

        # Prefer csv if present, else first txt
        target = next((n for n in names if n.lower().endswith(".csv")), None)
        if target is None:
            target = next((n for n in names if n.lower().endswith(".txt")), None)
        if target is None:
            logger.error("No .csv or .txt file found in HCPCS zip")
            return rows

        logger.info(f"Parsing {target} from HCPCS zip")
        with zf.open(target) as raw:
            text = io.TextIOWrapper(raw, encoding="latin-1", errors="replace")
            if target.lower().endswith(".csv"):
                rows = list(_parse_csv(text))
            else:
                rows = list(_parse_fixed_width(text))

    logger.info(f"Parsed {len(rows)} HCPCS row(s) from source")
    return rows


def _bulk_insert(db_path: str, rows: list[tuple[str, str]]) -> int:
    """
    Bulk-insert rows into cpt_codes using INSERT OR IGNORE. Returns the count
    of rows newly inserted (excluding duplicates).
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM cpt_codes")
    before = cursor.fetchone()[0]

    try:
        cursor.executemany(
            "INSERT OR IGNORE INTO cpt_codes (code, description) VALUES (?, ?)",
            rows,
        )
        conn.commit()
    except sqlite3.IntegrityError:
        logger.error("IntegrityError during bulk insert — partial rows may have been written", exc_info=True)
        conn.rollback()

    cursor.execute("SELECT COUNT(*) FROM cpt_codes")
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
    logger.info(f"Inserted {inserted}/{len(rows)} new HCPCS/CPT codes into {db_path}")
    return inserted


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Seed cpt_codes table from CMS HCPCS data.")
    parser.add_argument("--db-path", required=True, help="Path to user SQLite DB (e.g. data/123.db)")
    parser.add_argument(
        "--source-url",
        default=DEFAULT_SOURCE_URL,
        help=f"Override HCPCS zip URL (default: {DEFAULT_SOURCE_URL})",
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
