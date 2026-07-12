"""Fresh full HDBSCAN re-cluster of all canonical articles (Phase 4 fix).

The previous clustering accumulated a 56-member catch-all cluster because Stage A
attached every HDBSCAN 'noise' singleton to the nearest OPEN story at cosine >=
0.78, which drifted a blob of loosely-related articles into a monster.

This script does a CLEAN full re-cluster and removes that failure mode:

  1. Reset cluster_id = NULL on every article; delete all existing stories.
  2. Run HDBSCAN (min_cluster_size=2, min_samples=1, euclidean on L2-normalised
     vectors) over ALL canonical articles -> tight same-event clusters.
  3. Every remaining unclustered (noise) canonical becomes its own 1-member story,
     so every article belongs to exactly one story. This also prevents Stage A on
     the next run from re-attaching these as 'unassigned' and re-growing a blob.
  4. Propagate cluster_id to duplicates (Stage C) and compute bias (Stage E).

Idempotent: safe to re-run. After this, run _purity_eval.py to confirm purity.
"""
import os
import logging
import numpy as np
import hdbscan
from dotenv import load_dotenv
from supabase import create_client
import cluster_stories as cs

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# zero-uuid used as a no-op filter so we touch ALL rows
_ZERO = "00000000-0000-0000-0000-000000000000"


def main() -> None:
    c = cs.make_client()
    now = cs._now_iso()

    # 1: reset
    logger.info("Resetting all cluster_id to NULL and clearing stories table")
    c.table("articles").update({"cluster_id": None}).neq("id", _ZERO).execute()
    c.table("stories").delete().neq("id", _ZERO).execute()

    # 2: load all canonical + embeddings
    canon = (c.table("articles")
             .select("id, title, source_id, published_at")
             .is_("canonical_article_id", "null")
             .execute().data) or []
    ids = [r["id"] for r in canon]
    emap = cs.fetch_embeddings(c, ids)
    items = []
    for r in canon:
        v = emap.get(r["id"])
        if v is None:
            logger.warning("no embedding for %s; skipping", r["id"])
            continue
        r["vec"] = v
        items.append(r)
    logger.info("loaded %d canonical articles with embeddings", len(items))

    # HDBSCAN over ALL canonical (same params as Stage B)
    X = np.stack([a["vec"] for a in items])
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=cs.HDBSCAN_MIN_CLUSTER_SIZE,
        min_samples=cs.HDBSCAN_MIN_SAMPLES,
        metric="euclidean",
    )
    labels = clusterer.fit_predict(Xn)

    by_label = {}
    for art, lab in zip(items, labels):
        if lab == -1:
            continue
        by_label.setdefault(int(lab), []).append(art)

    created = []
    for lab, members in sorted(by_label.items()):
        vecs = np.stack([m["vec"] for m in members])
        centroid = vecs.mean(axis=0)
        members_sorted = sorted(members, key=lambda m: cs._parse_ts(m.get("published_at")))
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
            "centroid_embedding": cs._vector_literal(centroid),
            "status": "active",
        }
        res = c.table("stories").insert(row).execute()
        sid = res.data[0]["id"]
        aids = [m["id"] for m in members]
        c.table("articles").update({"cluster_id": sid}).in_("id", aids).execute()
        created.append(sid)
        logger.info("created story %s with %d member(s)", sid, len(members))

    # 3: singleton stories for any still-unclustered canonical
    unclustered = (c.table("articles")
                   .select("id, title, source_id, published_at")
                   .is_("canonical_article_id", "null")
                   .is_("cluster_id", "null")
                   .execute().data) or []
    for r in unclustered:
        v = emap.get(r["id"])
        row = {
            "representative_title": (r.get("title") or "")[:300],
            "first_seen_at": r.get("published_at"),
            "last_updated_at": now,
            "article_count": 1,
            "bias_distribution": None,
            "is_blindspot": False,
            "centroid_embedding": cs._vector_literal(v) if v is not None else None,
            "status": "active",
        }
        res = c.table("stories").insert(row).execute()
        sid = res.data[0]["id"]
        c.table("articles").update({"cluster_id": sid}).eq("id", r["id"]).execute()
        created.append(sid)
    logger.info("created %d singleton story/stories for unclustered canonical", len(unclustered))

    # 4: propagate + bias
    cs.stage_c(c)
    cs.stage_e(c, set(created))

    # report
    total_stories = c.table("stories").select("id", count="exact").execute().count
    active = c.table("stories").select("id", count="exact").eq("status", "active").execute().count
    clustered = (c.table("articles").select("id", count="exact")
                 .not_.is_("cluster_id", "null").execute().count)
    unassigned = (c.table("articles").select("id", count="exact")
                  .is_("canonical_article_id", "null").is_("cluster_id", "null").execute().count)
    logger.info("DONE: stories=%d active=%d clustered_articles=%d unassigned_canonical=%d",
                total_stories, active, clustered, unassigned)
    print(f"\nRe-cluster complete: {total_stories} stories, {clustered} articles clustered, "
          f"{unassigned} canonical unassigned.")


if __name__ == "__main__":
    main()
