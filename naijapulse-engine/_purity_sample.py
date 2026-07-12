"""Acceptance test 2 - cluster purity (hand-verified ground truth).

Pulls a representative, cluster-proportional sample of canonical articles that
already have a cluster_id, dumps them to purity_sample.json so they can be
hand-labelled into true stories, and prints them for reading.

Sampling is proportional to cluster size so the big "catch-all" clusters are
represented in the sample (they are the main failure mode to watch).

Run this first, read the output / purity_sample.json, then edit purity_labels.json
with your true-story label per article_id and run _purity_eval.py.
"""
import os
import sys
import json
import math
from collections import defaultdict
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SAMPLE_SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def main() -> None:
    c = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    # all canonical (non-duplicate) articles that have been clustered
    rows = (c.table("articles")
            .select("id, title, source_id, cluster_id, published_at")
            .is_("canonical_article_id", "null")
            .not_.is_("cluster_id", "null")
            .execute()
            .data) or []
    print(f"total canonical clustered articles: {len(rows)}")

    by_cluster = defaultdict(list)
    for r in rows:
        by_cluster[r["cluster_id"]].append(r)

    # proportional sample: each cluster contributes ceil(size * SAMPLE_SIZE/total)
    total = len(rows)
    sample = []
    for cid, members in by_cluster.items():
        k = max(1, math.ceil(len(members) * SAMPLE_SIZE / total))
        k = min(k, len(members))
        # pick the first k by published_at for determinism (and so the earliest
        # members - usually the most representative - are included)
        members_sorted = sorted(members, key=lambda x: x.get("published_at") or "")
        sample.extend(members_sorted[:k])

    # cap at SAMPLE_SIZE if rounding pushed us over
    sample = sample[:SAMPLE_SIZE]

    payload = [{
        "article_id": r["id"],
        "cluster_id": r["cluster_id"],
        "title": r["title"],
        "source_id": r["source_id"],
        "published_at": r.get("published_at"),
    } for r in sample]
    with open("purity_sample.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"wrote {len(payload)} articles to purity_sample.json "
          f"({len(by_cluster)} clusters represented)\n")
    for i, r in enumerate(payload):
        print(f"{i:3} | cid={r['cluster_id'][:8]} | {r['title'][:90]}")


if __name__ == "__main__":
    main()
