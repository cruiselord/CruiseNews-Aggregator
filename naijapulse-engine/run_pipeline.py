#!/usr/bin/env python3
"""
NaijaPulse — one‑command pipeline runner.

Default (Phase 1):
    1. Creates/verifies the Supabase schema (setup_supabase.py).
    2. Runs the ingestion pipeline (ingest_supabase.py).

With --embed (Phase 1 + 2):
    3. Embeds any articles missing vectors (embed_articles.py).

With --dedup (Phase 1 + 2 + 3 — the FULL flow):
    3. Embeds any articles missing vectors (embed_articles.py).
    4. Runs near‑duplicate detection (dedup.py) — linked directly to Phase 2,
       so embeddings feed straight into duplicate detection with no manual step.

With --dedup-only (Phase 3 only):
    Runs dedup.py on its own (e.g. to re‑process articles ingested earlier).

Usage:
    ./venv/bin/python run_pipeline.py                 # Phase 1 only
    ./venv/bin/python run_pipeline.py --embed         # Phase 1 + embeddings
    ./venv/bin/python run_pipeline.py --dedup         # Phase 1 + 2 + 3 (full)
    ./venv/bin/python run_pipeline.py --dedup-only    # Phase 3 only (rerun)
"""

import sys
import argparse
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _run(script: str, *args: str) -> int:
    proc = subprocess.run([sys.executable, str(HERE / script), *args], check=False)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="NaijaPulse pipeline runner.")
    parser.add_argument("--embed", action="store_true",
                        help="Backfill embeddings after ingestion (Phase 2).")
    parser.add_argument("--dedup", action="store_true",
                        help="FULL flow: ingest + embed + near‑duplicate detection (Phases 1+2+3).")
    parser.add_argument("--dedup-only", action="store_true",
                        help="Run near‑duplicate detection only (Phase 3).")
    args = parser.parse_args()

    # --dedup implies --embed (dedup needs the Phase 2 vectors)
    full = args.dedup
    do_embed = args.embed or full
    do_dedup = args.dedup or args.dedup_only

    steps = 2 + (1 if do_embed else 0) + (1 if do_dedup else 0)
    step = 0

    print(f"▶ Step {step+1}/{steps} — Setting up Supabase schema...")
    step += 1
    rc = _run("setup_supabase.py")
    if rc != 0:
        print("⚠ Schema step returned non‑zero; continuing anyway.")

    print(f"\n▶ Step {step+1}/{steps} — Running ingestion pipeline...")
    step += 1
    rc = _run("ingest_supabase.py")

    if do_embed:
        print(f"\n▶ Step {step+1}/{steps} — Embedding articles (Ollama, Phase 2)...")
        step += 1
        rc = _run("embed_articles.py")

    if do_dedup:
        print(f"\n▶ Step {step+1}/{steps} — Near‑duplicate detection (Phase 3)...")
        step += 1
        rc = _run("dedup.py")

    return rc


if __name__ == "__main__":
    sys.exit(main())
