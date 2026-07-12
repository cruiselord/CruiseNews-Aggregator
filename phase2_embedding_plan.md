# Phase 2 – Embedding (Ollama) – Implementation Plan

**Goal**: Generate vector embeddings for each article’s textual content and store them in the Supabase `articles.embedding` column (pgvector). This enables downstream similarity, deduplication, and clustering.

---

## 1. Overview of the Embedding Pipeline

1. **Select source text** – Use a deterministic concatenation of `title` + `summary`. We avoid full‑text because:
   - Full‑text is large and slower to embed.
   - Summaries capture the core story and keep token count low, matching the behavior of the Ground News spec.
   - The spec explicitly recommends this approach (see *Phase 2* description).
2. **Identify pending articles** – Query Supabase for rows where `embedding IS NULL`.
3. **Batch processing** – Process articles in configurable batches (default 100). This balances memory use and API call overhead.
4. **Embedding generation** – Call Ollama locally with the `nomic-embed-text` model via the HTTP API (default `http://localhost:11434/api/embeddings`).
5. **Persist results** – Upsert the returned vector into the `embedding` column.
6. **Logging & metrics** – Record per‑batch timing, success / failure counts, and any API errors.

---

## 2. Architectural Decisions & Trade‑offs

| Option | Pros | Cons | Recommendation |
|--------|------|------|----------------|
| **A. Inline embedding inside `run_pipeline.py`** (single‑process) | Simple, no extra services. | Long‑running script can be interrupted; no parallelism; slower for many articles. | Acceptable for prototyping, but we’ll add optional concurrency (see below). |
| **B. Separate background worker (e.g., `embed_worker.py`)** launched via `cron` or `launchd` | Decouples ingestion from embedding; can run continuously. | Additional process management; need a scheduler. | Preferred for production‑grade pipelines after proof‑of‑concept. |
| **C. Parallel embedding using Python `concurrent.futures`** | Faster wall‑clock time (multiple HTTP calls in parallel). | Ollama can handle concurrent requests, but too many may saturate CPU/RAM. | Implement configurable concurrency (default `max_workers=4`). |
| **D. Use Supabase Edge Functions** to host embedding service | Offloads compute to cloud, easier scaling. | Requires network round‑trip, possible auth/latency; Supabase free tier limits compute time. | Not suitable now; we have local Ollama.

**Chosen approach**: Start with **Option C** – a parallel worker embedded in a dedicated script (`embedding_job.py`). This gives us performance without external services and can later be split into a daemon (Option B) if needed.

---

## 3. Detailed Steps

1. **Create a new script** `naijapulse-engine/embed_articles.py`
   - Import `supabase` client (`supabase-py`), `requests` for Ollama, `json`, `time`, `concurrent.futures`.
   - Load environment variables (`SUPABASE_URL`, `SUPABASE_KEY`) via `python-dotenv` (already used elsewhere).
2. **Define helper functions**
   - `fetch_pending(limit: int, offset: int) -> List[Dict]` – SELECT rows where `embedding IS NULL`.
   - `build_prompt(article: Dict) -> str` – Concatenate `title` and `summary` with a separator (e.g., "\n\n").
   - `call_ollama(text: str) -> List[float]` – POST to `http://localhost:11434/api/embeddings` with JSON `{"model": "nomic-embed-text", "prompt": text}`. Parse `embedding` field.
   - `store_embedding(article_id: str, vector: List[float])` – UPDATE the row.
3. **Batch loop**
   ```python
   BATCH_SIZE = 100
   OFFSET = 0
   while True:
       batch = fetch_pending(BATCH_SIZE, OFFSET)
       if not batch:
           break
       with ThreadPoolExecutor(max_workers=4) as executor:
           futures = {executor.submit(process_one, a): a for a in batch}
           for f in as_completed(futures):
               # handle success/failure logging
       OFFSET += BATCH_SIZE
   ```
   - `process_one` calls `build_prompt`, `call_ollama`, then `store_embedding`.
4. **Error handling**
   - Retry up to 3 times on network errors.
   - If Ollama returns a non‑200 or malformed response, log the article ID and continue.
   - Use exponential back‑off (e.g., 1s, 2s, 4s).
5. **Metrics & reporting**
   - Print a summary at the end: total processed, successes, failures, total time.
   - Write an optional JSON log file (`embedding_job.log.json`) for later audit.
6. **Integration with existing pipeline**
   - Add a new sub‑command to `run_pipeline.py` (e.g., `--embed`) that invokes this script after ingestion.
   - Update the top‑level README/PROGRESS.md to reflect the new step.

---

## 4. Performance Optimizations

- **Chunk size**: 100‑200 articles per batch gives a good balance; can be tuned via env var `EMBED_BATCH_SIZE`.
- **Concurrency limit**: Respect Ollama’s CPU usage; default `max_workers=4` on a MacBook with 8 cores. Expose via `EMBED_CONCURRENCY`.
- **Vector storage**: Ensure the `pgvector` extension is installed (`CREATE EXTENSION IF NOT EXISTS vector;`). The column is already defined in the schema.
- **Avoid re‑embedding**: The SELECT filter on `embedding IS NULL` guarantees idempotence. If you need to recompute, provide a `--reembed` flag that clears the column first.
- **Persisted HTTP session**: Use `requests.Session()` to reuse TCP connections to Ollama.

---

## 5. Acceptance Test (per spec)

1. **Preparation** – Ensure at least 100 articles with `embedding IS NULL` exist (run ingestion if needed).
2. **Execution** – Run `python -m naijapulse_engine.embed_articles` (or via `run_pipeline.py --embed`).
3. **Success criteria**
   - **Timing**: 100 articles processed in ≤ 2 minutes on the developer’s Mac.
   - **Embedding presence** – After the run, query Supabase and verify `embedding` is not NULL for those rows.
   - **Similarity sanity check** – Pick 5 known‑related articles (e.g., same event) and compute cosine similarity between their embeddings using a small helper script. Expect > 0.7 similarity, while random pairs should be < 0.4.
   - **Logging** – A console summary and JSON log file are produced with no uncaught exceptions.
4. **Failure handling** – If any articles failed, the log must contain their IDs and error messages. The run should still be considered a pass if ≤ 5 % failures (allowing for occasional network hiccups).

---

## 6. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Ollama process not running | Entire pipeline stalls | Add a pre‑flight check that `GET http://localhost:11434/api/version` succeeds; otherwise exit with a clear error.
| Supabase rate limits / quota | Slow batch updates | Use upsert with `on_conflict='id'` and limit to 100 rows per request (the SDK already does this).
| Memory blow‑up for large batches | OOM crash | Keep batch size modest; monitor memory usage; fallback to smaller batch on failure.
| Embedding dimension mismatch (future model change) | DB column size mismatch | Store dimension size in a constant; if model changes, run a migration to adjust `vector(768)`.

---

## 7. Future Extensions (post‑phase 2)

- **Full‑text embedding fallback** – If `summary` is missing, fall back to truncated `full_text` (first 500 tokens).
- **Scheduled embedding refresh** – Periodic job (cron) that re‑embeds recent articles when the model is upgraded.
- **Embedding quality monitoring** – Store a checksum of the input text; when the model/version changes, recompute and compare cosine similarity distributions.
- **Edge‑function wrapper** – Provide a Supabase Edge Function that accepts a text payload and returns an embedding; useful for on‑demand API calls.

---

## 8. Next Steps

1. Implement `embed_articles.py` as described.
2. Add the `--embed` flag to `run_pipeline.py`.
3. Run the acceptance test and record results in `PROGRESS.md`.
4. Once the test passes, merge to `main` and update the phase status in `PROGRESS.md`.
5. Discuss any desired tweaks (e.g., concurrency level) before proceeding to Phase 3 (Deduplication).

---

*Prepared for review. Please approve or suggest adjustments before any code is written.*