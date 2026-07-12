"""Choose a conservative clustering threshold via single-linkage over cosine.

Purity rewards UNDER-merging: a singleton is always 100% pure, so the only
thing that hurts is putting two UNRELATED articles in one cluster. We therefore
want the highest cosine threshold that still joins genuine same-event pairs.

For each candidate threshold T we build the cosine>=T graph over ALL canonical
articles, take connected components as clusters, then compute predicted purity
on the 60 hand-labelled sample (restricted to labelled articles) and report the
largest cluster size. Goal: largest cluster small AND predicted purity >= 0.80.
"""
import os
import json
import numpy as np
from collections import defaultdict, Counter
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
n = len(items)
X = np.stack([v for _, v in items])
norms = np.linalg.norm(X, axis=1, keepdims=True)
norms[norms == 0] = 1.0
Xn = X / norms
Sim = Xn @ Xn.T  # cosine since normalised
np.fill_diagonal(Sim, 0.0)

# hand labels (ground truth) for the 60-article sample
labels = json.load(open("purity_labels.json"))
aid2story = labels  # {article_id: true_story}
# only those present in the embedding set
lab_ids = [i for i, _ in items if i in aid2story]
print(f"canonical embedded: {n}; labelled-in-sample: {len(lab_ids)}")


def components(T):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # threshold edges (upper triangle)
    for i in range(n):
        row = Sim[i]
        # vectorised: all j>i with sim>=T
        js = np.where(row[i + 1:] >= T)[0]
        for d in js:
            union(i, i + 1 + d)
    comp = defaultdict(list)
    for i in range(n):
        comp[find(i)].append(i)
    return list(comp.values())


def purity_on_sample(clusters):
    # map each labelled article -> cluster id
    idx = {i: k for k, (i, _) in enumerate(items)}
    cl_of = {}
    for ci, comp in enumerate(clusters):
        for node in comp:
            aid = items[node][0]
            cl_of[aid] = ci
    total = 0
    dom = 0
    largest = 0
    for comp in clusters:
        members = [items[node][0] for node in comp]
        largest = max(largest, len(members))
        lab = [aid2story[a] for a in members if a in aid2story]
        if not lab:
            continue
        cnt = Counter(lab)
        total += len(lab)
        dom += cnt.most_common(1)[0][1]
    return dom / total if total else 1.0, total, largest, len(clusters)


print(f"\n{'T':>5} {'purity':>7} {'#clusters':>9} {'max_size':>8}")
for T in (0.80, 0.82, 0.84, 0.86, 0.88, 0.90, 0.92):
    cl = components(T)
    p, tot, mx, nc = purity_on_sample(cl)
    print(f"{T:>5.2f} {p:>7.3f} {nc:>9} {mx:>8}")
