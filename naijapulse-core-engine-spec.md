# NaijaPulse Core Engine — Build Spec (Ground News-style aggregation, no UI)

**Owner:** Adegoke
**Purpose:** Feed this document to Claude Code as the working spec for building the *core aggregation engine* — ingestion, clustering, bias tagging, blindspot detection. No UI, no auth. The engine must be testable end-to-end via CLI/API calls before any frontend work resumes.

**Continues from:** NaijaPulse MVP (Next.js 14 + Supabase + Ollama, Ground News-style layout). This spec assumes the UI shell already exists and this work plugs into the same Supabase project.

---

## 1. Non-goals (explicitly out of scope)

- No UI/frontend work
- No user auth, personalization, or "My News Bias" tracking
- No push notifications / email digests (stub the hook, don't build the sender)
- No ownership/funding database (Ground News "Vantage" tier — separate future project)
- No monetization logic

If Claude Code starts generating React components or auth flows, stop it — that's scope creep on this pass.

---

## 2. Architecture

```
[RSS Feeds] --poll--> [Ingestion] --extract--> [Article Store]
                                                      |
                                                      v
                                            [Embedding (Ollama)]
                                                      |
                                                      v
                                     [Dedup: MinHash LSH near-dupes]
                                                      |
                                                      v
                                     [Clustering: HDBSCAN on embeddings]
                                                      |
                                                      v
                                          [Story = 1 cluster of articles]
                                                      |
                          +---------------------------+---------------------------+
                          v                                                       v
                 [Bias tagging per article's source]                  [Blindspot detection]
                 (join against source_bias table)                      (bias distribution
                                                                          formula on story)
                          |                                                       |
                          +---------------------------+---------------------------+
                                                      v
                                            [Story API / query layer]
                                       (no UI consumes this yet — test via curl/scripts)
```

---

## 3. Stack (free/local only)

| Stage | Tool | Notes |
|---|---|---|
| Feed polling | `feedparser` (Python) | Poll each source's `/feed` on a cron, every 15–30 min |
| Full-text extraction | `trafilatura` | Fallback to RSS summary if extraction fails or is blocked |
| Embeddings | Ollama `nomic-embed-text` | Already running locally; 768-dim vectors |
| Near-dup detection | `datasketch` (MinHash LSH) | Catch wire-copy republishing before clustering |
| Clustering | `hdbscan` + `umap-learn` (optional, for debug viz only) | Density-based, no fixed cluster count needed |
| Storage | Supabase Postgres + `pgvector` | Already in your stack; pgvector is free on all tiers |
| Scheduler | macOS `launchd` or plain `cron` | Runs the pipeline locally — avoids paying for a hosted worker |
| Local LLM assist | Ollama (your existing qwen3.6:27b-mlx or similar) | For cluster labeling/summarization only — not for clustering itself |

Do **not** reach for a paid news API, a hosted vector DB, or a hosted job queue for this phase. Everything above runs free on your MacBook + Supabase free tier.

---

## 4. Data model (Postgres DDL sketch — adapt to existing NaijaPulse schema if tables already exist)

```sql
-- Sources: the outlets you poll
create table sources (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  rss_url text not null,
  homepage_url text,
  country text default 'NG',
  active boolean default true,
  created_at timestamptz default now()
);

-- Nigerian bias seed table — YOU populate this manually, see Section 6
create table source_bias (
  source_id uuid references sources(id) primary key,
  ownership_lean text,       -- e.g. 'pro-establishment', 'independent', 'opposition-aligned'
  regional_lean text,        -- e.g. 'south-west', 'north', 'national'
  confidence text,           -- 'seed-guess' | 'reviewed' | 'confirmed'
  notes text,
  updated_at timestamptz default now()
);

-- Raw articles as ingested
create table articles (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references sources(id),
  url text unique not null,
  title text not null,
  summary text,              -- RSS-provided summary, always safe to store
  full_text text,             -- only if extraction succeeds; consider NOT storing full body
                               -- long-term (ToS/storage), store a hash + short excerpt instead
  published_at timestamptz,
  fetched_at timestamptz default now(),
  embedding vector(768),      -- pgvector column, matches nomic-embed-text dims
  content_hash text,           -- for MinHash/near-dup pre-check
  cluster_id uuid
);

create index on articles using ivfflat (embedding vector_cosine_ops);

-- Stories = clusters of articles about the same event
create table stories (
  id uuid primary key default gen_random_uuid(),
  representative_title text,
  first_seen_at timestamptz,
  last_updated_at timestamptz,
  article_count int default 0,
  bias_distribution jsonb,     -- {"pro-establishment": 3, "independent": 2, "opposition-aligned": 0}
  is_blindspot boolean default false
);

alter table articles add constraint fk_cluster foreign key (cluster_id) references stories(id);
```

---

## 5. Build phases (each phase must pass its acceptance test before moving on)

### Phase 1 — Ingestion
**Build:** Script that reads `sources` table, polls each RSS feed, inserts new articles (dedupe on `url` unique constraint), stores summary always, attempts `trafilatura` extraction for full text.
**Acceptance test:** Run against 10 real Nigerian RSS feeds (Punch, Vanguard, Premium Times, Daily Post, ThisDay, Tribune, Daily Trust, Guardian NG, The Nation, BusinessDay). Confirm ≥90% of feeds return valid entries and ≥70% of articles get successful full-text extraction. Log failures with reason (blocked, timeout, parse error) — don't silently drop them.

### Phase 2 — Embedding
**Build:** Background job that finds articles with `embedding IS NULL`, calls Ollama `nomic-embed-text` on `title + summary` (not full text — keeps it fast and consistent), writes the vector back.
**Acceptance test:** Batch of 100 articles embeds in under 2 minutes on your Mac. Spot check: pull 5 articles you know are about the same real event, confirm cosine similarity between their embeddings is meaningfully higher (>0.7) than between random pairs (<0.4).

### Phase 3 — Dedup (MinHash LSH)
**Build:** Before clustering, run near-duplicate detection to catch wire-service republishing (NAN copy running verbatim across 5 outlets) — collapse these to one canonical article per story, but keep all as "also reported by" references.
**Acceptance test:** Feed it 3 known verbatim NAN wire stories from different outlets. Confirm they're flagged as near-duplicates (>0.85 Jaccard threshold) and don't inflate the "5 independent sources" count in the blindspot formula later.

### Phase 4 — Clustering
**Build:** HDBSCAN over embeddings (min_cluster_size=2, since a "story" can be as few as 2 outlets in your smaller Nigerian source set — Ground News uses higher thresholds because they have 50,000 sources, you don't). Assign `cluster_id`, create/update `stories` row.
**Acceptance test:** Build a small hand-labeled eval set — 30 articles you've manually grouped into ~10 real stories from a single news day. Run the pipeline, measure cluster purity (do the articles that should be together end up together?) and don't accept the phase until purity is above 80%. Log false clusters (mixed stories) separately from false splits (same story, split into two clusters) — they need different fixes.

### Phase 5 — Bias tagging + Blindspot detection
**Build:** Join each article's `source_id` against `source_bias`. Compute `bias_distribution` on the `stories` row. Flag `is_blindspot = true` when coverage skews heavily to one lean category with near-zero representation from another — start with a simple rule (e.g., ≥70% one category, 0% another) and tune from there. Do not over-engineer Ground News's exact undisclosed formula — you don't know it, so build your own defensible rule and document it.
**Acceptance test:** Manually verify 5 flagged blindspots against your own read of the day's news. Do they make sense? If not, adjust the threshold, not the underlying clustering.

### Phase 6 — Query/API layer (for testing only, not UI)
**Build:** A thin FastAPI or Next.js API route exposing `GET /stories` (paginated, with bias_distribution) and `GET /stories/:id/articles`. This is how you'll validate the whole pipeline before touching UI again.
**Acceptance test:** `curl` it. Confirm a real story from today shows up with the right articles grouped and a sane bias distribution.

---

## 6. Nigerian bias taxonomy — starter framework (you seed this, no tool can)

Do not try to map Nigerian outlets onto US left/right. Use dimensions that actually apply:

- **Ownership/patronage lean**: many Nigerian papers are owned by or closely tied to political figures or business interests with known alignments. Start by researching each outlet's ownership (publicly available for the major ones) and tag as `pro-establishment`, `independent`, or `opposition-aligned`.
- **Regional/ethnic lean**: Nigerian media coverage often skews by where the paper is based and its historical readership (Lagos/South-West outlets vs. Abuja/North-focused vs. genuinely national). Tag `south-west`, `north`, `south-east`, `national`.
- **Confidence flag**: mark every row `seed-guess` until you've actually verified it. Don't let a guessed label silently become gospel in the product.

Start with your 10 initial sources, do the ownership research once, store it in `source_bias`, and revisit quarterly. This table is the single most important asset in the whole system — everything downstream depends on its accuracy, and it's the one piece Claude Code cannot generate for you from general knowledge.

---

## 7. Testing philosophy (per your standing preference: test everything, trust data not vibes)

- Every phase above has a numeric acceptance threshold — don't let Claude Code mark a phase "done" without running the test and showing you the number.
- Keep the 30-article hand-labeled eval set from Phase 4 permanently — rerun it after any change to embedding model, clustering params, or dedup threshold, so you can see if a "improvement" actually regressed something.
- Log everything that fails silently in Ground News's real system but shouldn't in yours: failed extractions, empty clusters, sources that stop publishing.

---

## 8. What to hand Claude Code, verbatim

> Build the pipeline in `/naijapulse-engine` following the phases in this spec, in order. Do not proceed to the next phase until the acceptance test for the current phase passes and you've shown me the actual output/numbers. Use feedparser + trafilatura for ingestion, Ollama nomic-embed-text for embeddings, hdbscan for clustering, datasketch for MinHash dedup, and write to the existing Supabase project using the schema in Section 4. Do not build any UI, auth, or notification sending — stub those. Start with Phase 1 only, against the 10 sources listed in Section 5, and show me real output before we move on.

---

## 9. Known limitations of this build (own these, don't let them surprise you later)

- Bias tagging is only as good as `source_bias`, and that table starts as guesses
- Clustering will misgroup fast-breaking or ambiguous stories sometimes — the eval set exists to catch this, not eliminate it
- No human review loop, unlike Ground News's actual production system
- Storing full article text long-term is a legal grey area at scale — plan to store summary + short excerpt + link-out once you're past local testing
- 10 sources means smaller, sparser story clusters than Ground News's 50,000-source graph — a "blindspot" with only 3-4 outlets total is statistically noisier than one built from 50
