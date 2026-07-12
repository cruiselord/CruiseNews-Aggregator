#!/usr/bin/env python3
"""
Phase 4 - Story clustering for the NaijaPulse core engine.

Groups canonical (non-duplicate) articles into "stories" - clusters of articles
covering the same real-world event - and writes results to the `stories` table.
Duplicates inherit their canonical article's story via cluster_id.

DESIGN PRINCIPLE (continuity):
    We NEVER re-cluster the whole historical article table. On every run we only
    touch canonical articles that have no cluster_id yet (canonical_article_id IS
    NULL AND cluster_id IS NULL). Stage A attaches new articles to EXISTING open
    stories (so an ongoing event keeps one story); only articles that match no
    open story fall through to Stage B (HDBSCAN discovery of brand-new stories).

ALGORITHM (single pipeline step, in order):
    Stage A - match against existing OPEN stories (active, last_updated_at <=5d):
              cosine(article.embedding, story.centroid) >= 0.78 -> attach.
    Stage B - HDBSCAN over the leftovers (min_cluster_size=2, min_samples=1) to
              discover NEW stories.
    Stage C - propagate cluster_id from each canonical article to its duplicates.
    Stage D - close stories with no new article in 5+ days (keep Stage A fast).
    Stage E - bias_distribution (per ownership_lean, canonical members only) +
              is_blindspot flag.

COUNTING CONVENTION (documented for tuning):
    * article_count      = number of CANONICAL member articles (dedup collapsed
                           wire copies, so they are not independent coverage).
    * bias_distribution  = count of CANONICAL member articles per ownership_lean
                           (duplicates are excluded, so they can never inflate /
                           double-count the lean tally - see acceptance test 3).
    * centroid_embedding = mean of ALL member article embeddings (canonical +
                           duplicates; duplicates barely shift it and it stays in
                           the same vector space as the article embeddings).

Usage:
    ./venv/bin/python cluster_stories.py
"""

import os
import sys
import logging
import datetime
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import hdbscan
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# --- thresholds (from the spec) ---
STAGE_A_THRESHOLD = 0.78      # cosine gate for attaching to an existing story
ACTIVE_WINDOW_DAYS = 5        # only compare against / keep open stories <= this old
HDBSCAN_MIN_CLUSTER_SIZE = 2
HDBSCAN_MIN_SAMPLES = 1
BLINDSPOT_MIN_ARTICLES = 3    # a lean with this many while others have 0 -> blindspot


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def make_client():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("Missing SUPABASE_URL / SUPABASE_KEY environment variables")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def to_vec(v):
    """pgvector comes back as a '[...]' string or a list; normalise to np.array."""
    if v is None:
        return None
    if isinstance(v, str):
        return np.fromstring(v.strip("[]"), sep=",", dtype=float)
    return np.asarray(v, dtype=float)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _vector_literal(vec: np.ndarray) -> str:
    """pgvector wants a text literal like '[0.1,0.2,...]', not a JSON array."""
    return "[" + ",".join(f"{x:.8g}" for x in vec) + "]"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_ts(s: Optional[str]) -> datetime.datetime:
    if not s:
        return datetime.datetime.max
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------
def fetch_embeddings(client, ids: List[str]) -> Dict[str, np.ndarray]:
    """Return {article_id: vector} for the given ids at the current model."""
    if not ids:
        return {}
    rows = (client.table("embeddings")
            .select("article_id, vector")
            .in_("article_id", ids)
            .eq("model", EMBED_MODEL)
            .execute()
            .data) or []
    return {r["article_id"]: to_vec(r["vector"]) for r in rows}


def load_unassigned_canonical(client) -> List[Dict]:
    """Canonical articles (canonical_article_id IS NULL) with no cluster_id yet.

    These are the ONLY articles we ever (re)process - this is what keeps story
    continuity intact run over run.
    """
    rows = (client.table("articles")
            .select("id, title, source_id, published_at")
            .is_("canonical_article_id", "null")
            .is_("cluster_id", "null")
            .execute()
            .data) or []
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    emap = fetch_embeddings(client, ids)
    out = []
    for r in rows:
        vec = emap.get(r["id"])
        if vec is None:
            logger.warning("No embedding for canonical article %s; skipping", r["id"])
            continue
        r["vec"] = vec
        out.append(r)
    return out


def load_active_stories(client, now: str) -> List[Dict]:
    """Open stories we are allowed to attach new articles to: active AND
    last_updated_at within the active window. Closed / stale stories are excluded."""
    cutoff = (datetime.datetime.fromisoformat(now) -
              datetime.timedelta(days=ACTIVE_WINDOW_DAYS)).isoformat()
    rows = (client.table("stories")
            .select("id, centroid_embedding, article_count, last_updated_at")
            .eq("status", "active")
            .gte("last_updated_at", cutoff)
            .execute()
            .data) or []
    out = []
    for r in rows:
        c = to_vec(r.get("centroid_embedding"))
        if c is None:
            continue
        r["centroid"] = c
        out.append(r)
    return out


# --------------------------------------------------------------------------
# Stage A - attach to existing open stories
# --------------------------------------------------------------------------
def stage_a(client, unassigned: List[Dict], active: List[Dict], now: str
            ) -> Tuple[Set[str], Set[str]]:
    """Attach unassigned canonical articles to existing open stories.

    Returns (assigned_article_ids, touched_story_ids).
    """
    assigned: Set[str] = set()
    touched: Set[str] = set()
    if not active:
        return assigned, touched

    # best active story per article
    best_story_of: Dict[str, str] = {}
    best_sim_of: Dict[str, float] = {}
    for a in unassigned:
        best_sim = -1.0
        best_story = None
        for s in active:
            sim = cosine(a["vec"], s["centroid"])
            if sim > best_sim:
                best_sim = sim
                best_story = s["id"]
        if best_story is not None and best_sim >= STAGE_A_THRESHOLD:
            best_story_of[a["id"]] = best_story
            best_sim_of[a["id"]] = best_sim

    if not best_story_of:
        logger.info("Stage A: 0 articles matched an open story (threshold %.2f)",
                    STAGE_A_THRESHOLD)
        return assigned, touched

    # group article ids by story, then apply + recompute each story's centroid
    by_story: Dict[str, List[str]] = {}
    for aid, sid in best_story_of.items():
        by_story.setdefault(sid, []).append(aid)
        assigned.add(aid)
        touched.add(sid)

    for sid, aids in by_story.items():
        client.table("articles").update({"cluster_id": sid}).in_("id", aids).execute()
        _recompute_story(client, sid, now, touched=False)
        logger.info("Stage A: attached %d article(s) to story %s", len(aids), sid)

    # touched stories need a last_updated_at bump + bias recompute (done in _recompute)
    return assigned, touched


def _recompute_story(client, sid: str, now: str, touched: bool = True) -> None:
    """Recompute a story's centroid + article_count + last_updated_at.

    centroid      = mean of ALL member embeddings (canonical + duplicates)
    article_count = number of CANONICAL member articles
    """
    members = (client.table("articles")
               .select("id")
               .eq("cluster_id", sid)
               .execute()
               .data) or []
    member_ids = [m["id"] for m in members]
    canon = (client.table("articles")
             .select("id", count="exact")
             .eq("cluster_id", sid)
             .is_("canonical_article_id", "null")
             .execute())
    canon_count = canon.count if hasattr(canon, "count") and canon.count is not None else len(
        (canon.data or []))

    emap = fetch_embeddings(client, member_ids)
    vecs = [v for v in emap.values() if v is not None]
    update = {"article_count": canon_count, "last_updated_at": now}
    if vecs:
        centroid = np.mean(np.stack(vecs), axis=0)
        update["centroid_embedding"] = _vector_literal(centroid)
    client.table("stories").update(update).eq("id", sid).execute()


# --------------------------------------------------------------------------
# Stage B - discover new stories among the leftovers
# --------------------------------------------------------------------------
def stage_b(client, leftovers: List[Dict], now: str) -> List[str]:
    """HDBSCAN over articles that matched no open story. Returns new story ids."""
    created: List[str] = []
    if len(leftovers) < HDBSCAN_MIN_CLUSTER_SIZE:
        logger.info("Stage B: only %d leftover(s) - nothing to discover (need >= %d).",
                    len(leftovers), HDBSCAN_MIN_CLUSTER_SIZE)
        return created

    X = np.stack([a["vec"] for a in leftovers])
    # normalise so euclidean distance ~ angular (cosine) distance
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE,
        min_samples=HDBSCAN_MIN_SAMPLES,
        metric="euclidean",
    )
    labels = clusterer.fit_predict(Xn)

    by_label: Dict[int, List[Dict]] = {}
    for art, lab in zip(leftovers, labels):
        if lab == -1:
            continue  # noise: stays a single-source story until something joins it
        by_label.setdefault(int(lab), []).append(art)

    for lab, members in sorted(by_label.items()):
        if len(members) < 1:
            continue
        vecs = np.stack([m["vec"] for m in members])
        centroid = vecs.mean(axis=0)
        # representative = earliest member's title
        members_sorted = sorted(members, key=lambda m: _parse_ts(m.get("published_at")))
        rep_title = members_sorted[0].get("title", "")[:300]
        first_seen = min((m.get("published_at") for m in members if m.get("published_at")),
                         default=None)
        row = {
            "representative_title": rep_title,
            "first_seen_at": first_seen,
            "last_updated_at": now,
            "article_count": len(members),
            "bias_distribution": None,
            "is_blindspot": False,
            "centroid_embedding": _vector_literal(centroid),
            "status": "active",
        }
        res = client.table("stories").insert(row).execute()
        sid = res.data[0]["id"]
        aids = [m["id"] for m in members]
        client.table("articles").update({"cluster_id": sid}).in_("id", aids).execute()
        created.append(sid)
        logger.info("Stage B: created story %s with %d article(s)", sid, len(members))

    return created


# --------------------------------------------------------------------------
# Stage C - propagate cluster_id to duplicates
# --------------------------------------------------------------------------
def stage_c(client) -> int:
    """Every article with canonical_article_id set copies its canonical's cluster_id.
    Must run AFTER Stage A/B so canonicals are already assigned."""
    dups = (client.table("articles")
            .select("id, canonical_article_id, cluster_id")
            .not_.is_("canonical_article_id", "null")
            .execute()
            .data) or []
    if not dups:
        return 0
    canon_ids = list({d["canonical_article_id"] for d in dups})
    canon_rows = (client.table("articles")
                  .select("id, cluster_id")
                  .in_("id", canon_ids)
                  .execute()
                  .data) or []
    canon_cluster = {r["id"]: r.get("cluster_id") for r in canon_rows}

    updated = 0
    for d in dups:
        target = canon_cluster.get(d["canonical_article_id"])
        if target and target != d.get("cluster_id"):
            client.table("articles").update({"cluster_id": target}).eq("id", d["id"]).execute()
            updated += 1
    logger.info("Stage C: propagated cluster_id to %d duplicate article(s)", updated)
    return updated


# --------------------------------------------------------------------------
# Stage D - close stale stories
# --------------------------------------------------------------------------
def stage_d(client, now: str) -> int:
    cutoff = (datetime.datetime.fromisoformat(now) -
              datetime.timedelta(days=ACTIVE_WINDOW_DAYS)).isoformat()
    res = (client.table("stories")
           .update({"status": "closed"})
           .eq("status", "active")
           .lt("last_updated_at", cutoff)
           .execute())
    n = len(res.data) if res.data else 0
    logger.info("Stage D: closed %d stale story/stories (last_updated_at < %s)", n, cutoff)
    return n


# --------------------------------------------------------------------------
# Stage E - bias_distribution + is_blindspot
# --------------------------------------------------------------------------
def stage_e(client, touched: Set[str]) -> None:
    """For each story touched this run, join its CANONICAL member articles to
    source_bias, count per ownership_lean, and flag blindspots."""
    skipped_sources = 0
    for sid in touched:
        members = (client.table("articles")
                   .select("id, source_id")
                   .eq("cluster_id", sid)
                   .is_("canonical_article_id", "null")
                   .execute()
                   .data) or []
        src_ids = [m["source_id"] for m in members if m.get("source_id")]
        if not src_ids:
            bias = {}
        else:
            bias_rows = (client.table("source_bias")
                         .select("source_id, ownership_lean")
                         .in_("source_id", src_ids)
                         .execute()
                         .data) or []
            lean_by_source = {b["source_id"]: b.get("ownership_lean") for b in bias_rows}
            # count DISTINCT sources per lean (a source with N canonical articles
            # about one story still contributes 1 to its lean - no double count)
            lean_sources: Dict[str, Set[str]] = {}
            for m in members:
                s = m.get("source_id")
                lean = lean_by_source.get(s)
                if lean is None:
                    skipped_sources += 1
                    continue
                lean_sources.setdefault(lean, set()).add(s)
            bias = {lean: len(srcs) for lean, srcs in lean_sources.items()}

        # blindspot starting rule: one lean has >=BLINDSPOT_MIN_ARTICLES while
        # every other lean has zero representation (i.e. only one lean present)
        distinct_leans = len(bias)
        max_count = max(bias.values()) if bias else 0
        is_blindspot = bool(distinct_leans == 1 and max_count >= BLINDSPOT_MIN_ARTICLES)

        client.table("stories").update({
            "bias_distribution": bias,
            "is_blindspot": is_blindspot,
        }).eq("id", sid).execute()
        logger.info("Stage E: story %s bias=%s blindspot=%s", sid, bias, is_blindspot)

    if skipped_sources:
        logger.warning("Stage E: %d canonical member article(s) had NO source_bias "
                       "row - skipped from bias_distribution (not fatal).", skipped_sources)


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def run_clustering(client) -> Dict:
    now = _now_iso()
    logger.info("Phase 4 clustering starting at %s", now)

    unassigned = load_unassigned_canonical(client)
    active = load_active_stories(client, now)
    logger.info("Phase 4 in: %d unassigned canonical articles, %d open stories",
                len(unassigned), len(active))

    assigned, touched_a = stage_a(client, unassigned, active, now)
    leftovers = [a for a in unassigned if a["id"] not in assigned]
    created = stage_b(client, leftovers, now)
    touched = touched_a | set(created)

    stage_c(client)
    stage_d(client, now)
    stage_e(client, touched)

    # final tallies for the report
    total_stories = client.table("stories").select("id", count="exact").execute().count
    active_stories = (client.table("stories").select("id", count="exact")
                      .eq("status", "active").execute().count)
    clustered_articles = (client.table("articles").select("id", count="exact")
                           .not_.is_("cluster_id", "null").execute().count)
    blindspots = (client.table("stories").select("id", count="exact")
                  .eq("is_blindspot", True).execute().count)

    stats = {
        "unassigned_in": len(unassigned),
        "open_stories_in": len(active),
        "stage_a_assigned": len(assigned),
        "stage_b_created": len(created),
        "stories_total": total_stories,
        "stories_active": active_stories,
        "articles_clustered": clustered_articles,
        "blindspots": blindspots,
    }
    logger.info("Phase 4 done: %s", stats)
    return stats


def main() -> int:
    client = make_client()
    stats = run_clustering(client)
    print("\n" + "=" * 72)
    print("PHASE 4 - STORY CLUSTERING  (run report)")
    print("=" * 72)
    for k, v in stats.items():
        print(f"  {k:22} {v}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
