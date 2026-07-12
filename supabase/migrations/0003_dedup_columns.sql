-- Phase 3 — near-duplicate detection columns
-- Run before dedup.py. Idempotent: guards each ALTER so it is safe to re-run.

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'articles'
          and column_name = 'canonical_article_id'
    ) then
        alter table public.articles
            add column canonical_article_id uuid references public.articles(id);
    end if;

    if not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'articles'
          and column_name = 'dedup_score'
    ) then
        alter table public.articles add column dedup_score float;
    end if;

    if not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'articles'
          and column_name = 'dedup_checked_at'
    ) then
        alter table public.articles add column dedup_checked_at timestamptz;
    end if;
end $$;
