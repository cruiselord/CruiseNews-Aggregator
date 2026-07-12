import os
from dotenv import load_dotenv
from supabase import create_client
load_dotenv()
c = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
sid = 'c6c45f9f-2fcc-4b8b-9c7d-a14f79ca55d7'
rows = c.table('articles').select('id, title, canonical_article_id, cluster_id').eq('cluster_id', sid).execute().data or []
print(f"story {sid} members:")
canon = [r for r in rows if r['canonical_article_id'] is None]
dup = [r for r in rows if r['canonical_article_id'] is not None]
print(f"  total members = {len(rows)}  (canonical={len(canon)}, duplicates={len(dup)})")
for r in rows:
    tag = 'CANON' if r['canonical_article_id'] is None else 'dup->' + r['canonical_article_id'][:8]
    print(f"    [{tag:14}] {r['title'][:60]}")
s = c.table('stories').select('article_count, bias_distribution').eq('id', sid).execute().data[0]
print("stored article_count:", s['article_count'], " bias:", s['bias_distribution'])
