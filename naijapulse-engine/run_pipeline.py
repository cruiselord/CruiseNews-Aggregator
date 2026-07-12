#!/usr/bin/env python3
"""
NaijaPulse — one‑command pipeline runner.

Default (Phase 1):
    1. Creates/verifies the Supabase schema (setup_supabase.py).
    2. Runs the ingestion pipeline (ingest_supabase.py).

Flags compose into the full chain. The cron entry point is `--all`, which runs
Phases 1 → 2 → 3 → 4 → 5 in order with row‑count logging at every stage boundary:

    Phase 1  ingestion        (RSS → articles + inline embeddings)
    Phase 2  embedding        (Ollama nomic‑embed‑text on title+summary)
    Phase 3  dedup            (near‑duplicate / wire‑copy collapse)
    Phase 4  clustering       (story discovery + continuity, Stages A–D)
    Phase 5  bias tagging     (bias_distribution + blindspot detection)

Implication rules (each flag pulls in everything before it):
    --embed      ⇒ Phase 1+2
    --dedup      ⇒ Phase 1+2+3   (--dedup implies --embed)
    --cluster    ⇒ Phase 1+2+3+4 (--cluster implies --dedup ⇒ --embed)
    --bias       ⇒ Phase 1+2+3+4+5 (--bias implies --cluster ⇒ --dedup ⇒ --embed)
    --all        ⇒ full chain (alias for --bias)

Usage:
    ./venv/bin/python run_pipeline.py                  # Phase 1 only
    ./venv/bin/python run_pipeline.py --embed          # Phase 1 + 2
    ./venv/bin/python run_pipeline.py --dedup          # Phase 1 + 2 + 3
    ./venv/bin/python run_pipeline.py --cluster        # Phase 1 + 2 + 3 + 4
    ./venv/bin/python run_pipeline.py --bias           # FULL chain 1→5
    ./venv/bin/python run_pipeline.py --all            # same as --bias (full)
    ./venv/bin/python run_pipeline.py --dedup-only     # Phase 3 only (rerun)
    ./venv/bin/python run_pipeline.py --cluster-only   # Phase 4 only (rerun)
    ./venv/bin/python run_pipeline.py --bias-only      # Phase 5 only (rerun)
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
    parser.add_argument("--cluster", action="store_true",
                        help="FULL flow + story clustering (Phases 1+2+3+4).")
    parser.add_argument("--bias", action="store_true",
                        help="FULL flow + bias tagging/blindspot (Phases 1→5). "
                             "Implies --cluster ⇒ --dedup ⇒ --embed.")
    parser.add_argument("--all", action="store_true",
                        help="Alias for --bias: run the entire pipeline (Phases 1→5).")
    parser.add_argument("--dedup-only", action="store_true",
                        help="Run near‑duplicate detection only (Phase 3).")
    parser.add_argument("--cluster-only", action="store_true",
                        help="Run story clustering only (Phase 4) — assumes 1‑3 are done.")
    parser.add_argument("--bias-only", action="store_true",
                        help="Run bias tagging only (Phase 5) — assumes 1‑4 are done.")
    args = parser.parse_args()

    full = args.dedup or args.cluster or args.all or args.bias
    do_embed = args.embed or full
    do_dedup = args.dedup or args.dedup_only or full
    do_cluster = bool(args.cluster or args.all or args.cluster_only or args.bias)
    do_bias = bool(args.bias or args.all or args.bias_only)

    steps = 2 + (1 if do_embed else 0) + (1 if do_dedup else 0) \
        + (1 if do_cluster else 0) + (1 if do_bias else 0)
    step = 0

    print(f"▶ Step {step+1}/{steps} — Setting up Supabase schema...")
    step += 1
    rc = _run("setup_supabase.py")
    if rc != 0:
        print("⚠ Schema step returned non‑zero; continuing anyway.")

    print(f"\n▶ Step {step+1}/{steps} — Running ingestion pipeline (Phase 1)...")
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

    if do_cluster:
        print(f"\n▶ Step {step+1}/{steps} — Story clustering (Phase 4, Stages A–D)...")
        step += 1
        rc = _run("cluster_stories.py")

    if do_bias:
        print(f"\n▶ Step {step+1}/{steps} — Bias tagging + blindspot detection (Phase 5)...")
        step += 1
        rc = _run("bias_blindspot.py")

    return rc


if __name__ == "__main__":
    sys.exit(main())
