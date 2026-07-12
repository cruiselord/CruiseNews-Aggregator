"""Predict Phase-4 purity (on the 60 hand-labelled articles) for candidate
HDBSCAN param sets, BEFORE touching the live DB.

We cluster ALL canonical articles, then restrict to the 60 labelled ones and
compute cluster purity exactly like _purity_eval.py (dominant true-story per
predicted cluster). Also reports the full-set cluster-size distribution so we
can spot any catch-all.
"""
import os
import json
import numpy as np
import hdbscan
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
titles = {r["id"]: r["title"] for r in rows}
emap = cs.fetch_embeddings(c, ids)
items = [(i, emap[i]) for i in ids if emap.get(i) is not None]
n = len(items)
X = np.stack([v for _, v in items])
norms = np.linalg.norm(X, axis=1, keepdims=True)
norms[norms == 0] = 1.0
Xn = X / norms

labels = json.load(open("purity_labels.json"))          # article_id -> true_story
sample = json.load(open("purity_sample.json"))          # the 60 labelled
lab_ids = {r["article_id"] for r in sample}
idx_of = {i: k for k, (i, _) in enumerate(items)}

# which labelled articles are in the embedding set?
lab_in = [a for a in lab_ids if a in idx_of]
print(f"canonical embedded: {n}; labelled & embedded: {len(lab_in)}")


def predict_purity(lab_arr):
    # predicted cluster id per labelled article
    pred = {a: int(lab_arr[idx_of[a]]) for a in lab_in}
    by_pred = defaultdict(list)
    for a in lab_in:
        by_pred[pred[a]].append(labels[a])
    total = 0
    dom = 0
    impure = []
    for p, stories in by_pred.items():
        if p == -1:
            # noise = singleton -> pure
            for s in stories:
                total += 1
                dom += 1
            continue
        cnt = Counter(stories)
        d = cnt.most_common(1)[0][1]
        total += len(stories)
        dom += d
        if d / len(stories) < 0.80:
            impure.append((p, len(stories), round(d / len(stories), 2), len(cnt)))
    return dom / total, impure


def size_dist(lab_arr):
    sz = Counter(lab_arr.tolist())
    real = [v for k, v in sz.items() if k != -1]
    return len(real), sz.get(-1, 0), max(real) if real else 0, sorted(real, reverse=True)[:8]


print(f"\n{'mcs':>3} {'ms':>3} {'eps':>5} {'sel':>4} | {'pred_pur':>8} {'#cl':>4} {'noise':>5} {'max':>4} | impure_clusters")
for mcs in (2, 3, 4):
    for ms in (1, 2):
        cl = hdbscan.HDBSCAN(min_cluster_size=mcs, min_samples=ms,
                             metric="euclidean").fit_predict(Xn)
        p, imp = predict_purity(cl)
        nc, noise, mx, top = size_dist(cl)
        imp_s = "; ".join(f"c{p}({s},{pp},{d})" for p, s, pp, d in imp[:6])
        print(f"{mcs:>3} {ms:>3} {0.0:>5} {'eom':>4} | {p:>8.3f} {nc:>4} {noise:>5} {mx:>4} | {imp_s}")

for eps in (0.05, 0.1, 0.15):
    cl = hdbscan.HDBSCAN(min_cluster_size=2, min_samples=1,
                         cluster_selection_epsilon=eps, metric="euclidean").fit_predict(Xn)
    p, imp = predict_purity(cl)
    nc, noise, mx, top = size_dist(cl)
    imp_s = "; ".join(f"c{p}({s},{pp},{d})" for p, s, pp, d in imp[:6])
    print(f"{2:>3} {1:>3} {eps:>5} {'eom':>4} | {p:>8.3f} {nc:>4} {noise:>5} {mx:>4} | {imp_s}")

for eps in (0.1, 0.15, 0.2):
    cl = hdbscan.HDBSCAN(min_cluster_size=3, min_samples=2,
                         cluster_selection_epsilon=eps,
                         cluster_selection_method="leaf", metric="euclidean").fit_predict(Xn)
    p, imp = predict_purity(cl)
    nc, noise, mx, top = size_dist(cl)
    imp_s = "; ".join(f"c{p}({s},{pp},{d})" for p, s, pp, d in imp[:6])
    print(f"{3:>3} {2:>3} {eps:>5} {'leaf':>4} | {p:>8.3f} {nc:>4} {noise:>5} {mx:>4} | {imp_s}")
