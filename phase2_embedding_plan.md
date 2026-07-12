# Phase 2 — Embedding (Ollama) — SUPERSEDED

This draft plan is **superseded**. Phase 2 was implemented on 2026‑07‑12.

See the approved plan at `.claude/plans/iterative-zooming-octopus.md` and the
status in `PROGRESS.md` (Phase 2 = ✅ Done).

## What was actually built
- `naijapulse-engine/embed_core.py` — batch embedding via Ollama `/api/embed` + Supabase helpers.
- `naijapulse-engine/embed_articles.py` — fills missing embeddings (124/124 in 76 s).
- Inline embed + `embedded`/`embed_failed` counters in `ingest_supabase.py`.
- `run_pipeline.py --embed` flag.
- `supabase/init_tables.sql` adds `UNIQUE (article_id, model)` on `embeddings`.

## Key design correction vs. this draft
The draft assumed an `articles.embedding` **column**. The real schema uses a
dedicated `embeddings` table, which is the better design — so vectors are
written there (idempotent delete‑then‑insert), not as a column on `articles`.
