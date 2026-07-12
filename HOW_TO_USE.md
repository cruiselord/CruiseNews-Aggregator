# NaijaPulse Engine — Complete How-To Guide (Phases 1 → 6)

This guide is written for a **complete beginner** who has just been handed this
project and needs to go from "I cloned the folder" to "the API is serving live
data." It covers the **entire application** — ingestion, embeddings,
de-duplication, clustering, bias/blind-spot tagging, and the read-only API —
plus what to do, what *not* to do, and how to know it all worked.

---

## 0. What this application does (the 30-second version)

NaijaPulse ingests articles from ~10 Nigerian news RSS feeds, stores them in a
**Supabase** cloud database, then runs a 5-stage pipeline that turns raw articles
into **"stories"** (clusters of articles about the same event) with a
**media-bias distribution** per story. **Phase 6** is a small read-only web API
that lets you inspect all of that over HTTP.

```
RSS feeds ──▶ Phase 1 Ingestion ──▶ Phase 2 Embeddings ──▶ Phase 3 Dedup
                                                                    │
                                          Phase 5 Bias/Blindspot ◀── Phase 4 Clustering
                                                                    │
                                                          Phase 6 Read-only API (you query this)
```

Everything below runs from the `naijapulse-engine/` folder.

---

## 1. First-time setup

### 1a. Open a terminal and enter the engine folder
```bash
cd /Users/Adegoke/Documents/news/naijapulse-engine
```
> If you're not Adegoke, replace the path with wherever the project lives. The
> important thing is to end up **inside** the folder that contains
> `run_pipeline.py` and `phase6_api.py`.

### 1b. Install Python dependencies
All dependencies live in the bundled `venv`. Install them once:
```bash
./venv/bin/pip install -r ../requirements.txt
```
The `requirements.txt` (repo root) lists: `supabase`, `python-dotenv`,
`feedparser`, `trafilatura`, `fastapi`, `uvicorn`. A few pipeline stages also use
`numpy` and `hdbscan` (already in the venv); if a stage complains about a missing
module, `./venv/bin/pip install numpy hdbscan` fixes it.

### 1c. Confirm your environment files exist
You need **two** `.env` files with secrets/URLs:
- `naijapulse-engine/.env` → `SUPABASE_URL` + `SUPABASE_KEY` (the service-role key).
- repo-root `.env` → same Supabase vars **plus** Ollama embedding config:
  `OLLAMA_URL=http://localhost:11434/api/embed`, `OLLAMA_EMBED_MODEL=nomic-embed-text`.

If either is missing, the pipeline will fail immediately with a "Missing
SUPABASE_URL / SUPABASE_KEY" error. Get the values from the project owner.

### 1d. Start Ollama (required for Phase 2 embeddings)
Phase 2 embeds text locally using **Ollama**. In a separate terminal:
```bash
ollama serve            # starts the local Ollama server (if not already running)
ollama pull nomic-embed-text   # one-time: download the embedding model
```
Verify it's up: `curl -s http://localhost:11434/api/tags` should return JSON.
**If Ollama isn't running, Phase 2 will fail** — this is the #1 gotcha.

---

## 2. The pipeline, stage by stage

The recommended path is the one-command runner, which chains everything in the
correct order. From `naijapulse-engine/`:

```bash
./venv/bin/python run_pipeline.py --bias
```
This runs **Phases 1 → 5** in order:
1. Sets up the Supabase schema (`setup_supabase.py`)
2. Ingests articles (`ingest_supabase.py`) — **Phase 1**
3. Embeds them via Ollama (`embed_articles.py`) — **Phase 2**
4. De-duplicates wire copy (`dedup.py`) — **Phase 3**
5. Clusters into stories (`cluster_stories.py`) — **Phase 4**
6. Tags bias + blind spots (`bias_blindspot.py`) — **Phase 5**

### 2a. One more required step before Phase 5 makes sense: seed source bias
`run_pipeline.py` does **not** populate the `source_bias` table. Without it,
Phase 5 has no ownership-lean data to distribute, so every story's
`bias_distribution` ends up meaningless. Run this **once** (after the schema
exists, before or after `--bias`):
```bash
./venv/bin/python seed_source_bias.py
```
It's idempotent (safe to re-run). If you skip it, Phase 5 still runs but the
bias numbers won't reflect real outlet leanings.

### 2b. What each phase does (so you understand the output)

| Phase | Script | What it produces |
|------|--------|------------------|
| 1 | `ingest_supabase.py` | Fetches RSS feeds → `articles` table (title, summary, full_text, image_url). Writes `ingest_report.json`. |
| 2 | `embed_articles.py` | Generates `nomic-embed-text` vectors from `title+summary` → `embeddings` table. |
| 3 | `dedup.py` | Finds near-duplicate wire copy; marks one canonical article, points duplicates at it (`canonical_article_id`). |
| 4 | `cluster_stories.py` / `_recluster.py` | Groups canonical articles into `stories` (same-event clusters); writes `bias_distribution` skeleton. |
| 5 | `bias_blindspot.py` | Fills each story's `bias_distribution`, `bias_coverage_pct`, `is_blindspot`. |

### 2c. Re-running individual phases (common during development)
```bash
./venv/bin/python run_pipeline.py --embed        # Phase 1 + 2
./venv/bin/python run_pipeline.py --dedup        # Phase 1 + 2 + 3
./venv/bin/python run_pipeline.py --cluster      # Phase 1..4
./venv/bin/python run_pipeline.py --bias         # FULL 1..5
./venv/bin/python run_pipeline.py --bias-only    # Phase 5 only (re-tag bias)
./venv/bin/python run_pipeline.py                # Phase 1 only (ingest)
```
Each flag implies the earlier ones, so `--bias` is the full chain. Use the
`--*-only` flags to re-run a single stage without redoing everything.

---

## 3. How to know it ran successfully (verification)

After `--bias` finishes, check these signals:

**A. Ingestion report** (`naijapulse-engine/ingest_report.json`):
```bash
./venv/bin/python -c "import json;d=json.load(open('ingest_report.json'));print('feeds',d['feeds_success'],'/',d['feeds_total']);print('extraction',d['extraction_success_rate'],'%')"
```
Expect `feeds_success/feeds_total` and an extraction rate. (Today 4 feeds still
fail XML parse — that's a known gap, not a crash.)

**B. Live counts via the API (Phase 6)** — start the API (see §4) and:
```bash
curl -s localhost:8000/pipeline-health | python3 -m json.tool
```
Expect roughly: `total_articles` ~500, `total_canonical_articles` ~498,
`total_stories` ~163, `bias_coverage_buckets` with `100%: 163`. If those three
numbers are present and non-zero, the whole pipeline landed in the DB.

**C. A known ground-truth story** (proves clustering + bias both worked):
```bash
curl -s localhost:8000/stories/c990ae21-060e-47eb-b4e7-81fc297cf4fe | python3 -c "import sys,json;d=json.load(sys.stdin);print('members',len(d['members']));print('bias',d['bias_distribution'])"
```
Expect **27 members** and `bias_distribution` ≈
`{"mixed":19,"independent":6,"pro_government":2,"anti_government":0}`.

If B and C look right, you're done with the pipeline. 🎉

---

## 4. Phase 6 — the read-only API (how to run & use it)

### 4a. Start the server
From `naijapulse-engine/`:
```bash
./venv/bin/uvicorn phase6_api:app --port 8000
```
Keep this terminal open. Open a **second** terminal for the commands below.

### 4b. Interactive docs (easiest)
Open **http://localhost:8000/docs** in a browser — every endpoint is clickable
("Try it out" → "Execute").

### 4c. Endpoints (copy-paste)
```bash
curl -s localhost:8000/ | python3 -m json.tool                                  # health
curl -s "localhost:8000/stories?limit=3" | python3 -m json.tool                 # list (newest first)
curl -s "localhost:8000/stories?is_political_topic=true" | python3 -m json.tool # political only
curl -s "localhost:8000/stories?min_articles=20" | python3 -m json.tool         # big stories
curl -s "localhost:8000/stories?sort=article_count&limit=5" | python3 -m json.tool
curl -s "localhost:8000/stories/c990ae21-060e-47eb-b4e7-81fc297cf4fe" | python3 -m json.tool  # one story + articles
curl -s localhost:8000/sources | python3 -m json.tool                          # outlets + lean
curl -s localhost:8000/pipeline-health | python3 -m json.tool                  # diagnostics
```
A fake story ID returns a clean **404** (expected).

### 4d. Stop the server
Press `Ctrl+C` in the server window. The API never writes to the database, so
there's nothing to clean up.

---

## 5. DO / DON'T (the rules that keep you out of trouble)

**✅ DO**
- Run everything from **inside** `naijapulse-engine/`.
- Start **Ollama** and pull `nomic-embed-text` *before* any `--embed`/`--bias` run.
- Run `seed_source_bias.py` **once** before relying on Phase 5 numbers.
- Prefer `run_pipeline.py --bias` over hand-running stages, so ordering is correct.
- Use the `--*-only` flags to re-run a single stage instead of the whole chain.
- Verify with `pipeline-health` + the ground-truth story (§3) after each full run.
- Keep the API server in its own terminal window; run `curl` from another.

**❌ DON'T**
- **Don't** run the API, clustering, or bias scripts without the Supabase `.env`
  present — they'll exit with "Missing SUPABASE_URL / SUPABASE_KEY".
- **Don't** forget Ollama — Phase 2 fails silently-ish (no embeddings → Phase 3/4
  have nothing to work with).
- **Don't** hand-edit rows in Supabase expecting the pipeline to "notice" — most
  stages are idempotent and keyed off `dedup_checked_at` / `cluster_id` NULL
  checks; re-run the relevant `--*-only` stage instead.
- **Don't** expect `full_text` in any API response — it's deliberately excluded
  (legal/biz boundary). Only headlines, summaries, and links are returned.
- **Don't** treat a `0 blindspots flagged` result as a bug — on the current sample
  it's *plausible* (no hard pro/anti-government split surfaced). It means the rule
  didn't fire, not that it's broken.
- **Don't** enable Row-Level Security (RLS) on the tables without also writing
  read policies — RLS is currently **off** on all 6 tables, so the anon/service
  key can read *and* write everything. For local read-only testing that's fine;
  for anything exposed, decide on RLS deliberately (see §6).

---

## 6. Security note (read before exposing this anywhere)

- **RLS is disabled** on `sources`, `articles`, `embeddings`, `clusters`,
  `source_bias`, `stories`. Anyone holding the anon/service key can read **and
  modify** every row. This is acceptable for a local read-only test harness; do
  **not** put it on a public endpoint without enabling RLS + read policies.
- The API itself is read-only by construction (GET-only, no write paths), but the
  underlying database key it uses is a service-role key, so the *database* is not
  protected — keep the server on `localhost`.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Missing SUPABASE_URL / SUPABASE_KEY` | `.env` missing/wrong folder | Check both `.env` files (§1c). |
| Phase 2 produces 0 embeddings / errors | Ollama not running or model missing | `ollama serve` + `ollama pull nomic-embed-text` (§1d). |
| `No module named X` | dep not installed | `./venv/bin/pip install X` (numpy/hdbscan common). |
| `curl: (7) Failed to connect` | API not running / wrong window | Restart §4a, keep window open. |
| Phase 5 bias all zeros / looks wrong | `source_bias` not seeded | Run `seed_source_bias.py` (§2a). |
| `pipeline-health` shows 0 stories | clustering didn't run | Re-run `run_pipeline.py --bias`. |
| Feeds show fewer than 10 successes | 4 feeds (Punch, Vanguard, Guardian NG, The Nation) still fail XML parse — **known gap**, not fatal. | Improve feed fetching (future work). |

---

## 8. Quick "I just want to see it work" path

```bash
cd /Users/Adegoke/Documents/news/naijapulse-engine
ollama serve &            # (in its own terminal) + ollama pull nomic-embed-text
./venv/bin/python seed_source_bias.py
./venv/bin/python run_pipeline.py --bias
./venv/bin/uvicorn phase6_api:app --port 8000
# then open http://localhost:8000/docs
```

---

*Phases 1–5 are the data pipeline; Phase 6 is the read-only API over their
output. See `PHASE6_BUILD.md` for the API spec and `PROGRESS.md` for project
status. The `clusters` table is legacy/dead — real clusters live in `stories`.*
