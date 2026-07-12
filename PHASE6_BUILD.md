# Phase 6 — Read-only Query/API layer (FastAPI)

**Status: NOT BUILT.** `PROGRESS.md` falsely claims Phase 6 is done
("curl-verified 2026-07-12"). That was never true — there is **no
`phase6_api.py`**, and `fastapi`/`uvicorn` are **not installed**. The
first task in a new session is to correct that `PROGRESS.md` claim, then build
this phase per the plan below.

This file is a self-contained handoff. A fresh session can read it and
execute end-to-end without re-deriving context. Live DB numbers below were
pulled 2026-07-12 and are reproduced here so you don't have to re-query.

---

## 0. Decision locked (user picked option A)

Acceptance test #2's "known-correct" numbers were **stale** — the Oyo
story was re-clustered after hand-verification. **Update the expected values to
live truth**, and assert the API reproduces *current* data:

- Story `c990ae21-060e-47eb-b4e7-81fc297cf4fe` → **27 articles**,
  `bias_distribution = {"mixed":19,"independent":6,"pro_government":2,"anti_government":0}`
  (NOT the old claim of 10 / `{"mixed":5,"independent":5}`).
- representative_title (live):
  `"After Oyo Schoolchildren, Teachers Regain Freedom, ADC Demands Rescue of Borno, Kwara Kidnap Victims"`

---

## 1. Live Supabase schema (inspected 2026-07-12, `public`)

| Table | Rows | Columns |
|---|---|---|
| `sources` | 12 | `id, name, rss_url, homepage_url, country, active, created_at` |
| `articles` | 500 | `id, source_id, url, title, summary, full_text, published_at, fetched_at, content_hash, cluster_id, image_url, canonical_article_id, dedup_score, dedup_checked_at` |
| `embeddings` | 500 | `id, article_id, model, vector, created_at` |
| `clusters` | **0 (empty/legacy)** | `id, label, size, created_at` — **IGNORE; real clusters are in `stories`** |
| `source_bias` | 11 | `source_id, ownership_lean, regional_base`⚠️(typo'd, not `regional_lean`), `confidence, notes, updated_at, owner, ownership_type, political_alignment, source_urls` |
| `stories` | 163 | `id, representative_title, first_seen_at, last_updated_at, article_count, bias_distribution(jsonb), is_blindspot, centroid_embedding(vector), status, bias_coverage_pct, blindspot_checked_at` |

**Key facts for the build:**
- Real cluster table = **`stories`** (163 rows). `clusters` is dead.
- `is_political_topic` is **NOT a stored column** — it's computed at
  runtime in `bias_blindspot.py` (keyword scan). The API must compute it.
- `source_bias.regional_base` is misspelled in the DB (typo). The
  `/sources` join only needs `ownership_lean, confidence, notes` (all present).
- RSS feed status is **not in the DB** — it lives in
  `naijapulse-engine/ingest_report.json` (see §6).

---

## 2. Live reference numbers (for acceptance, no re-query needed)

- Total articles = **500**, canonical (canonical_article_id IS NULL) = **498**,
  2 are duplicates.
- Stories = **163**. Blindspots flagged = **0**.
- bias_coverage_pct buckets: 100% = **163**, 1–99% = **0**, 0% = **0**.
- Duplicate-collapse candidate (acceptance #4): story
  `31b7b9ea-9d4d-4b59-ae74-dd06320ce65e`
  `"NAN: FG commissions new rice mill in Kano"` → canonical_n=**2**, dup_n=**2**.
- RSS (from `ingest_report.json`): feeds_total=10, feeds_success=10,
  feeds_failed=0, feeds_gnews_fallback=4, feed_success_rate=100.0,
  extraction_success=203, extraction_success_rate=67.89.

---

## 3. Objective

Build a **read-only FastAPI** service over the existing Supabase data so the
whole pipeline (phases 1–5) can be validated end-to-end via HTTP — no UI,
no Next.js route, no second runtime. Reuse the existing `venv` + `supabase-py`
client + `.env` (`SUPABASE_URL`, `SUPABASE_KEY`).

---

## 4. Endpoints

### `GET /stories`
- Pagination: `offset` / `limit`, **default limit=20**. Offset/limit only —
  do NOT build cursor pagination (scale problem we don't have, ~163 stories).
- Filters (all optional, combinable):
  - `is_blindspot=true` → stored bool, push to SQL `WHERE is_blindspot`.
  - `min_articles=N` → stored int, push to SQL `WHERE article_count >= N`.
  - `is_political_topic=true` → **computed in API** (see §5). Fetch all
    stories' representative_title + member headlines, keyword-scan, filter in Python.
- Sort: default `last_updated_at desc`; `sort=article_count` supported.
- Per-story fields (list view): `id, representative_title, article_count,
  bias_distribution, is_blindspot, is_political_topic(computed),
  bias_coverage_pct, first_seen_at, last_updated_at`.
- **Do NOT include member articles in the list view** — that's the detail endpoint.

### `GET /stories/{id}`
- Everything from the list view for this one story, **plus** its full member list.
- **Collapse duplicates:** only canonical articles
  (`canonical_article_id IS NULL`) appear. Each canonical article carries
  `also_reported_by` = count of duplicates whose `canonical_article_id`
  points to it (Ground-News "N outlets" pattern).
- **404** if the id doesn't exist — never a 200 with empty/null data.

### `GET /sources`
- Every source, joined with its `source_bias` row
  (`ownership_lean, confidence, notes`). Sources with **no** `source_bias`
  row still appear, with those fields `null` (don't silently drop gaps).
- Plus a live count of canonical articles contributed per source.

### `GET /pipeline-health`  (diagnostic, not a product feature)
- `total_articles` (500), `total_canonical_articles` (498),
  `total_stories` (163 — from `stories`, not the empty `clusters`).
- Per-source article counts.
- RSS feed status — **read from `naijapulse-engine/ingest_report.json`**
  (not the DB): `feeds_total, feeds_success, feeds_failed,
  feeds_gnews_fallback, feed_success_rate, extraction_success,
  extraction_success_rate`.
- Min-sample-gate counts: stories where
  `sum(bias_distribution values) < 3` (the `tagged >= 3` gate from
  `bias_blindspot.py` `MIN_SAMPLE_TAGGED`).
- bias_coverage_pct distribution bucketed: `100%`, `50–99%`, `1–49%`, `0%`.
- Count of stories excluded by the topic gate (computed
  `is_political_topic = false`).
- Count flagged (`is_blindspot = true`) **vs** eligible for blindspot
  (political AND tagged >= 3).
- This replaces the one-off manual acceptance reports — make it trustworthy.

---

## 5. `is_political_topic` computation (must be done in the API)

Not stored. Reuse the keyword set + logic from `bias_blindspot.py`
(`POLITICAL_KEYWORDS` tuple + `_is_political_topic()`). Compute per story
from `representative_title` + member headlines (one bulk join of canonical
articles' `title` + `cluster_id`). Used for: the list field, the
`is_political_topic=true` filter, and the pipeline-health "excluded by topic
gate" count.

`tagged` (for min-sample gate / eligible count) = `sum(bias_distribution.values())`,
since `bias_distribution` enumerates every lean (including zeros) and each
canonical-with-bias contributes to exactly one lean.

---

## 6. Response contract (HARD — enforce everywhere, not just convenient spots)

- **`full_text` must NEVER appear in any response body, any endpoint, any field
  name.** This is the legal/biz boundary (Ground News shows snippet +
  link-out, not hosted full text). Treat as a hard rule.
- Article fields returned: `title, summary, url, image_url, source_name,
  published_at, also_reported_by` (canonical only).
- **Never** return internal-only fields: embedding vectors, `dedup_score`,
  `content_hash`, `fetched_at`, `centroid_embedding`.
- `source_name` comes from joining `articles.source_id → sources.name`.

---

## 7. Build steps

1. (If needed) Correct `PROGRESS.md`: Phase 6 row in the "Upcoming Phases"
   table and the "Current Phase" line currently claim Phase 6 is DONE — change
   to **pending / not built**. Remove/rewrite the fictional "## Phase 6 –
   Query/API layer (FastAPI)" section that claims `phase6_api.py` exists and was
   curl-verified. Add an Action Log entry noting the file was never built.
2. Create `naijapulse-engine/phase6_api.py` — FastAPI app, GET endpoints only.
3. Reuse `supabase-py` client: `from supabase import create_client`,
   `create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])`,
   `load_dotenv()` from `naijapulse-engine/.env`. (Match the pattern in
   `cluster_stories.py` / `bias_blindspot.py`.)
4. Install deps into the existing venv and record them:
   `./venv/bin/pip install fastapi uvicorn` and add `fastapi` + `uvicorn`
   to `requirements.txt` (currently only: supabase, python-dotenv, feedparser,
   trafilatura).
5. Run: `cd naijapulse-engine && ./venv/bin/uvicorn phase6_api:app --port 8000`.
6. No auth, no write endpoints, no Next.js. Read-only by design.

---

## 8. Acceptance tests (run against LIVE data, show actual output)

1. **Schema confirmation** — paste the §1 table (done; it's in this file).
2. **Ground-truth** — `GET /stories/c990ae21-060e-47eb-b4e7-81fc297cf4fe`
   returns exactly **27** member articles and
   `bias_distribution = {"mixed":19,"independent":6,"pro_government":2,"anti_government":0}`.
3. **Full-text leak** — pipe the **raw JSON** of *every* endpoint through
   `grep full_text` and confirm it's genuinely absent everywhere.
4. **Duplicate collapse** — `GET /stories/31b7b9ea-9d4d-4b59-ae74-dd06320ce65e`
   (the rice-mill story, canonical_n=2, dup_n=2): the duplicate does
   NOT appear as a separate entry, and the canonical article's
   `also_reported_by = 2`.
5. **Filter/sort** — `GET /stories?is_blindspot=true` returns only
   `is_blindspot=true` rows (today that's an empty list — 0 flagged; confirm
   the empty result is correct, not a bug). Same check for
   `is_political_topic=true` and `min_articles=N`.
6. **Pipeline-health sanity** — `GET /pipeline-health` numbers match a
   direct `SELECT` of the tables (articles=500, canonical=498,
   stories=163, coverage 100%=163, etc.).
7. **404** — `GET /stories/<fake-uuid>` returns a proper 404, not 200.

---

## 9. Security note (surface to user, do NOT auto-fix)

Supabase MCP advisory (critical): **RLS is disabled on all 6 tables**
(sources, articles, embeddings, clusters, source_bias, stories). Anyone with
the anon key can read/modify every row. Because this is a read-only local
testing API it's low real risk, but do **not** auto-enable RLS (doing so
without policies blocks all access). Present this SQL to the user and let them
decide:
```sql
ALTER TABLE public.sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clusters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.source_bias ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.stories ENABLE ROW LEVEL SECURITY;
```

---

## 10. Out of scope (stop if tempted)

Next.js frontend, auth, write endpoints, cursor pagination. This phase is
read-only validation tooling only.
