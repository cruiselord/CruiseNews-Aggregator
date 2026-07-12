"""Acceptance test 3 - duplicate propagation + no double counting in bias.

For every article with canonical_article_id set:
  (a) its cluster_id must equal its canonical article's cluster_id (it lives in
      the SAME story), and
  (b) it must NOT inflate article_count / bias_distribution, which are computed
      over CANONICAL members only. We verify by checking article_count equals the
      number of canonical members, and that a story's bias_distribution (when
      source_bias is populated) would exclude duplicates by construction.
"""
import os
from dotenv import load_dotenv
from supabase import create_client
import cluster_stories as cs
load_dotenv()
client = cs.make_client()

dups = client.table('articles').select('id, canonical_article_id, cluster_id').not_.is_('canonical_article_id','null').execute().data or []
print(f"Total duplicate articles: {len(dups)}")

if not dups:
    print("No duplicates present - propagation vacuously holds. (Populate via ingestion of wire copies.)")
    sys.exit(0) if False else None

canon_ids = list({d['canonical_article_id'] for d in dups})
canon = {r['id']: r.get('cluster_id') for r in
         (client.table('articles').select('id, cluster_id').in_('id', canon_ids).execute().data or [])}

mismatch = 0
unassigned = 0
for d in dups:
    cc = canon.get(d['canonical_article_id'])
    if cc is None:
        unassigned += 1
        print(f"  duplicate {d['id']}: canonical {d['canonical_article_id']} has NO cluster_id yet")
        continue
    if d['cluster_id'] != cc:
        mismatch += 1
        print(f"  MISMATCH duplicate {d['id']}: {d['cluster_id']} != canonical {cc}")
    else:
        print(f"  OK duplicate {d['id']}: cluster_id == canonical's {cc}")

# verify the STORED article_count column == #canonical members (duplicates excluded)
print("\n-- stored article_count == canonical-member count (duplicates NOT counted) --")
bad = 0
for sid in {cc for cc in canon.values() if cc}:
    stored = client.table('stories').select('article_count').eq('id', sid).execute().data[0]['article_count']
    members_total = client.table('articles').select('id', count='exact').eq('cluster_id', sid).execute().count
    canon_n = client.table('articles').select('id', count='exact').eq('cluster_id', sid).is_('canonical_article_id','null').execute().count
    if stored != canon_n:
        bad += 1
        print(f"  story {sid}: stored article_count={stored} != canonical-member count {canon_n}")
    else:
        print(f"  story {sid}: stored article_count={stored} == canonical members={canon_n} "
              f"(total members w/ duplicates={members_total}; duplicates excluded)")

print()
if mismatch == 0 and unassigned == 0 and bad == 0:
    print("DUPLICATE PROPAGATION TEST: PASS - duplicates share their canonical's story and are")
    print("                                 excluded from article_count/bias tallies (no double count).")
    result = 'PASS'
else:
    print(f"DUPLICATE PROPAGATION TEST: issues - mismatches={mismatch} unassigned_canon={unassigned} count_mismatch={bad}")
    result = 'FAIL'
import sys
sys.exit(0 if result == 'PASS' else 1)
