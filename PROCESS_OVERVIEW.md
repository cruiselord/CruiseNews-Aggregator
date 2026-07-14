# NaijaPulse Aggregator – End‑to‑End Process Guide

*Designed for newcomers to software development and product managers who need to explain the system to non‑technical stakeholders.*

---

## Table of Contents
1. [High‑Level Overview](#high-level-overview)
2. [Prerequisites & Setup](#prerequisites--setup)
3. [Pipeline Stages (What the system does)]
   - 3.1 [Phase 1 – Ingestion & Supabase Schema](#phase-1-ingestion--supabase-schema)
   - 3.2 [Phase 2 – Text Embedding](#phase-2-text-embedding)
   - 3.3 [Phase 3 – Near‑Duplicate Detection (Deduplication)](#phase-3-near‑duplicate-detection-deduplication)
   - 3.4 [Phase 4 – Story Clustering](#phase-4-story-clustering)
   - 3.5 [Phase 5 – Bias Tagging & Blind‑Spot Detection](#phase-5-bias-tagging--blind‑spot-detection)
4. [Running the Full Pipeline](#running-the-full-pipeline)
5. [Inspecting the Results](#inspecting-the-results)
6. [How Each Stage Is Implemented (Technical Details)](#how-each-stage-is-implemented-technical-details)
7. [Common Commands & Flags](#common-commands--flags)
8. [Troubleshooting Checklist](#troubleshooting-checklist)
9. [Future Extensions & Where to Add New Features](#future-extensions--where-to-add-new-features)
---

## 1. High‑Level Overview

NaijaPulse is a **local‑first news aggregation platform** that:

1. **Pulls news articles from RSS feeds.**
2. **Stores raw article data in Supabase** (a hosted PostgreSQL + storage service).
3. **Enriches the articles** with vector embeddings so they can be compared for similarity.
4. **Detects duplicate or near‑duplicate articles** so the same story isn’t stored multiple times.
5. **Clusters similar articles together** to form “stories”.
6. **Tags each story for political bias** and flags any “blind‑spots” (biases that are under‑represented).

The whole workflow is orchestrated by a single Python script `run_pipeline.py`.  The script can run the whole pipeline or any individual phase using convenient command‑line flags.

---

## 2. Prerequisites & Setup

| Item | Why We Need It | How to Install |
|------|----------------|----------------|
| **Python 3.11+** | Core language for all scripts. | `brew install python@3.11` (macOS) or download from python.org |
| **virtual environment (venv)** | Isolates dependencies from your global Python. | `python3 -m venv venv && source venv/bin/activate` |
| **pip packages** (see `requirements.txt`) | Provides libraries for HTTP, Supabase client, embedding, etc. | `pip install -r requirements.txt` |
| **Supabase project** | Remote PostgreSQL + storage used for persisting articles. | Create a free project at https://supabase.com, copy URL and service key into a `.env` file (see `.env.example`). |
| **Ollama** (optional but recommended) | Runs a local LLM that provides the `nomic‑embed‑text` model for generating embeddings. | Install from https://ollama.com and pull the model: `ollama pull nomic-embed-text`. |
| **RSS feed URLs** | Sources of raw news articles. | Add them to `naijapulse-engine/ingest.py` or a config file.

**Environment file** – Create a `.env` in the project root (copy `.env.example`).  It should contain:
```
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_ANON_KEY=sbp... (public key)
OLLAMA_HOST=http://127.0.0.1:11434   # if you run Ollama locally
```
---

## 3. Pipeline Stages (What the system does)

### 3.1 Phase 1 – Ingestion & Supabase Schema
*English*: The system makes sure the database tables exist, then fetches articles from each RSS feed, extracts the headline, summary, URL, and publication date, and writes those rows into Supabase.

*Technical*: `setup_supabase.py` uses the Supabase Python client to run `CREATE TABLE IF NOT EXISTS` statements for `articles`, `stories`, `source_bias`, etc. `ingest_supabase.py` iterates over a list of RSS URLs, parses each `<item>`, normalises fields, and inserts rows via the Supabase `insert` endpoint.

### 3.2 Phase 2 – Text Embedding
*English*: After we have the raw text, we turn each article’s title + summary into a vector of numbers (an *embedding*). Those vectors let us measure similarity between articles.

*Technical*: `embed_articles.py` reads all rows where `embedding IS NULL`, calls the local Ollama endpoint (`POST /api/embeddings` with model `nomic-embed-text`), receives a 768‑dimensional float array, and updates the `embedding` column in Supabase.

### 3.3 Phase 3 – Near‑Duplicate Detection (Deduplication)
*English*: Some feeds publish the same story multiple times. This step looks for articles whose embeddings are “very close” (cosine similarity > 0.95) and groups them together, keeping just one canonical article.

*Technical*: `dedup.py` loads all embeddings, builds an Annoy/FAISS index for fast nearest‑neighbor search, then iterates over each article, marking any neighbor within the similarity threshold as a duplicate. Duplicate rows are either soft‑deleted (`is_duplicate = true`) or removed, and the canonical article’s ID is stored on the duplicate for traceability.

### 3.4 Phase 4 – Story Clustering
*English*: We want to know which articles belong to the same *story* (e.g., “election results”). This step clusters similar embeddings together, assigns a `cluster_id` to each article, and creates a higher‑level `stories` record that summarises the cluster.

*Technical*: `cluster_stories.py` uses hierarchical agglomerative clustering (SciPy) on the remaining embeddings. For each cluster it:
1. Picks the most recent article as the *representative*.
2. Computes aggregated bias statistics.
3. Inserts a row into the `stories` table with fields like `representative_title`, `bias_distribution`, and `is_blindspot` (initially false).

### 3.5 Phase 5 – Bias Tagging & Blind‑Spot Detection
*English*: Every story is examined to see if it leans toward a political viewpoint (pro‑government, anti‑government, independent, or mixed). If a story’s bias is under‑represented compared to the overall source mix, it is flagged as a *blind‑spot* – an area where the news ecosystem may be missing perspectives.

*Technical*: `bias_blindspot.py` reads the `source_bias` table (which maps each source to a declared lean), joins it with `stories`, calculates the proportion of each lean per story, then:
- Updates `bias_distribution` JSON column on the story.
- Sets `is_blindspot = true` when the story’s lean count is below a minimum sample threshold **and** the overall platform bias distribution would benefit from more coverage of that lean.

---

## 4. Running the Full Pipeline

From the repository root (with the virtual environment activated):
```
./venv/bin/python naijapulse-engine/run_pipeline.py --all
```
*What happens*:
1. Schema + ingestion → Phase 1
2. Embedding → Phase 2
3. Deduplication → Phase 3
4. Clustering → Phase 4
5. Bias tagging → Phase 5

The script prints a **Pipeline Health Report** at the end, showing success/failure for each phase and timing info.

---

## 5. Inspecting the Results

### Via Supabase Dashboard
1. Open https://app.supabase.com and select your project.
2. Navigate to **Table Editor** → `stories`.
3. You’ll see columns such as:
   - `representative_title`
   - `bias_distribution` (a JSON map of lean → percent)
   - `is_blindspot` (boolean)
4. Click a story to view related `articles` via the foreign‑key `cluster_id`.

### Via API
The FastAPI server (`phase6_api.py`) exposes endpoints:
- `GET /stories` – list all stories
- `GET /articles?story_id=<id>` – articles for a story
- `GET /bias` – overall platform bias statistics

Run the API locally:
```bash
uvicorn naijapulse-engine.phase6_api:app --reload
```
Then query with `curl` or a browser.
---

## 6. How Each Stage Is Implemented (Technical Details)

| Stage | Main Python Module | Key Functions / Classes | External Services / Libraries |
|-------|-------------------|--------------------------|-------------------------------|
| Schema + Ingestion | `setup_supabase.py`, `ingest_supabase.py` | `create_tables()`, `fetch_rss()`, `parse_item()`, `upsert_article()` | Supabase REST API (`supabase-py` client), `feedparser` for RSS |
| Embedding | `embed_articles.py` | `get_unembedded_articles()`, `call_ollama()`, `store_embedding()` | Ollama local server (`httpx` request), NumPy for vectors |
| Deduplication | `dedup.py` | `build_index()`, `find_duplicates()`, `mark_duplicate()` | `annoy` (approximate nearest neighbor), cosine similarity helper |
| Clustering | `cluster_stories.py` | `cluster_embeddings()`, `create_story_record()`, `assign_cluster_id()` | SciPy `linkage` + `fcluster`, PostgreSQL JSON column ops |
| Bias & Blind‑Spot | `bias_blindspot.py` | `load_source_bias()`, `compute_story_bias()`, `flag_blindspot()` | Supabase for source bias table, Python `collections.Counter` |

**Error handling** – Each stage returns a Unix exit code; the runner `run_pipeline.py` aborts the pipeline on a non‑zero code and prints a helpful message.

---

## 7. Common Commands & Flags

| Command | Meaning |
|---------|----------|
| `python run_pipeline.py` | Run **only Phase 1** (schema + ingestion). |
| `python run_pipeline.py --embed` | Run Phase 1 **plus** Phase 2 (embedding). |
| `python run_pipeline.py --dedup` | Run Phases 1‑3 (ingest, embed, dedup). |
| `python run_pipeline.py --cluster` | Run Phases 1‑4. |
| `python run_pipeline.py --bias` | Run the **full** pipeline (Phases 1‑5). |
| `python run_pipeline.py --all` | Alias for `--bias`. |
| `python run_pipeline.py --dedup-only` | Re‑run **only** the dedup step on existing data. |
| `python run_pipeline.py --cluster-only` | Re‑run only clustering (requires prior phases already done). |
| `python run_pipeline.py --bias-only` | Re‑run only bias tagging (requires prior phases already done). |
---

## 8. Troubleshooting Checklist

1. **Supabase connection errors** – Verify `SUPABASE_URL` and `SUPABASE_ANON_KEY` are correct in `.env`.  Test with a simple `curl $SUPABASE_URL/rest/v1/articles`.
2. **Embedding fails** – Ensure Ollama is running (`ollama serve`) and the model `nomic-embed-text` is pulled.  Check network access on `http://127.0.0.1:11434`.
3. **Zero rows processed** – Confirm RSS URLs are reachable; look at `pipeline_run*.log` for HTTP 404s.
4. **High duplicate rate** – Adjust the cosine‑similarity threshold in `dedup.py` (default 0.95) if you think too many distinct articles are being marked duplicate.
5. **Bias flags seem odd** – Review the `source_bias` table; the lean values must be one of `pro_government`, `anti_government`, `independent`, or `mixed`.
---

## 9. Future Extensions & Where to Add New Features

| Desired Feature | Where to Extend |
|----------------|-----------------|
| **Additional source types** (Twitter, PDFs) | Add a new ingestion module (e.g., `ingest_twitter.py`) and call it from `run_pipeline.py` under a new flag `--twitter`. |
| **Live streaming updates** | Implement a background worker that watches RSS feeds and pushes new rows to Supabase; use Supabase Realtime or WebSockets. |
| **More sophisticated bias models** | Replace the simple lookup in `bias_blindspot.py` with a classifier model (e.g., a small fine‑tuned transformer) and store predictions in a new `bias_score` column. |
| **Front‑end UI** | Build a React dashboard that consumes the FastAPI endpoints (`/stories`, `/articles`). |
| **Scheduled nightly runs** | Deploy the pipeline to a serverless platform (e.g., Supabase Edge Functions or GitHub Actions) and schedule with a cron expression.

---

*This guide is intended to be a single source of truth for anyone new to the codebase, whether they are engineers writing the next feature or product managers explaining the product’s value proposition.*
