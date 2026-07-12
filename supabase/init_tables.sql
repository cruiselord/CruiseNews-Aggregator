-- ============================================================================
-- NaijaPulse Engine — Supabase schema
-- Idempotent: safe to run multiple times.
-- Apply with: psql "$SUPABASE_DB_URL" -f supabase/init_tables.sql
--            (or paste into Supabase → SQL Editor and Run)
-- ============================================================================

create extension if not exists "uuid-ossp";
create extension if not exists vector;

-- ---------------------------------------------------------------------------
-- sources: the RSS outlets we poll
-- ---------------------------------------------------------------------------
create table if not exists public.sources (
    id           uuid primary key default uuid_generate_v4(),
    name         text not null,
    rss_url      text not null unique,
    homepage_url text,
    country      text default 'NG',
    active       boolean default true,
    created_at   timestamptz default now()
);

-- ---------------------------------------------------------------------------
-- articles: raw ingested articles (Phase 1)
-- ---------------------------------------------------------------------------
create table if not exists public.articles (
    id           uuid primary key default uuid_generate_v4(),
    source_id    uuid references public.sources(id) on delete set null,
    url          text not null unique,
    title        text not null,
    summary      text,
    full_text    text,
    image_url    text,
    published_at timestamptz,
    fetched_at   timestamptz default now(),
    content_hash text,
    cluster_id   uuid
);

create index if not exists idx_articles_source_id on public.articles(source_id);
create index if not exists idx_articles_published_at on public.articles(published_at desc);
create index if not exists idx_articles_content_hash on public.articles(content_hash);

-- ---------------------------------------------------------------------------
-- embeddings: Phase 2 (Ollama embeddings, pgvector)
-- ---------------------------------------------------------------------------
create table if not exists public.embeddings (
    id          uuid primary key default uuid_generate_v4(),
    article_id  uuid references public.articles(id) on delete cascade,
    model       text not null,
    vector      vector(768),
    created_at  timestamptz default now()
);

-- One embedding per (article, model). Enables idempotent upserts (Phase 2).
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'embeddings_article_model_key'
    ) then
        alter table public.embeddings
            add constraint embeddings_article_model_key unique (article_id, model);
    end if;
end $$;

create index if not exists idx_embeddings_vector
    on public.embeddings using ivfflat (vector vector_cosine_ops) with (lists = 100);

-- ---------------------------------------------------------------------------
-- clusters: Phase 4 (HDBSCAN story clusters)
-- ---------------------------------------------------------------------------
create table if not exists public.clusters (
    id          uuid primary key default uuid_generate_v4(),
    label       text,
    size        integer default 0,
    created_at  timestamptz default now()
);

-- Enable RLS off for the service role ingestion path (server-to-server).
-- If you later add a public/anon client, enable RLS and add policies here.
alter table public.sources   disable row level security;
alter table public.articles  disable row level security;
alter table public.embeddings disable row level security;
alter table public.clusters  disable row level security;
