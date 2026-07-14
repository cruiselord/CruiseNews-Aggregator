# NaijaPulse / CruiseNews-Aggregator — Senior Backend Engineer Code Audit

**Repo:** https://github.com/cruiselord/CruiseNews-Aggregator
**Commit audited:** `79b59ae` (2026-07-12)
**Audited by:** reading the actual source files, not summaries or prior progress reports.
**Audience:** Claude Code — this document is written so you can act on each finding
without asking clarifying questions. Every finding cites the real file and the real
code as it exists in the repo right now.

**How to use this document:** Work through findings in the order given (Critical →
High → Medium). Each finding is self-contained: problem, current code, why it's a
problem, the fix, why the fix is better, and exact instructions. Do not skip the
"Why this matters" sections — they explain the reasoning so future changes don't
regress the fix. Report back actual output (test results, before/after numbers) after
each fix, the same way every phase of this project has been validated so far — don't
mark anything done without showing real evidence.

---

## Executive summary

This is a real system, not a toy. The phased pipeline (ingest → embed → dedup →
cluster → bias-tag → serve) is architecturally sound, the acceptance-test discipline
across every phase has been genuinely good engineering practice, and several pieces of
logic — the near-duplicate lean-collision detector in Stage A of bias tagging, the
directional-only blindspot comparison, the topic-relevance gate — are well-reasoned
and correctly implemented. That's the real foundation, and it's worth keeping.

But there are two findings below that are not style preferences — they are things
that would fail a real code review at any of the four companies you named, on sight:

1. **A public GitHub repository is currently serving full scraped article bodies**
   from a committed SQLite database, which directly violates the project's own
   "never store/republish full_text" rule that's been enforced everywhere else in
   this codebase.
2. **Row Level Security is explicitly disabled on every table**, and the service-role
   key (full read/write privileges, bypasses RLS entirely) is loaded directly inside
   the process meant to be exposed as a public-facing API.

Finding 1 is a live exposure and needs fixing today regardless of anything else.

**Finding 2 is intentionally deferred — this is a decision, not an oversight.**
Adegoke needs ongoing write access to the DB during active development, and RLS
lockdown gets in the way of that on every iteration. The fix is deferred until a
specific, unambiguous trigger condition:

> ⚠️ **SHIP BLOCKER — DO NOT MISS THIS:** Finding 2 (RLS + service-role key) MUST be
> fixed before `phase6_api.py` (or any successor API) is deployed anywhere reachable
> from the public internet — Vercel, Render, Railway, Fly, a public URL of any kind.
> It does NOT need to happen before other development work. The risk is contained
> right now only because the API currently runs locally via `uvicorn` on Adegoke's
> own machine — nothing untrusted can reach it. The moment that stops being true,
> this fix is no longer optional. If you (Claude Code) are ever asked to help deploy
> this API publicly, treat that request as blocked until this finding is resolved,
> and say so explicitly rather than proceeding.

Everything else in this document should be fixed on the schedule in "Recommended
execution order" below, which accounts for this deferral.

---

## CRITICAL — Security

### Finding 1: Full article bodies are committed to a public GitHub repository

**Where:** `naijapulse-engine/naijapulse_local.db` (tracked in git, 811 KB, confirmed
present at commit `79b59ae`)

**What's actually in it** (verified by opening the file):
```
articles columns: id, source_id, url, title, summary, full_text, published_at,
                  fetched_at, content_hash, cluster_id

Sample row:
  title: "Makinde: All 39 pupils rescued, five teachers freed — but we lost soldiers"
  full_text: "Seyi Makinde, governor of Oyo state, says all 39 pupils abducted in
              Oriire LGA h[...]"  <- full scraped article body, not a snippet
  url: https://www.thecable.ng/makinde-all-39-pupils-rescued-...
```
129 articles, full text populated, sitting in a **public** repository (confirmed: no
private-repo indicator on GitHub, 0 stars/forks, publicly viewable and clonable by
anyone right now).

**Root cause:** `naijapulse-engine/ingest.py` is a fallback ingestion script that
writes to this local SQLite file when Supabase is unreachable. The file header even
says so correctly:
```python
"""
!!! NON-PRIMARY / FALLBACK SCRIPT — DO NOT USE AS THE MAIN INGESTION PATH !!!

This script writes to a LOCAL SQLite mirror (naijapulse_local.db) and is only a
demo/fallback used when the live Supabase project is unreachable.
"""
```
The script correctly warns not to use it as the main path — but its *output file* got
committed to git anyway, at some point before `.gitignore` picked up the `*.db` rule.
`.gitignore` only prevents *future* untracked files from being added; it does nothing
for a file that's already tracked. `git log --all -p` confirms `naijapulse_local.db`
has been in every commit since it was first added — it's in the permanent history of
a public repo, not just the current working tree.

**Why this matters:** This isn't a style issue — it's the exact legal/business
boundary you defined for this project from the very first spec (`GET /stories`
endpoints deliberately exclude `full_text`; `phase6_api.py` has a whole middleware
dedicated to guaranteeing `full_text` never leaks via the API — see Finding 4). All
of that discipline is currently bypassed by one committed binary file that anyone can
`git clone` and open in five seconds. Reproducing full article bodies from Nigerian
publishers at scale, in public, is precisely the "mass copyright infringement" outcome
this project has been engineered from day one to avoid.

**Fix — plan:**
1. Remove `naijapulse_local.db` from the working tree and from git history (not just
   `git rm` — that only removes it from *future* commits; it stays in history forever
   otherwise, in a public repo, permanently downloadable via old commit SHAs).
2. Delete `ingest.py` entirely rather than keep it as a "fallback" — see Finding 8,
   it's dead code with zero references anywhere else in the codebase, and it's the
   sole source of this problem. If a local-fallback mode is genuinely needed later,
   rebuild it deliberately with an output path that's actually gitignored *before*
   the first commit, not after.
3. Fix `.gitignore` to also ignore `*.sqlite` (currently only `*.db`/`*.sqlite3` are
   listed — `naijapulse-engine/naijapulse_local.db` matches `*.db` fine, but be
   defensive against any future naming variant).

**Exact commands:**
```bash
# 1. Remove the dead fallback script (see Finding 8 for why this is also a
#    correctness/duplication problem, not just the source of this leak)
git rm naijapulse-engine/ingest.py

# 2. Purge the DB file from ALL history, not just HEAD. Use git-filter-repo
#    (the git-recommended tool — git filter-branch is deprecated and slower).
pip install git-filter-repo
git filter-repo --path naijapulse-engine/naijapulse_local.db --invert-paths

# 3. Force-push the rewritten history (this changes every commit SHA downstream
#    of the file's introduction — coordinate this, don't do it silently)
git push origin --force --all
git push origin --force --tags

# 4. Rotate anything that could have been inferred from the exposed data as a
#    precaution — not because a scrape of public news headlines is itself a secret,
#    but because "audit everything that touched a public exposure" is the correct
#    reflex, not an optional step.
```

**Instructions for Claude Code:**
```
Remove naijapulse-engine/ingest.py entirely (confirm first that nothing imports or
subprocesses it — grep the whole repo for "ingest.py" excluding "ingest_supabase.py"
and "ingest_report.json"; expect zero results). Then remove
naijapulse-engine/naijapulse_local.db from git history using git-filter-repo (not
git filter-branch, and not just git rm — it must be purged from history since this
is a public repo). Force-push the rewritten history. Update .gitignore to also cover
*.sqlite (not just *.db/*.sqlite3) as a defensive measure. Report back: confirmation
the file no longer appears in `git log --all --full-history -- naijapulse-engine/naijapulse_local.db`,
and confirmation ingest.py had zero references before removal.
```

---

### Finding 2: Row Level Security is explicitly disabled, service-role key runs inside the public-facing API process

**Status: DEFERRED, deliberately.** Adegoke has decided to keep RLS off and
`phase6_api.py` on the service-role key for now, because active development needs
ongoing write access to the DB. Do not implement this fix until specifically asked
to, or until this project is about to be deployed to a publicly reachable host —
whichever comes first. The finding and its fix are documented in full below so
they're ready to execute immediately when that moment comes, not so they get done
today.

**Where:** `supabase/init_tables.sql`
```sql
-- Enable RLS off for the service role ingestion path (server-to-server).
-- If you later add a public/anon client, enable RLS and add policies here.
alter table public.sources    disable row level security;
alter table public.articles   disable row level security;
alter table public.embeddings disable row level security;
alter table public.clusters   disable row level security;
```
And `naijapulse-engine/phase6_api.py`:
```python
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]   # <- this is the service-role key,
                                              #    per PROGRESS.md's own description
                                              #    ("SUPABASE_KEY in .env, service-role path")
```

**Why this matters:** The SQL comment is honest about the tradeoff — RLS-off is a
reasonable choice *while* every consumer is a trusted server-to-server script running
on your own Mac. But `phase6_api.py` is explicitly designed to eventually sit behind
a public URL (that's the entire point of Phase 6, and it's the explicit next step
discussed for Vercel/Render deployment). The service-role key bypasses RLS
unconditionally — it doesn't matter whether RLS is on or off for a service-role
connection, service-role always has full read/write on every table. Combining
"RLS disabled" with "service-role key loaded inside the internet-facing process" means
that any future vulnerability in that process — a dependency CVE, a misconfigured
route, an SSRF, even just an accidentally verbose error message leaking the key in a
stack trace — is a full database compromise (read AND write on every table), not a
contained read-only incident. A read-only API should not have write-capable
credentials sitting in its process memory at all. This is the single most important
structural fix in this document, and it needs to happen **before** any deployment
step, not after.

**Fix — plan:**
1. Create a dedicated Postgres role for the API with SELECT-only grants on exactly the
   tables/columns it needs — not service-role, not even the Postgres `anon` role
   unmodified, a purpose-built role.
2. Enable RLS on all four tables and add explicit read-only policies scoped to that
   role.
3. `phase6_api.py` authenticates as that role, never as service-role. Ingestion/
   pipeline scripts keep using service-role, since they're server-to-server and never
   directly internet-facing.

**Code:**
```sql
-- New migration: supabase/migrations/0004_readonly_api_role.sql

-- 1. Re-enable RLS everywhere.
alter table public.sources    enable row level security;
alter table public.articles   enable row level security;
alter table public.embeddings enable row level security;
alter table public.clusters   enable row level security;
alter table public.stories    enable row level security;   -- was missing from the
                                                              -- original disable list
                                                              -- too; it holds
                                                              -- bias_distribution
                                                              -- data, needs a policy

-- 2. Dedicated read-only role for the API layer.
create role naijapulse_api_readonly nologin;
grant usage on schema public to naijapulse_api_readonly;
grant select on public.sources, public.articles, public.stories to naijapulse_api_readonly;
-- Deliberately NOT granted: embeddings (contains raw vectors — never needed by the
-- API contract), clusters (legacy/unused table per bias_blindspot.py's own comment).

-- 3. Explicit read policies. full_text is excluded at the column level, not just
--    the application level, so even a bug in phase6_api.py's query construction
--    physically cannot pull it back.
create policy api_read_sources on public.sources
  for select to naijapulse_api_readonly using (true);

create policy api_read_stories on public.stories
  for select to naijapulse_api_readonly using (true);

-- articles: expose only the columns the API contract actually returns. Postgres RLS
-- policies control ROWS, not columns, so column-level exclusion needs a view:
create view public.articles_api_safe as
  select id, source_id, url, title, summary, image_url, published_at,
         canonical_article_id, cluster_id
  -- full_text, content_hash, dedup_score, fetched_at deliberately excluded
  from public.articles;

grant select on public.articles_api_safe to naijapulse_api_readonly;

create policy api_read_articles_safe on public.articles_api_safe
  for select to naijapulse_api_readonly using (true);
```

```python
# phase6_api.py — use a scoped connection, not service-role, for the API's own
# runtime queries. Service-role stays reserved for pipeline scripts only.
API_SUPABASE_URL = os.environ["SUPABASE_URL"]
API_READONLY_KEY = os.environ["SUPABASE_API_READONLY_KEY"]  # new, separate secret —
                                                               # NOT the service-role key

@lru_cache(maxsize=1)
def get_client():
    return create_client(API_SUPABASE_URL, API_READONLY_KEY)
```
Also update every `client.table("articles")` call in `phase6_api.py` to
`client.table("articles_api_safe")` so the view (not the raw table) is what the
read-only role actually touches — this makes Finding 4's belt-and-braces middleware
genuinely redundant instead of the *only* thing standing between the API and a leak.

**Why this is better:** Defense in depth. Right now there is exactly one thing
preventing a `full_text` leak from the API: a Python middleware function (and it's
currently broken — see Finding 4). After this fix, there are three independent
layers: the database role physically cannot SELECT `full_text` at all, the view
doesn't expose the column, and the middleware is a genuine last-resort backstop
instead of the sole line of defense. Even a completely rewritten `phase6_api.py` with
new bugs could not leak `full_text`, because the credential it's using doesn't have
access to it.

**Instructions for Claude Code:**
```
1. Write supabase/migrations/0004_readonly_api_role.sql exactly as specified above,
   adapted to match whatever the live schema actually calls the stories/articles
   columns (verify against the real DB first — this project's schema has drifted
   from written specs before).
2. Apply the migration to the live Supabase project.
3. Generate a new API-scoped key for the naijapulse_api_readonly role via Supabase's
   dashboard (or the appropriate Supabase CLI/API mechanism) and add it to .env as
   SUPABASE_API_READONLY_KEY, alongside (not replacing) the existing SUPABASE_KEY
   used by pipeline scripts.
4. Update phase6_api.py to use the new readonly credential and query
   articles_api_safe instead of articles directly.
5. Acceptance test: attempt a raw SELECT full_text FROM articles using the new
   readonly role's credentials directly (via psql or the Supabase SQL editor logged
   in as that role) and confirm it is REJECTED — this is the test that actually
   proves the fix, not just that the API's Python code happens not to ask for it.
6. Confirm all existing phase6_api.py acceptance tests (from PHASE6_BUILD.md) still
   pass under the new credential — if any endpoint breaks, that's a sign the readonly
   grants are too narrow, widen them deliberately rather than reaching for
   service-role again.
```

---

## CRITICAL — Correctness bugs

### Finding 3: `run_pipeline.py` silently swallows failures from every stage except the last

**Where:** `naijapulse-engine/run_pipeline.py`, lines 83–111
```python
rc = _run("setup_supabase.py")
if rc != 0:
    print("⚠ Schema step returned non‑zero; continuing anyway.")

rc = _run("ingest_supabase.py")          # rc reassigned, never checked

if do_embed:
    rc = _run("embed_articles.py")        # rc reassigned, never checked

if do_dedup:
    rc = _run("dedup.py")                 # rc reassigned, never checked

if do_cluster:
    rc = _run("cluster_stories.py")       # rc reassigned, never checked

if do_bias:
    rc = _run("bias_blindspot.py")        # rc reassigned, never checked

return rc   # <- only the LAST stage that happened to run determines the exit code
```

**Why this matters:** Run `./run_pipeline.py --all` and imagine ingestion
(`ingest_supabase.py`) crashes outright — network down, Supabase unreachable,
whatever. `rc` is 1. The script does not stop. It proceeds to embed (nothing new to
embed, exits 0), dedup (nothing new, exits 0), cluster (nothing new, exits 0), bias
(nothing new, exits 0 — Phase 5's own `_evaluate_blindspot` gate means "no data" and
"no blindspots found" look identical). The final `return rc` is 0, because Phase 5 was
the last thing that ran and it succeeded on stale data. Anything watching this
process's exit code — a cron job, CI, a future monitoring hook — sees "success." This
is the exact failure mode that makes a pipeline untrustworthy: it doesn't fail loudly,
it fails silently and reports green.

**Fix:**
```python
def main() -> int:
    # ... argument parsing unchanged ...

    print(f"▶ Step {step+1}/{steps} — Setting up Supabase schema...")
    step += 1
    rc = _run("setup_supabase.py")
    if rc != 0:
        print(f"✗ Schema setup failed (exit {rc}). Aborting — nothing downstream "
              f"can be trusted if the schema step failed.")
        return rc

    print(f"\n▶ Step {step+1}/{steps} — Running ingestion pipeline (Phase 1)...")
    step += 1
    rc = _run("ingest_supabase.py")
    if rc != 0:
        print(f"✗ Ingestion failed (exit {rc}). Aborting remaining stages — "
              f"downstream stages would run against stale/incomplete data and "
              f"falsely report success.")
        return rc

    if do_embed:
        print(f"\n▶ Step {step+1}/{steps} — Embedding articles (Ollama, Phase 2)...")
        step += 1
        rc = _run("embed_articles.py")
        if rc != 0:
            print(f"✗ Embedding failed (exit {rc}). Aborting.")
            return rc

    if do_dedup:
        print(f"\n▶ Step {step+1}/{steps} — Near‑duplicate detection (Phase 3)...")
        step += 1
        rc = _run("dedup.py")
        if rc != 0:
            print(f"✗ Dedup failed (exit {rc}). Aborting.")
            return rc

    if do_cluster:
        print(f"\n▶ Step {step+1}/{steps} — Story clustering (Phase 4)...")
        step += 1
        rc = _run("cluster_stories.py")
        if rc != 0:
            print(f"✗ Clustering failed (exit {rc}). Aborting.")
            return rc

    if do_bias:
        print(f"\n▶ Step {step+1}/{steps} — Bias tagging (Phase 5)...")
        step += 1
        rc = _run("bias_blindspot.py")
        if rc != 0:
            print(f"✗ Bias tagging failed (exit {rc}).")
            return rc

    print("\n✓ Pipeline completed successfully — all stages ran and reported success.")
    return 0
```

**Why this is better:** Fail-fast is the correct default for a data pipeline where
each stage depends on the previous one's output being trustworthy. A stage that didn't
run cleanly should never be silently treated as "produced nothing new, so downstream
is fine" — those are two different states (failure vs. no-new-data) and the current
code cannot tell them apart. This fix makes the exit code mean what it says.

**Instructions for Claude Code:**
```
Replace the body of run_pipeline.py's main() exactly as shown in Finding 3 of
CODE_AUDIT.md — every stage must check its own rc and abort immediately with a
non-zero exit if that stage failed, rather than continuing to the next stage.
Acceptance test: temporarily break ingest_supabase.py (e.g. point SUPABASE_URL at an
invalid host), run `./run_pipeline.py --all`, and confirm the run stops after the
ingestion step with a non-zero exit code, instead of continuing through embed/dedup/
cluster/bias and reporting success. Then restore the correct SUPABASE_URL and confirm
a normal --all run still completes and returns 0.
```

---

### Finding 4: The `--dedup-only` / `--cluster-only` / `--bias-only` flags don't actually skip earlier phases

**Where:** `naijapulse-engine/run_pipeline.py`, lines 81–89 — outside every
conditional:
```python
print(f"▶ Step {step+1}/{steps} — Setting up Supabase schema...")
step += 1
rc = _run("setup_supabase.py")
...
print(f"\n▶ Step {step+1}/{steps} — Running ingestion pipeline (Phase 1)...")
step += 1
rc = _run("ingest_supabase.py")     # <- runs UNCONDITIONALLY, every invocation

if do_embed:      # only gates stages AFTER this point
    ...
```

**Why this matters:** The docstring and `--help` text both promise
`--dedup-only` = "Phase 3 only (rerun)" and `--bias-only` = "Run bias tagging only
(Phase 5) — assumes 1-4 are done." PROGRESS.md documents the same claim:
`--dedup-only → Phase 3 only (rerun on new rows)`. None of that is true in the actual
code — every invocation of `run_pipeline.py`, including `--bias-only`, triggers a full
schema-setup pass and a full RSS re-ingestion of all 10 feeds (network calls,
trafilatura full-text extraction, image scraping) before it does the thing you asked
for. Beyond wasting time and bandwidth on every "just rerun Phase 5" call, this is a
correctness trap: you cannot actually test "rerun Phase 5 in isolation against the
current data" with this tool, because it always mutates the data first by ingesting
whatever's new. If you're debugging a Phase 5 threshold and expect the input data to
be held constant between two runs, it silently isn't.

**Fix:**
```python
def main() -> int:
    # ... argument parsing unchanged ...

    only_mode = args.dedup_only or args.cluster_only or args.bias_only
    full = args.dedup or args.cluster or args.all or args.bias
    do_embed = args.embed or full
    do_dedup = args.dedup or args.dedup_only or full
    do_cluster = bool(args.cluster or args.all or args.cluster_only or args.bias)
    do_bias = bool(args.bias or args.all or args.bias_only)
    do_ingest_and_schema = full or args.embed or not only_mode
    # ^ i.e. skip schema+ingestion ONLY when a bare "*-only" flag was passed alone

    steps = (2 if do_ingest_and_schema else 0) \
        + (1 if do_embed and not only_mode else 0) \
        + (1 if do_dedup else 0) + (1 if do_cluster else 0) + (1 if do_bias else 0)
    step = 0

    if do_ingest_and_schema:
        print(f"▶ Step {step+1}/{steps} — Setting up Supabase schema...")
        step += 1
        rc = _run("setup_supabase.py")
        if rc != 0:
            print(f"✗ Schema setup failed (exit {rc}). Aborting.")
            return rc

        print(f"\n▶ Step {step+1}/{steps} — Running ingestion pipeline (Phase 1)...")
        step += 1
        rc = _run("ingest_supabase.py")
        if rc != 0:
            print(f"✗ Ingestion failed (exit {rc}). Aborting.")
            return rc
    else:
        print("↷ Skipping schema setup + ingestion ('*-only' flag — operating on "
              "existing data).")

    # ... rest unchanged, using the corrected do_embed/do_dedup/do_cluster/do_bias ...
```

**Why this is better:** Makes the tool's actual behavior match its documented and
`--help`-advertised behavior. A "-only" flag that silently does more than it claims is
worse than not having the flag at all, because it actively misleads whoever's running
it (including future-you, six weeks from now, trusting the docstring).

**Instructions for Claude Code:**
```
Fix run_pipeline.py so that when EXACTLY ONE of --dedup-only / --cluster-only /
--bias-only is passed (with no other flags), schema setup and ingest_supabase.py are
SKIPPED entirely — the run operates only on data already in Supabase. Combined flags
(e.g. --bias --dedup-only together, or --all) should still run the full chain as
before. Acceptance test: run `./run_pipeline.py --bias-only` and confirm via the
printed step log that ingestion did NOT run — cross-check article counts in Supabase
before and after the run are identical (no new articles), while stories/bias columns
were still updated.
```

---

### Finding 5: The SQLite fallback in `ingest_supabase.py` is dead code that would crash, not fall back

**Where:** `naijapulse-engine/ingest_supabase.py`, lines 99–109
```python
class SupabaseDB:
    def __init__(self, url: str, key: str):
        try:
            self.supabase: Client = create_client(url, key)
            self._use_sqlite = False
        except Exception as e:
            logger.warning(f"Supabase client init failed ({e}), falling back to SQLite for demo.")
            self.supabase = None          # <- set to None here
            self._use_sqlite = True
            self._init_sqlite()
```
But every method on this class — `seed_sources`, `get_sources`, `article_exists`,
`insert_article` — unconditionally does `self.supabase.table(...)`, e.g.:
```python
def article_exists(self, url: str) -> bool:
    try:
        result = self.supabase.table('articles').select('id').eq('url', url)...
        #        ^^^^^^^^^^^^^ None.table(...) -> AttributeError, every time
```

**Why this matters:** The log message promises graceful degradation ("falling back to
SQLite for demo") that cannot possibly happen — the very next line of code after that
log message will throw `AttributeError: 'NoneType' object has no attribute 'table'`.
This is worse than having no fallback at all, because the misleading log message would
send whoever's debugging a real Supabase outage looking in the wrong direction — they'd
read "falling back to SQLite" and assume the run degraded gracefully, when it actually
crashed one line later with an unrelated-looking `NoneType` error.

**Fix — plan:** Don't try to make the fallback actually work (that's re-implementing
half the class twice, for a path that should arguably never exist in the API-serving
or production pipeline path — see Finding 2's point about credential separation).
Instead, fail loudly and honestly.
```python
class SupabaseDB:
    def __init__(self, url: str, key: str):
        self.supabase: Client = create_client(url, key)
        # If this raises, let it raise — do not silently swap to a different
        # storage backend with different guarantees. A pipeline run that thinks
        # it's writing to Supabase but is actually writing to local SQLite is a
        # silent data-loss bug, not a resilience feature.
```

**Why this is better:** A pipeline that can't reach its database of record should stop
immediately and say so clearly, not quietly redirect writes somewhere else that
nothing downstream (embedding, dedup, clustering, the API) knows to look at. If local
offline development against SQLite is genuinely wanted later, it should be an explicit
`--local` flag the operator chooses, never an implicit fallback triggered by a
transient connection error.

**Instructions for Claude Code:**
```
Remove the SQLite fallback branch from SupabaseDB.__init__ in ingest_supabase.py
entirely — let create_client's exception propagate. Also remove the now-unreachable
_init_sqlite / _create_sqlite_schema methods on this class (confirm they're not used
elsewhere first — the local db init logic for actual local demo purposes belongs, if
kept anywhere, only in a script that's explicitly opt-in, not this one). Acceptance
test: temporarily set SUPABASE_URL to an invalid value, run ingest_supabase.py, and
confirm it exits with a clear connection-error message rather than an unrelated
AttributeError several calls later.
```

---

### Finding 6: The `full_text` scrubbing middleware in `phase6_api.py` is broken exactly when it matters

**Where:** `naijapulse-engine/phase6_api.py`, lines 460–473
```python
@app.middleware("http")
async def strip_full_text(request, call_next):
    response = await call_next(request)
    if isinstance(response, JSONResponse):
        body = response.body
        try:
            payload = json.loads(body)
            _scrub(payload)
            response.body = json.dumps(payload).encode("utf-8")   # <- mutated here
        except Exception:
            pass
    return response
```

**Why this matters:** Starlette's `Response` object computes its `Content-Length`
header once, at construction time, from the byte length of `response.body` at that
moment (`Response.init_headers`, called inside `__init__`). Reassigning
`response.body` afterward — which is exactly what this middleware does — changes the
body's byte length but does **not** update the already-computed `Content-Length`
header. This is a known Starlette/FastAPI footgun. Concretely: on every single request
where `_scrub` actually has something to remove (i.e., the one case this whole
middleware exists for — a future regression that accidentally includes `full_text`
somewhere), the response sent to the client will have a `Content-Length` header that
disagrees with the actual body length. Depending on the HTTP client, this produces a
truncated body, a hung connection waiting for bytes that will never arrive, or an
outright rejected response. The one safety net specifically built to catch a
`full_text` leak is itself non-functional in exactly the scenario it exists to handle.
In the current, correct state of the code (nothing actually leaks `full_text` today),
this bug is invisible — `_scrub` finds nothing to remove, the body's byte length
doesn't change, `Content-Length` still happens to match by coincidence. It will only
surface the day it's actually needed, which is the worst possible time to discover it.

**Fix:**
```python
from starlette.responses import Response

@app.middleware("http")
async def strip_full_text(request, call_next):
    response = await call_next(request)
    if isinstance(response, JSONResponse):
        try:
            payload = json.loads(response.body)
            _scrub(payload)
            new_body = json.dumps(payload).encode("utf-8")
            # Build a fresh Response so headers (Content-Length included) are
            # recomputed from the new body, instead of mutating body in place
            # on an already-constructed Response.
            return Response(
                content=new_body,
                status_code=response.status_code,
                headers={k: v for k, v in response.headers.items()
                         if k.lower() != "content-length"},
                media_type=response.media_type,
            )
        except Exception:
            pass
    return response
```

**Why this is better:** Constructing a new `Response` recomputes `Content-Length`
correctly from the actual final body, the same way FastAPI does it for any normal
response. This makes the belt-and-braces guard actually hold up under the one
condition it was written for. Combined with Finding 2 (column-level exclusion at the
database role/view level), `full_text` now has three independent layers preventing a
leak instead of one broken one.

**Instructions for Claude Code:**
```
Replace the strip_full_text middleware in phase6_api.py exactly as shown in Finding 6
of CODE_AUDIT.md. Acceptance test: temporarily add a full_text field to one of the
dict literals returned by an endpoint (e.g. hardcode "full_text": "x" * 5000 into the
/stories response for this test only), hit that endpoint with curl -v, and confirm
(a) the response body does NOT contain "full_text" and (b) the returned Content-Length
header matches the actual byte length of the response body received (curl will show
a "transfer closed with N bytes remaining to read" or similar warning if they
mismatch — confirm that warning is ABSENT). Then remove the temporary hardcoded field.
```

---

## HIGH — Performance / scalability

These won't hurt you at 131 stories and 232 articles. They will hurt you the moment
this project has real ongoing volume, because every one of them is a per-row network
round trip that scales linearly with your data instead of a batched operation that
doesn't.

### Finding 7: Per-article existence checks in the ingestion loop (N+1 queries)

**Where:** `naijapulse-engine/ingest_supabase.py`, inside `run_ingestion`:
```python
for entry in entries[:50]:                      # up to 50 entries per source
    stats.articles_total += 1
    link = entry.get('link', '')
    if db.article_exists(link):                  # <- 1 network round trip, PER ENTRY
        continue
    article_data, extraction_status = process_entry(entry, source_id, ...)
    new_id = db.insert_article(article_data)      # <- another round trip, per entry
```
`article_exists()` itself:
```python
def article_exists(self, url: str) -> bool:
    result = self.supabase.table('articles').select('id').eq('url', url).limit(1).execute()
    return len(result.data) > 0
```

**Why this matters:** With 10 sources × up to 50 entries each, that's up to 500
individual `SELECT ... WHERE url = ?` round trips to Supabase per ingestion run, each
paying full HTTP + PostgREST latency, before a single article has even been inserted.
This is the textbook N+1 query pattern, and it's the single biggest reason ingestion
runs feel slow as your source list grows.

**Fix:**
```python
def get_existing_urls(self, urls: List[str]) -> Set[str]:
    """Batch existence check: one round trip instead of one per URL."""
    if not urls:
        return set()
    # PostgREST .in_() filter — single query for the whole batch.
    result = self.supabase.table('articles').select('url').in_('url', urls).execute()
    return {row['url'] for row in (result.data or [])}
```
```python
# In run_ingestion, per source: collect all entry links first, do ONE existence
# check for the whole batch, then only process genuinely new ones.
entry_links = [e.get('link', '') for e in entries[:50] if e.get('link')]
existing = db.get_existing_urls(entry_links)

for entry in entries[:50]:
    link = entry.get('link', '')
    if not link or link in existing:
        stats.articles_total += 1
        continue
    stats.articles_total += 1
    article_data, extraction_status = process_entry(entry, source_id, ...)
    new_id = db.insert_article(article_data)
    ...
```

**Why this is better:** Collapses up to 50 round trips per source into 1. At 10
sources that's turning up to 500 sequential network calls into 10 — a real,
measurable speedup on every single ingestion run, and it gets more valuable as you add
sources, not less.

**Instructions for Claude Code:**
```
Add get_existing_urls() to the SupabaseDB class in ingest_supabase.py as shown, and
update run_ingestion() to batch the existence check per source before the per-entry
loop, instead of calling article_exists() inside the loop. Acceptance test: add timing
instrumentation (or just wall-clock the run) before and after this change, on the same
set of sources, and report the actual before/after elapsed time for the ingestion
step. Confirm article counts (new articles inserted) are identical between the two
versions — this must be a pure performance fix, not a behavior change.
```

---

### Finding 8: Every article's page is downloaded twice over the network

**Where:** `naijapulse-engine/ingest_supabase.py`, `process_entry()`:
```python
full_text = None
if url and not summary_only:
    full_text, extraction_status = extract_full_text(url)   # <- fetches url (trafilatura)
    ...
image_url = None
if url and not summary_only:
    image_url, _ = extract_image_url(url)                    # <- fetches url AGAIN (requests)
```
`extract_full_text` uses `trafilatura.fetch_url(url)` internally.
`extract_image_url` independently does its own `requests.get(url, ...)`. Both
download the exact same HTML page, for the exact same article, back to back.

**Why this matters:** Every article ingested costs two full HTTP round trips to the
publisher's site instead of one — doubling both the wall-clock time per article and
the load your scraper puts on Nigerian news sites (several of which, per your own
`BROWSER_HDR` comment, already actively try to block scrapers with 403 challenges).
Fewer, not more, requests to sites that are already rate-limiting you is directly in
your interest.

**Fix:**
```python
def fetch_page(url: str, timeout: int = 20) -> Optional[str]:
    """Single download of the raw HTML, reused by both extraction steps."""
    try:
        resp = requests.get(url, headers=BROWSER_HDR, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
    except requests.exceptions.RequestException:
        pass
    return None


def extract_full_text_from_html(html: str) -> tuple:
    if not html:
        return None, "download_failed"
    extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not extracted:
        return None, "extraction_failed"
    return extracted, None


def extract_image_url_from_html(html: str, base_url: str) -> tuple:
    if not html:
        return None, "no_html"
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return og["content"], None
    img = soup.find("img")
    if img and img.get("src"):
        return requests.compat.urljoin(base_url, img["src"]), None
    return None, "no_image"
```
```python
# In process_entry: fetch once, extract twice from the same HTML.
html = fetch_page(url) if (url and not summary_only) else None
full_text, extraction_status = extract_full_text_from_html(html)
image_url, _ = extract_image_url_from_html(html, url)
```

**Why this is better:** One network fetch per article instead of two — halves
scraping-related network time and load on the publishers' servers, with identical
output. `trafilatura.extract()` already accepts raw HTML directly (it doesn't need to
be the one doing the fetching), so this is a pure efficiency win with no functional
change.

**Instructions for Claude Code:**
```
Refactor extract_full_text() and extract_image_url() in ingest_supabase.py into the
fetch_page() + extract_full_text_from_html() + extract_image_url_from_html() pattern
shown in Finding 8, so each article's HTML is downloaded exactly once and reused for
both extractions. Preserve all existing error-status strings (download_failed,
extraction_failed, timeout, blocked, other) so IngestionStats counters keep working
unchanged. Acceptance test: run ingestion against a small batch and confirm full_text
and image_url extraction success rates are unchanged from before the refactor (this
must be a pure efficiency fix), while reporting the reduction in total HTTP requests
made (should be roughly half of before, for articles where both extractions were
attempted).
```

---

### Finding 9: `/sources` and `/pipeline-health` pull the entire `articles` table into memory on every request

**Where:** `naijapulse-engine/phase6_api.py`, both endpoints do this:
```python
articles = (client.table("articles")
            .select("source_id, canonical_article_id")
            .execute()
            .data) or []
canonical_per_source: Dict[str, int] = Counter()
for a in articles:
    if a.get("canonical_article_id") is None:
        canonical_per_source[a.get("source_id")] += 1
```

**Why this matters:** This fetches every row in `articles` over the network and
aggregates in Python, on every single call to these endpoints. At 232 articles this is
free. At 50,000 articles, every request to `/sources` or `/pipeline-health` — which,
per your own design, are meant to be hit repeatedly for monitoring — pulls the entire
table across the network and iterates it in the API process's memory. This is a
classic "worked fine on the demo dataset, fell over in production" pattern.

**Fix:** Push the aggregation into SQL, where Postgres can use an index and never
transfer raw rows across the network at all.
```sql
-- One-time: a Postgres function (or just inline the equivalent PostgREST RPC call)
create or replace function canonical_counts_by_source()
returns table(source_id uuid, canonical_count bigint) as $$
  select source_id, count(*) as canonical_count
  from public.articles
  where canonical_article_id is null
  group by source_id;
$$ language sql stable;
```
```python
# phase6_api.py
canonical_rows = client.rpc("canonical_counts_by_source").execute().data or []
canonical_per_source = {r["source_id"]: r["canonical_count"] for r in canonical_rows}
```

**Why this is better:** The response size of this query is "one row per source"
(currently 10, will still be small even at 100 sources) instead of "one row per
article" (currently 232, grows unboundedly forever). This is the difference between a
query that stays fast as the dataset grows and one that gets linearly slower with
every article ever ingested.

**Instructions for Claude Code:**
```
Add the canonical_counts_by_source() SQL function via a new migration. Update the
/sources and /pipeline-health endpoints in phase6_api.py to call it via
client.rpc(...) instead of fetching the full articles table and aggregating in
Python. Apply the same pattern to _fetch_canonical_titles_by_story() if a similar
SQL-side alternative is practical — if not (it needs actual title text back, not just
counts, so a full transfer may be unavoidable there), at minimum add a code comment
explaining why that one is different, so a future reader doesn't assume it was missed.
Acceptance test: confirm /sources and /pipeline-health return identical data to
before the change, and report the query row-count difference (should go from N
articles to ~10 sources for the aggregation query specifically).
```

---

### Finding 10: Bias tagging does two sequential network round trips per story, in a Python loop

**Where:** `naijapulse-engine/bias_blindspot.py`, `run_bias()`:
```python
for st in stories:                                  # 131 stories today
    sid = st["id"]
    res = compute_cluster(client, sid, ...)          # round trip #1: fetch members
    client.table("stories").update({...}).eq("id", sid).execute()  # round trip #2: write
```

**Why this matters:** 131 stories today means 262+ sequential Supabase round trips
every time Phase 5 runs, and this grows linearly with your story count forever, with
no batching at all. This is the same underlying pattern as Finding 7, in a different
file.

**Fix — plan:** Batch the read (fetch all canonical articles across all stories in
one query, group in Python by `cluster_id`), then batch the write (Supabase's
`upsert` accepts a list of rows in a single call).
```python
# One query for ALL canonical articles across every story, instead of one query per story.
all_members = (client.table("articles")
               .select("id, source_id, title, cluster_id")
               .is_("canonical_article_id", "null")
               .execute()
               .data) or []

members_by_story: Dict[str, List[dict]] = {}
for m in all_members:
    cid = m.get("cluster_id")
    if cid:
        members_by_story.setdefault(cid, []).append(m)

# Compute in Python (no network calls inside this loop now).
updates = []
for st in stories:
    sid = st["id"]
    members = members_by_story.get(sid, [])
    res = compute_cluster_from_members(members, lean_by_source, vocabulary,
                                        missing_bias_sources,
                                        representative_title=st.get("representative_title"))
    updates.append({
        "id": sid,
        "bias_distribution": res["bias_distribution"],
        "bias_coverage_pct": res["bias_coverage_pct"],
        "is_blindspot": res["is_blindspot"],
        "blindspot_checked_at": now,
    })

# One batched write instead of 131 sequential ones.
client.table("stories").upsert(updates).execute()
```
(`compute_cluster` needs a small signature change to accept an already-fetched
`members` list instead of querying inside itself — straightforward refactor, logic
unchanged.)

**Why this is better:** Turns 262+ sequential round trips into 2 (one bulk read, one
bulk write), regardless of story count. This is the difference between a Phase 5 run
that takes seconds versus one that takes minutes once you have thousands of stories.

**Instructions for Claude Code:**
```
Refactor bias_blindspot.py's run_bias() to fetch all canonical articles in a single
query upfront, group them by cluster_id in Python, and compute each story's bias
result without a per-story database read. Then write all story updates in a single
batched upsert instead of one .update().execute() call per story inside the loop.
Rename compute_cluster to compute_cluster_from_members and change its signature to
accept a pre-fetched members list rather than querying inside the function — keep all
existing logic (normalization, gating, blindspot rule) unchanged, this is purely
about where the data comes from. Acceptance test: confirm bias_distribution,
bias_coverage_pct, and is_blindspot values are IDENTICAL to a run before this
refactor (diff the two runs' output story-by-story), and report the wall-clock time
difference between the old per-story-round-trip version and the new batched version.
```

---

## MEDIUM — Code quality, duplication, reproducibility

### Finding 11: The political-topic keyword list and logic are duplicated across two files, and the pipeline never persists what it computes

**Where:** Both `naijapulse-engine/bias_blindspot.py` (lines 62–73, 203–213) and
`naijapulse-engine/phase6_api.py` (lines 60–71, 86–91) contain an identical
43-keyword `POLITICAL_KEYWORDS` tuple and an identical `_is_political_topic()`
function — copy-pasted, not shared.

Worse: `bias_blindspot.py` computes `is_political` for every story
(`compute_cluster()` returns it), but the actual database write only persists four
columns:
```python
client.table("stories").update({
    "bias_distribution": res["bias_distribution"],
    "bias_coverage_pct": res["bias_coverage_pct"],
    "is_blindspot": res["is_blindspot"],
    "blindspot_checked_at": now,
    # is_political is computed above but never written here
}).eq("id", sid).execute()
```
So `phase6_api.py` has no choice but to recompute it from scratch, at request time,
using its own separately-maintained copy of the same keyword list.

**Why this matters:** Two independently-edited copies of the same business logic will
drift. If you ever tune the keyword list (add "cybersecurity bill" or remove an
over-triggering term like "fuel," which currently also fires on ordinary fuel-price
human-interest stories, not just policy ones) in one file during a future debugging
session, the other file silently keeps the old behavior. The pipeline's stored
`is_blindspot` was computed using the gate at write time; the API's `is_political_topic`
field is computed fresh on every request using whatever the *current* code says — for
the same story, these two could legitimately disagree after any future edit to just
one of the two copies, and nothing would flag that they'd diverged.

**Fix:**
```python
# New file: naijapulse-engine/political_topic.py
"""Single source of truth for political-topic detection. Imported by both the
pipeline (bias_blindspot.py) and the API (phase6_api.py) so they can never drift."""

POLITICAL_KEYWORDS = (
    "election", "inec", "government", "govt", "minister", "senate", "policy",
    # ... full existing list, moved here verbatim, not duplicated ...
)

def is_political_topic(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(kw in low for kw in POLITICAL_KEYWORDS)
```
```python
# bias_blindspot.py
from political_topic import is_political_topic as _is_political_topic

# Persist what was actually computed:
client.table("stories").update({
    "bias_distribution": res["bias_distribution"],
    "bias_coverage_pct": res["bias_coverage_pct"],
    "is_blindspot": res["is_blindspot"],
    "is_political_topic": res["is_political"],   # <- now stored, not just returned
    "blindspot_checked_at": now,
}).eq("id", sid).execute()
```
```python
# phase6_api.py — read the stored column instead of recomputing:
from political_topic import is_political_topic as _is_political_topic  # kept only
    # as a fallback for stories computed before this migration; new code paths
    # should prefer the stored column directly:

st["is_political_topic"] = st.get("is_political_topic")   # read straight from DB
```
Requires one new column: `alter table stories add column is_political_topic boolean;`
(add to a new migration, backfill via one `bias_blindspot.py` run after deploying).

**Why this is better:** One file owns the keyword list and the logic; both consumers
import it. It is now structurally impossible for the pipeline's understanding of
"is this story political" to disagree with the API's, because there's only one
implementation. It also means `/pipeline-health`'s topic-gate stats and
`bias_blindspot.py`'s own run stats are guaranteed to be counting the same thing,
which they currently are only by coincidence (both happen to run the same
copy-pasted code, today).

**Instructions for Claude Code:**
```
Create naijapulse-engine/political_topic.py with POLITICAL_KEYWORDS and
is_political_topic() as the single shared implementation, moved (not copied) from
bias_blindspot.py. Update bias_blindspot.py to import from it, and to persist
is_political (as is_political_topic) into the stories table update it already does.
Add a migration for the new is_political_topic boolean column on stories. Update
phase6_api.py to import from the same shared module AND prefer reading the stored
column over recomputing when present. Run bias_blindspot.py once after migrating to
backfill is_political_topic on all existing stories. Acceptance test: confirm
phase6_api.py's /stories and /pipeline-health is_political_topic values match what's
now stored in the stories table exactly, for every story.
```

---

### Finding 12: `requirements.txt` is missing dependencies the code actually imports

**Where:** `requirements.txt`:
```
supabase
python-dotenv
feedparser
trafilatura
fastapi
uvicorn
```
Actual third-party imports found across the codebase (verified by grep):
`bs4` (BeautifulSoup), `requests`, `numpy`, `hdbscan` — none of which are listed.

**Why this matters:** The README's own "Getting started" section says step 1 is
`pip install -r requirements.txt`. Following that instruction exactly, verbatim, on a
clean machine, produces a `ModuleNotFoundError` the first time `ingest_supabase.py`
imports `bs4` or `requests`, and again when `cluster_stories.py` imports `hdbscan` or
`numpy`. This is the first thing anyone new to the repo — including a future
version of you, or an actual hiring manager cloning it to evaluate your work — would
hit, within seconds of following your own documented setup steps.

**Fix:**
```
# Project dependencies for NaijaPulse Engine
supabase==2.*
python-dotenv==1.*
feedparser==6.*
trafilatura==1.*
beautifulsoup4==4.*
requests==2.*
numpy==1.*
hdbscan==0.8.*

# Phase 6 - read-only FastAPI query/API layer
fastapi==0.*
uvicorn==0.*
```
(Pin to whatever major/minor versions are actually installed in your working venv
right now — run `pip freeze` inside the working venv and use those exact versions as
the floor, rather than guessing.)

**Why this is better:** `pip install -r requirements.txt` now actually installs
everything the code needs to run, matching what the README promises. Version pins
(even loose major-version pins) also mean a fresh install six months from now can't
silently pull a breaking new major version of `fastapi` or `supabase-py` and change
behavior without anyone noticing why.

**Instructions for Claude Code:**
```
Run `pip freeze` inside the project's actual working venv, and use it to add the
missing packages (beautifulsoup4, requests, numpy, hdbscan) to requirements.txt with
version pins matching what's actually installed and working today. Then, in a genuinely
clean virtual environment (not the existing working one), run
`pip install -r requirements.txt` followed by the full pipeline
(`run_pipeline.py --all`) and confirm it completes with zero ModuleNotFoundError.
This is the real acceptance test — not just eyeballing the file.
```

---

### Finding 13: Two parallel ingestion scripts exist; one is dead, unreferenced, and is the source of Finding 1

**Where:** `naijapulse-engine/ingest.py` vs. `naijapulse-engine/ingest_supabase.py`.
Already covered in depth under Finding 1 — flagged separately here because it's also,
independently, a code-hygiene problem: `ingest.py` is confirmed to have zero
references anywhere else in the codebase (verified via repo-wide grep), it duplicates
most of `ingest_supabase.py`'s logic with known drift (its own header comment admits
"keep that fix in sync here if you ever actually use this fallback" — an
acknowledgment that the two copies are already out of sync and require manual
discipline to keep aligned, which is exactly the kind of maintenance burden a
"reduce bloat" pass should eliminate).

**Fix:** Already specified under Finding 1 — delete `ingest.py` as part of that fix,
don't keep it around as an unused fallback.

**Instructions for Claude Code:** Covered by Finding 1's instructions — no separate
action needed here beyond confirming, before deletion, that the grep for references
truly returns zero results in your current checkout (repos drift; re-verify rather
than trusting this document's snapshot).

---

## What's already genuinely good (keep doing this)

Worth being explicit about, since a "world-class review" isn't just a list of
problems:

- **The bias-tagging near-duplicate lean detector** (`_collision_key` /
  `load_and_normalize_source_bias` in `bias_blindspot.py`) is well-designed: it
  surfaces near-duplicate category values for human confirmation instead of silently
  auto-merging them, which is exactly the right call for data that feeds a
  user-facing bias claim.
- **The directional-only blindspot comparison** (`_evaluate_blindspot`) correctly
  separates non-directional categories (`mixed`/`independent`) from directional ones
  (`pro_government`/`anti_government`) — this was a real bug earlier in the project's
  history and the current implementation is the corrected, right version.
- **The minimum-sample gate and topic-relevance gate**, evaluated in the correct
  order (topic gate before blindspot evaluation, not after), match exactly what a
  rigorous statistical approach requires: don't make a claim you don't have the
  data to support, and don't evaluate a claim that doesn't apply to the topic at all.
- **The browser-UA feed-parsing fix** in `ingest_supabase.py` (fetching with a real
  browser User-Agent before handing bytes to `feedparser`, rather than letting
  `feedparser` fetch directly) is a genuinely correct diagnosis and fix for a real
  problem (several Nigerian outlets block feedparser's default UA).
- **The acceptance-test discipline across every phase** — hand-labeled purity sets,
  explicit before/after numbers, refusing to mark a phase done without real evidence
  — is the actual hard part of building something like this, and it's been done
  consistently. Every fix in this document should be held to that same standard:
  don't mark any of the above done without the acceptance test's real output.

---

## Recommended execution order

Don't do all of this at once. In order, and why:

1. **Finding 1 (public repo leak)** — first, today, regardless of anything else.
   This is live exposure, not a design debt.
2. **Findings 3–6 (correctness bugs)** — second. These are cheap, mechanical fixes
   that remove real landmines (silent failure, misleading flags, broken safety net)
   before you build anything more on top of this pipeline.
3. **Findings 7–10 (performance)** — third. Not urgent at current scale, but do
   them before volume grows rather than after something gets slow in production and
   you're debugging it under pressure.
4. **Findings 11–13 (code quality)** — fourth, ongoing. Lowest urgency, but the
   duplication fix (11) genuinely reduces future-drift risk and is worth doing before
   the keyword list gets tuned again.
5. **Finding 2 (RLS / service-role) — last, deliberately, not skipped.** This is a
   confirmed, tracked deferral, not a gap that got missed. It must be fully resolved
   before `phase6_api.py` or any successor is deployed anywhere reachable from the
   public internet, and it is the very last thing to do before that specific step —
   not before continued local development. Do not implement it early on a "might as
   well while I'm in here" impulse either: RLS lockdown makes routine DB writes
   during development more friction, which is exactly the cost Adegoke is choosing
   to defer paying until it's actually required.

Only Finding 1 blocks everything else immediately. Findings 3–13 should be
interleaved with forward progress, not treated as a mandatory big-bang rewrite.
Finding 2 blocks nothing right now — it blocks one specific future event (public
deployment) and should stay untouched until that event is imminent.
