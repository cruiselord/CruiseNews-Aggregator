"""Offline analysis to choose HDBSCAN params for the fresh re-cluster.

Pulls ALL canonical article embeddings, runs HDBSCAN with several parameter
sets, and reports cluster-size distributions + how well known same-event pairs
stay together. Goal: pick params that yield NO giant catch-all cluster while
still grouping genuine multi-outlet stories.
"""
import os
import numpy as np
import hdbscan
from collections import Counter
from dotenv import load_dotenv
from supabase import create_client
import cluster_stories as cs

load_dotenv()
c = cs.make_client()

rows = (c.table("articles")
        .select("id, title")
        .is_("canonical_article_id", "null")
        .execute().data) or []
ids = [r["id"] for r in rows]
emap = cs.fetch_embeddings(c, ids)
items = [(i, emap[i]) for i in ids if emap.get(i) is not None]
X = np.stack([v for _, v in items])
norms = np.linalg.norm(X, axis=1, keepdims=True)
norms[norms == 0] = 1.0
Xn = X / norms
print(f"canonical articles with embeddings: {len(items)}")

# known same-event pairs that SHOULD be clustered together (from hand labels)
SHOULD_PAIR = [
    ("argentina_switzerland_wc",),  # placeholder; filled below
]
# map article_id -> true story from purity_labels.json
import json
labels = json.load(open("purity_labels.json"))
aid2story = {aid: s for aid, s in labels.items()}
# build groups present in the DB sample
from collections import defaultdict
story2aids = defaultdict(list)
for aid, s in aid2story.items():
    story2aids[s].append(aid)
multi = {s: aids for s, aids in story2aids.items() if len(aids) >= 2}
print(f"\nhand-label stories with >=2 members (used to check pair retention): {len(multi)}")


def run(mcs, ms, eps, sel="eom"):
    cl = hdbscan.HDBSCAN(min_cluster_size=mcs, min_samples=ms,
                         cluster_selection_epsilon=eps,
                         cluster_selection_method=sel, metric="euclidean")
    lab = cl.fit_predict(Xn)
    sizes = Counter(lab.tolist())
    n_clusters = len([k for k in sizes if k != -1])
    n_noise = sizes.get(-1, 0)
    big = [(k, n) for k, n in sizes.items() if k != -1 and n >= 5]
    # pair retention
    retained = 0
    total_pairs = 0
    for s, aids in multi.items():
        aids = [a for a in aids if a in aid2story]  # all are
        # find cluster label for each aid
        idx = {i: j for j, (i, _) in enumerate(items)}
        labs = [lab[idx[a]] for a in aids if a in idx]
        labs = [l for l in labs if l != -1]
        # count within-cluster pairs
        from itertools import combinations
        for a, b in combinations(aids, 2):
            if a in idx and b in idx:
                total_pairs += 1
                if lab[idx[a]] != -1 and lab[idx[a]] == lab[idx[b]]:
                    retained += 1
    print(f"  mcs={mcs} ms={ms} eps={eps} sel={sel}: "
          f"clusters={n_clusters} noise={n_noise} max={max(sizes.values()) if sizes else 0} "
          f"big(>=5)={big} pair_retention={retained}/{total_pairs}")
    return lab, sizes


print("\n--- parameter sweep ---")
for mcs in (2, 3, 4):
    for ms in (1, 2):
        run(mcs, ms, 0.0)
for eps in (0.05, 0.1, 0.15):
    run(2, 1, eps)
for eps in (0.1, 0.15, 0.2):
    run(3, 2, eps, sel="leaf")
