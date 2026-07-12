import os
from dotenv import load_dotenv
from supabase import create_client
import numpy as np, cluster_stories as cs
load_dotenv()
c = cs.make_client()
sid = '24f63464-...'  # placeholder; we resolve below
# find the cluster that is huge
stories = c.table('stories').select('id, article_count, centroid_embedding').execute().data or []
big = max(stories, key=lambda s: s['article_count'])
sid = big['id']
print(f"BIGGEST story: {sid}  article_count={big['article_count']}")
cent = cs.to_vec(big['centroid_embedding'])
members = c.table('articles').select('id, title, source_id, published_at').eq('cluster_id', sid).execute().data or []
# get member embeddings and cosine to centroid
embs = cs.fetch_embeddings(c, [m['id'] for m in members])
print(f"\nmembers ({len(members)}), cosine-to-centroid:")
sims = []
for m in sorted(members, key=lambda x: x['title']):
    v = embs.get(m['id'])
    s = cs.cosine(v, cent) if v is not None else 0.0
    sims.append(s)
    print(f"  cos={s:.3f} | {m['title'][:75]}")
print(f"\nmin cos to centroid: {min(sims):.3f}  max: {max(sims):.3f}  mean: {np.mean(sims):.3f}")
# how many OTHER stories have a centroid within 0.78 of THIS centroid?
print("\nother stories' cosine to this centroid (top 10):")
others = [s for s in stories if s['id'] != sid]
oc = []
for s in others:
    oc.append((cs.cosine(cs.to_vec(s['centroid_embedding']), cent), s['article_count'], s['id'][:8]))
for sim, ac, i in sorted(oc, reverse=True)[:10]:
    print(f"  cos={sim:.3f} ac={ac} {i}")
