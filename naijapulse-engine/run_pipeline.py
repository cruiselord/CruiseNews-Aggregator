#!/usr/bin/env python3
"""
NaijaPulse — one‑command pipeline runner.

1. Creates/verifies the Supabase schema (setup_supabase.py).
2. Runs the ingestion pipeline (ingest_supabase.py).

Usage:
    ./venv/bin/python run_pipeline.py
"""

import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _run(script: str) -> int:
    proc = subprocess.run([sys.executable, str(HERE / script)], check=False)
    return proc.returncode


def main() -> int:
    print("▶ Step 1/2 — Setting up Supabase schema...")
    rc = _run("setup_supabase.py")
    if rc != 0:
        print("⚠ Schema step returned non‑zero; continuing anyway.")

    print("\n▶ Step 2/2 — Running ingestion pipeline...")
    return _run("ingest_supabase.py")


if __name__ == "__main__":
    sys.exit(main())
