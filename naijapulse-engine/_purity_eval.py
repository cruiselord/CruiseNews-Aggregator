"""Acceptance test 2 - cluster purity evaluation (reads LIVE cluster assignments).

Computes cluster purity of the Phase-4 clustering against the hand-labelled
ground-truth set in purity_labels.json. This version reads the CURRENT cluster_id
for each labelled article directly from the live `articles` table, so it is
correct after ANY re-cluster (it does NOT rely on a stale snapshot in
purity_sample.json, which the previous version did).

    purity(cluster) = (size of dominant true-story in cluster) / cluster_size
    overall purity  = sum over clusters of dominant_count / total_labelled

A NULL cluster_id (HDBSCAN noise point) is treated as its OWN singleton cluster,
which is the correct purity handling: a singleton is always 100% pure.

Labels file (purity_labels.json) format:
    { "<article_id>": "<true_story_id>", ... }
Articles sharing a true_story_id are the "same real-world story".

Acceptance target (PROGRESS.md Phase 4): overall purity >= 0.80.
"""
import os
import sys
import json
from collections import defaultdict, Counter

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

TARGET = 0.80


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "purity_labels.json"
    with open(path, encoding="utf-8") as f:
        labels = json.load(f)

    if not labels:
        print("no labels found")
        return 2

    c = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    # fetch CURRENT cluster_id for every labelled article.
    # Chunked: a single .in_() over all label ids can blow past PostgREST's
    # query-size ceiling (same class of bug as Finding 7 in ingest_supabase).
    ids = list(labels.keys())
    rows = []
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        rows.extend(
            (c.table("articles").select("id, cluster_id").in_("id", batch).execute().data) or []
        )
    cid_by_aid = {}
    for r in rows:
        cid = r.get("cluster_id")
        # noise / unclustered -> its own singleton cluster
        cid_by_aid[r["id"]] = cid if cid else f"__noise__{r['id']}"

    by_cluster = defaultdict(list)
    missing = 0
    for aid, true_story in labels.items():
        cid = cid_by_aid.get(aid)
        if cid is None:
            missing += 1
            continue
        by_cluster[cid].append(true_story)

    total = 0
    dominant_total = 0
    print(f"\n{'cluster':16} {'size':>4} {'dom':>4} {'purity':>7}  distinct_true_stories")
    print("-" * 74)
    impure = []
    for cid, stories in sorted(by_cluster.items(), key=lambda kv: -len(kv[1])):
        cnt = Counter(stories)
        dominant_n = cnt.most_common(1)[0][1]
        size = len(stories)
        p = dominant_n / size
        total += size
        dominant_total += dominant_n
        distinct = len(cnt)
        flag = "  <-- IMPURE" if p < TARGET else ""
        label = cid[:14] if not cid.startswith("__noise__") else "noise:" + cid[8:20]
        print(f"{label:16} {size:>4} {dominant_n:>4} {p:>7.2f}  distinct={distinct}{flag}")
        if p < TARGET:
            impure.append((cid, size, p, distinct))

    overall = dominant_total / total if total else 0.0
    print("-" * 74)
    print(f"TOTAL labelled articles : {total}   (not found in DB: {missing})")
    print(f"OVERALL cluster purity  : {overall:.3f}")
    print(f"Acceptance target       : >= {TARGET:.2f}")
    print(f"RESULT                  : {'PASS' if overall >= TARGET else 'FAIL'}")
    if impure:
        print(f"\nImpure clusters ({len(impure)}) dragging purity below target:")
        for cid, size, p, distinct in sorted(impure, key=lambda x: x[2]):
            tag = cid[:14] if not cid.startswith("__noise__") else "noise:" + cid[8:20]
            print(f"  {tag:16} size={size} purity={p:.2f} distinct_true_stories={distinct}")
    return 0 if overall >= TARGET else 1


if __name__ == "__main__":
    sys.exit(main())
