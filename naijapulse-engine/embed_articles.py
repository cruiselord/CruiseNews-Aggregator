#!/usr/bin/env python3
"""
Phase 2 — Embed articles via local Ollama (NaijaPulse).

Pages over articles that lack an `embeddings` row for the current model, embeds
their (title + summary) text via Ollama's batch endpoint, and stores the vectors
in the Supabase `embeddings` table. Idempotent: safe to re-run.

Usage:
    ./venv/bin/python embed_articles.py
    ./venv/bin/python embed_articles.py --limit 50
    ./venv/bin/python embed_articles.py --reembed      # clear then redo
"""

import argparse
import sys
import time
import logging

from embed_core import (
    make_client, make_session, ensure_model, embed_texts,
    pending_article_ids, fetch_text, store_embedding, EMBED_MODEL, BATCH_SIZE,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed articles via Ollama.")
    parser.add_argument("--limit", type=int, default=10000, help="Max articles to consider.")
    parser.add_argument("--model", default=EMBED_MODEL, help="Ollama embedding model name.")
    parser.add_argument("--reembed", action="store_true",
                        help="Delete existing embeddings for this model before re-embedding.")
    args = parser.parse_args()

    client = make_client()
    session = make_session()
    ensure_model(session, args.model)

    if args.reembed:
        logger.info(f"Clearing existing embeddings for model '{args.model}'...")
        client.table("embeddings").delete().eq("model", args.model).execute()

    pending = pending_article_ids(client, args.model, limit=args.limit)
    total = len(pending)
    logger.info(f"Articles needing embeddings: {total}")

    if total == 0:
        logger.info("Nothing to do — all articles already embedded.")
        return 0

    embedded = failed = 0
    start = time.time()

    # Process in BATCH_SIZE pages; one Ollama call per page (batch endpoint).
    for i in range(0, total, BATCH_SIZE):
        page = pending[i:i + BATCH_SIZE]
        texts = [fetch_text(client, aid) for aid in page]
        try:
            vectors = embed_texts(texts, session, model=args.model)
        except Exception as e:
            logger.error(f"Batch {i // BATCH_SIZE} failed: {e}")
            failed += len(page)
            continue
        for aid, vec in zip(page, vectors):
            try:
                store_embedding(client, aid, vec, model=args.model)
                embedded += 1
            except Exception as e:
                logger.error(f"Store failed for {aid}: {e}")
                failed += 1
        logger.info(f"  page {i // BATCH_SIZE + 1}: embedded {len(page)} texts")

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print("PHASE 2 — EMBEDDING RESULTS")
    print("=" * 60)
    print(f"Model:                {args.model}")
    print(f"Articles considered:  {total}")
    print(f"Embedded:             {embedded}")
    print(f"Failed:               {failed}")
    print(f"Total time:           {elapsed:.1f}s")
    print(f"Throughput:           {embedded / elapsed:.1f} articles/s" if elapsed > 0 else "")
    print("=" * 60)

    # Acceptance target: >=100 articles in <2 min.
    if total >= 100 and elapsed > 120:
        logger.warning("Acceptance target missed: 100+ articles took > 2 min.")
        return 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
