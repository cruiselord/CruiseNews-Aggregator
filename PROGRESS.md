# NaijaPulse Engine – Progress Tracker

*This file is intended to be updated regularly as we move through the build phases.*

---

## Current Phase
**Phase 1 – Ingestion (CONNECTED TO SUPABASE ✅)**

- ✅ **Spec written** (see `naijapulse-core-engine-spec.md`).
- ✅ **Ingestion script** (`ingest_supabase.py`) exists and runs against **Supabase**, not SQLite.
- ✅ **Supabase schema created** – `supabase/init_tables.sql` pasted once into the Supabase SQL editor
      (tables: `sources`, `articles`, `embeddings`, `clusters`; `articles` has `image_url`).
- ✅ **Sources seeded** – all 10 Nigerian outlets inserted into `sources`.
- ✅ **Articles ingested** – **124 articles** loaded into the `articles` table.
- ✅ **`full_text` fixed** – root cause was `trafilatura.fetch_url(url, timeout=...)` raising `TypeError`
      (this trafilatura build has no `timeout` kwarg). Extractor now calls `fetch_url(url)` correctly.
- ✅ **`image_url` added** – `extract_image_url()` scrapes the article page for `og:image`
      (falls back to first `<img>`); stored per article.
- ✅ **Backfill complete** – `backfill_articles.py` re‑extracted full text + image for every existing row.
      **`full_text` populated: 124/124 (100%)** · **`image_url` populated: 124/124 (100%)**.
- 🔲 **Feed success below target** – 6/10 feeds parse; 4 fail XML parse
      (Punch, Vanguard, Guardian NG, The Nation). Currently **60 %** vs ≥ 90 % target.

---

## Acceptance Status (last full run)
| Metric | Result | Target | Status |
|--------|--------|--------|--------|
| Feeds successful | 6 / 10 (60 %) | ≥ 90 % | ❌ |
| Articles ingested | 124 | – | ✅ |
| `full_text` populated | 124 / 124 (100 %) | – | ✅ |
| `image_url` populated | 124 / 124 (100 %) | – | ✅ |
| Full‑text extraction (real body) | working (post‑fix) | ≥ 70 % | ✅ |

> Note: extraction was silently failing before the `fetch_url` fix; after the fix + backfill,
> 100 % of stored rows carry the genuine article body.

---

## Upcoming Phases
| Phase | Description | Acceptance Target | Status |
|------|-------------|-------------------|--------|
| 2 | Embedding (Ollama) | 100 articles < 2 min, similarity thresholds | ⏳ Pending |
| 3 | Near‑duplicate detection (MinHash LSH) | Detect known wire‑copy dupes | ⏳ Pending |
| 4 | Clustering (HDBSCAN) | ≥ 80 % cluster purity on hand‑labeled set | ⏳ Pending |
| 5 | Bias tagging & blind‑spot detection | Manual verification of 5 blind‑spots | ⏳ Pending |
| 6 | Query/API layer (FastAPI) | `curl` returns correct stories | ⏳ Pending |

---

## Known Gaps / Next Steps
1. **Fix the 4 failing feeds** (Punch, Vanguard, Guardian NG, The Nation) – XML parse errors
   (`not well-formed (invalid token)` / `undefined entity`). Likely needs custom request headers
   or a more lenient parser; raising feed success to ≥ 90 %.
2. **Re‑run acceptance test** after feed fix to confirm ≥ 90 % feed success.
3. **Phase 2:** build the embedding job off `articles.full_text` (already populated).
4. **API:** when exposing articles, return only `title`, `summary`, `url`, `image_url`, `source`
   (never `full_text`) to stay in the legal/biz safe‑zone (Ground News model).

---

## Supabase MCP Integration
- ✅ Added MCP server configuration (`.mcp.json`, HTTP transport, project `wwxsylkcqmhoeesloalp`).
- 🔲 **Approve** the server once in an interactive `claude` session
      (`claude mcp list` shows it as *Pending approval*).
- 🔲 The MCP server is **read‑only** by design – it is a helper for inspecting the project,
      **not** the data‑loading path. Ingestion uses the `supabase-py` client + `SUPABASE_KEY` in `.env`.
- 🔲 (Optional) Install Supabase agent skills: `npx skills add supabase/agent-skills`.

---

## Files of Interest
| File | Purpose |
|------|---------|
| `naijapulse-engine/ingest_supabase.py` | Main ingestion pipeline (Supabase client) |
| `naijapulse-engine/backfill_articles.py` | Re‑extract `full_text` + `image_url` for existing rows |
| `naijapulse-engine/setup_supabase.py` | Schema bootstrap helper |
| `naijapulse-engine/run_pipeline.py` | One‑command: setup → ingest |
| `supabase/init_tables.sql` | Supabase schema (sources/articles/embeddings/clusters) |
| `.env` (repo root) | `SUPABASE_URL` + `SUPABASE_KEY` (git‑ignored) |

---

## Action Log
- **2026‑07‑12** – Added Supabase MCP server via `claude mcp add …`.
- **2026‑07‑12** – Generated this progress tracker.
- **2026‑07‑12** – Created `.env` with Supabase URL + key; ran ingestion; tables existed via manual SQL paste.
- **2026‑07‑12** – Fixed `trafilatura.fetch_url()` call (removed unsupported `timeout` kwarg).
- **2026‑07‑12** – Added `image_url` extraction (`og:image` via requests/BeautifulSoup).
- **2026‑07‑12** – Wrote + ran `backfill_articles.py`; **124/124 articles now have `full_text` + `image_url`**.
- **2026‑07‑12** – Updated `PROGRESS.md` to reflect Phase 1 Supabase connection + 100 % field population.

---

*Keep this file committed to the repo so the team can see real‑time status.*
