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
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

STARTED_AT = datetime.now(timezone.utc)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(script: str, *args: str) -> int:
    proc = subprocess.run([sys.executable, str(HERE / script), *args], check=False)
    return proc.returncode


def _run_timed(label: str, script: str, *args: str) -> tuple[int, float]:
    """Run a stage script, returning (returncode, elapsed_seconds)."""
    print(f"    ⏱  {label} started at {_stamp()}", flush=True)
    t0 = time.perf_counter()
    rc = _run(script, *args)
    dt = time.perf_counter() - t0
    status = "OK" if rc == 0 else f"FAILED (exit {rc})"
    print(f"    ⏱  {label} finished at {_stamp()} — {status} in {dt:.1f}s", flush=True)
    return rc, dt


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
    only_mode = args.dedup_only or args.cluster_only or args.bias_only
    do_embed = args.embed or full
    do_dedup = args.dedup or args.dedup_only or full
    do_cluster = bool(args.cluster or args.all or args.cluster_only or args.bias)
    do_bias = bool(args.bias or args.all or args.bias_only)
    do_ingest_and_schema = full or args.embed or not only_mode
    # ^ i.e. skip schema+ingestion ONLY when a bare "*-only" flag was passed alone

    steps = (2 if do_ingest_and_schema else 0) \
        + (1 if do_embed and not only_mode else 0) \
        + (1 if do_dedup else 0) + (1 if do_cluster else 0) + (1 if do_bias else 0)
    step = 0
    timings: list[tuple[str, str, float]] = []  # (phase_label, status, seconds)

    if do_ingest_and_schema:
        print(f"▶ Step {step+1}/{steps} — Setting up Supabase schema...")
        step += 1
        rc, dt = _run_timed("schema-setup", "setup_supabase.py")
        timings.append(("Schema setup", "OK" if rc == 0 else "FAILED", dt))
        if rc != 0:
            print(f"✗ Schema setup failed (exit {rc}). Aborting — nothing downstream "
                  f"can be trusted if the schema step failed.")
            return rc

        print(f"\n▶ Step {step+1}/{steps} — Running ingestion pipeline (Phase 1)...")
        step += 1
        rc, dt = _run_timed("ingestion", "ingest_supabase.py")
        timings.append(("Phase 1 — Ingestion", "OK" if rc == 0 else "FAILED", dt))
        if rc != 0:
            print(f"✗ Ingestion failed (exit {rc}). Aborting remaining stages — "
                  f"downstream stages would run against stale/incomplete data and "
                  f"falsely report success.")
            return rc
    else:
        print("↷ Skipping schema setup + ingestion ('*-only' flag — operating on "
              "existing data).")

    if do_embed:
        print(f"\n▶ Step {step+1}/{steps} — Embedding articles (Ollama, Phase 2)...")
        step += 1
        rc, dt = _run_timed("embedding", "embed_articles.py")
        timings.append(("Phase 2 — Embedding", "OK" if rc == 0 else "FAILED", dt))
        if rc != 0:
            print(f"✗ Embedding failed (exit {rc}). Aborting.")
            return rc

    if do_dedup:
        print(f"\n▶ Step {step+1}/{steps} — Near‑duplicate detection (Phase 3)...")
        step += 1
        rc, dt = _run_timed("dedup", "dedup.py")
        timings.append(("Phase 3 — Dedup", "OK" if rc == 0 else "FAILED", dt))
        if rc != 0:
            print(f"✗ Dedup failed (exit {rc}). Aborting.")
            return rc

    if do_cluster:
        print(f"\n▶ Step {step+1}/{steps} — Story clustering (Phase 4, Stages A–D)...")
        step += 1
        rc, dt = _run_timed("clustering", "cluster_stories.py")
        timings.append(("Phase 4 — Clustering", "OK" if rc == 0 else "FAILED", dt))
        if rc != 0:
            print(f"✗ Clustering failed (exit {rc}). Aborting.")
            return rc

    if do_bias:
        print(f"\n▶ Step {step+1}/{steps} — Bias tagging + blindspot detection (Phase 5)...")
        step += 1
        rc, dt = _run_timed("bias", "bias_blindspot.py")
        timings.append(("Phase 5 — Bias tagging", "OK" if rc == 0 else "FAILED", dt))
        if rc != 0:
            print(f"✗ Bias tagging failed (exit {rc}).")
            return rc

    total = sum(t for _, _, t in timings)
    print("\n" + "=" * 68)
    print("PIPELINE HEALTH REPORT")
    print("=" * 68)
    print(f"  Run started : {STARTED_AT.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Run finished: {_stamp()}")
    print(f"  Overall     : {'✓ ALL STAGES PASSED' if all(s == 'OK' for _, s, _ in timings) else '✗ ONE OR MORE STAGES FAILED'}")
    print("-" * 68)
    print(f"  {'PHASE':<26}{'STATUS':<10}{'TIME':>10}")
    for label, status, dt in timings:
        print(f"  {label:<26}{status:<10}{dt:>9.1f}s")
    print("-" * 68)
    print(f"  {'TOTAL':<26}{'':<10}{total:>9.1f}s")
    print("=" * 68)
    print("\n✓ Pipeline completed successfully — all stages ran and reported success.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
