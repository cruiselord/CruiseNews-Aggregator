#!/usr/bin/env python3
"""
NaijaPulse — Supabase schema bootstrap (run once, or on every ingestion start).

Strategy (in order):
  1. If SUPABASE_DB_URL is set, run supabase/init_tables.sql directly via psycopg2.
     This is the most reliable path (real DDL, idempotent SQL).
  2. Else if SUPABASE_URL + SUPABASE_KEY are set, try the built-in `sql` RPC
     (present on many projects). If that fails, fall back to step 3.
  3. Print the SQL so you can paste it into Supabase → SQL Editor once.

The script is safe to call repeatedly — every statement is `IF NOT EXISTS`.
"""

import os
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

SQL_FILE = Path(__file__).resolve().parent.parent / "supabase" / "init_tables.sql"


def _sql_text() -> str:
    return SQL_FILE.read_text(encoding="utf-8")


def _via_psycopg(url: str) -> bool:
    try:
        import psycopg2
    except ImportError:
        log.debug("psycopg2 not installed; skipping direct DB path.")
        return False
    try:
        conn = psycopg2.connect(url, connect_timeout=15)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(_sql_text())
        conn.close()
        log.info("✅ Tables created/applied via direct Postgres connection.")
        return True
    except Exception as e:
        log.warning(f"Direct DB connection failed: {e}")
        return False


def _via_rpc(url: str, key: str) -> bool:
    try:
        from supabase import create_client
    except ImportError:
        return False
    try:
        client = create_client(url, key)
        # Many Supabase projects expose a `sql` RPC that runs arbitrary statements.
        client.rpc("sql", {"stmt": _sql_text()}).execute()
        log.info("✅ Tables created/applied via the `sql` RPC.")
        return True
    except Exception as e:
        log.debug(f"`sql` RPC unavailable: {e!r}")
        return False


def main() -> int:
    if not SQL_FILE.is_file():
        log.error(f"Could not find schema file at {SQL_FILE}")
        return 1

    db_url = os.getenv("SUPABASE_DB_URL")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if db_url and _via_psycopg(db_url):
        return 0

    if url and key and _via_rpc(url, key):
        return 0

    # Fallback: print the SQL for a one-time manual apply.
    log.warning(
        "🚧 Could not apply schema automatically. "
        "Paste the following into Supabase → SQL Editor and click Run:"
    )
    print("\n----- BEGIN supabase/init_tables.sql -----\n")
    print(_sql_text().strip())
    print("\n----- END supabase/init_tables.sql -----\n")
    log.info(
        "Tip: set SUPABASE_DB_URL (Settings → Database → Connection string → URI) "
        "in your .env to enable fully automatic table creation."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
