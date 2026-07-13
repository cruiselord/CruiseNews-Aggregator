#!/usr/bin/env python3
"""
Phase 2 — Embedding helper for NaijaPulse (local Ollama).

Reusable, import-safe functions for turning article text into 768-dim vectors
via Ollama's batch `/api/embed` endpoint and storing them in the Supabase
`embeddings` table (one row per article per model).

Storage note: the schema keeps embeddings in a dedicated `embeddings` table
(article_id, model, vector(768)), NOT a column on `articles`. We upsert on
(article_id, model) so re-runs are idempotent.

Usage (programmatic):
    from embed_core import embed_texts, pending_article_ids, fetch_text, store_embedding
"""

import os
import time
import logging
from typing import List, Optional

import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/embed")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "200"))
CONCURRENCY = int(os.getenv("EMBED_CONCURRENCY", "4"))


def make_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise SystemExit("Missing SUPABASE_URL / SUPABASE_KEY in environment")
    return create_client(url, key)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def ensure_model(session: Optional[requests.Session] = None, model: str = EMBED_MODEL) -> None:
    """Pre-flight: confirm Ollama is reachable and `model` is pulled.

    Exits with a clear message if not, so the caller doesn't fail mid-batch.
    """
    s = session or make_session()
    try:
        base = OLLAMA_URL.rsplit("/api/", 1)[0]
        resp = s.get(f"{base}/api/tags", timeout=5)
        resp.raise_for_status()
        names = {m.get("name") for m in resp.json().get("models", [])}
        # tags include the suffix, e.g. "nomic-embed-text:latest"
        if not any(n.split(":")[0] == model for n in names):
            raise SystemExit(f"Ollama model '{model}' is not pulled. Run: ollama pull {model}")
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"Ollama is not reachable at {OLLAMA_URL}: {e}")


def embed_texts(texts: List[str], session: Optional[requests.Session] = None,
                model: str = EMBED_MODEL) -> List[List[float]]:
    """Batch-embed a list of texts via Ollama's /api/embed endpoint.

    ONE HTTP request for the whole batch (not one per article). Returns a list
    of 768-d vectors aligned 1:1 with `texts`. Ollama rejects empty strings, so
    blank texts are replaced with a single space to avoid a 500 on the batch.
    """
    if not texts:
        return []
    s = session or make_session()
    payload = {"model": model, "input": [t if t.strip() else " " for t in texts]}
    last_err = None
    for attempt in range(3):
        try:
            r = s.post(OLLAMA_URL, json=payload, timeout=120)
            r.raise_for_status()
            return r.json()["embeddings"]
        except Exception as e:
            last_err = e
            if attempt == 2:
                break
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Ollama embed failed after 3 attempts: {last_err}")


def pending_article_ids(client, model: str = EMBED_MODEL,
                        limit: int = 10000) -> List[str]:
    """Article IDs that have no `embeddings` row for `model` yet (idempotent).

    Fetches candidate article ids, then the already-embedded ids for this model,
    and diffs in Python. The dataset is small, so this is simpler and more
    reliable than a PostgREST NOT EXISTS join.

    The embedded-id lookup is chunked: PostgREST rejects a single .in_() filter
    once the serialized query (here, all article UUIDs) grows past its limits,
    returning HTTP 400 ("JSON could not be generated"). Same class of bug as
    Finding 7 in ingest_supabase.py — keep each round trip small.
    """
    arts = client.table("articles").select("id").limit(limit).execute().data or []
    if not arts:
        return []
    ids = [a["id"] for a in arts]
    done_set: set = set()
    chunk_size = 100
    for i in range(0, len(ids), chunk_size):
        batch = ids[i:i + chunk_size]
        try:
            done = (client.table("embeddings")
                    .select("article_id").in_("article_id", batch).eq("model", model)
                    .execute().data or [])
            done_set.update(d["article_id"] for d in done)
        except Exception as e:
            logger.warning(f"Pending-embeddings chunk {i // chunk_size} failed: {e}")
    return [i for i in ids if i not in done_set]


def fetch_text(client, article_id: str) -> str:
    """Build the embedding text for one article: title + '\\n\\n' + summary."""
    row = (client.table("articles").select("title,summary")
           .eq("id", article_id).limit(1).execute().data or [])
    if not row:
        return ""
    return f"{row[0]['title']}\n\n{row[0].get('summary') or ''}"


def _vector_literal(vec: List[float]) -> str:
    """pgvector wants a text literal like '[0.1,0.2,...]', not a JSON array."""
    return "[" + ",".join(f"{x:.8g}" for x in vec) + "]"


def store_embedding(client, article_id: str, vector: List[float],
                    model: str = EMBED_MODEL) -> None:
    """Idempotently store one embedding row keyed on (article_id, model).

    Implemented as delete-then-insert so it works whether or not a UNIQUE
    (article_id, model) constraint exists yet. If the constraint is added (see
    supabase/init_tables.sql), this can be simplified to a single upsert.
    """
    client.table("embeddings").delete().eq("article_id", article_id).eq("model", model).execute()
    client.table("embeddings").insert(
        {"article_id": article_id, "model": model, "vector": _vector_literal(vector)}
    ).execute()
