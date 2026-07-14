-- Phase: performance — push per-source article counting into SQL
-- (Finding 9). The /sources and /pipeline-health endpoints previously pulled
-- the ENTIRE articles table over the network and aggregated in Python on every
-- request. These RPCs return one row per source instead of one row per article,
-- so they stay fast as the dataset grows.
--
-- Idempotent: uses create or replace. Safe to re-run.

-- Canonical-only counts (used by /sources, which only reports canonical articles
-- per source).
create or replace function public.canonical_counts_by_source()
returns table(source_id uuid, canonical_count bigint) as $$
    select source_id, count(*) as canonical_count
    from public.articles
    where canonical_article_id is null
    group by source_id;
$$ language sql stable;

-- Total + canonical counts per source (used by /pipeline-health, which reports
-- both total_articles and canonical_articles per source). Replaces the full
-- articles-table scan that previously fed per_source_total / per_source_canonical.
create or replace function public.article_counts_by_source()
returns table(source_id uuid, total_count bigint, canonical_count bigint) as $$
    select source_id,
           count(*) as total_count,
           count(*) filter (where canonical_article_id is null) as canonical_count
    from public.articles
    group by source_id;
$$ language sql stable;
