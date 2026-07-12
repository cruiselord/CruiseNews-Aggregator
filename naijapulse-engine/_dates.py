import os
from dotenv import load_dotenv
from supabase import create_client
from collections import Counter
load_dotenv()
c = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
rows = c.table('articles').select('published_at, canonical_article_id, cluster_id').execute().data or []
def day(p): return (p or '')[:10]
cnt = Counter(day(r['published_at']) for r in rows)
print("articles by published day:")
for d, n in sorted(cnt.items()):
    print(f"  {d}: {n}")
canon_clustered = Counter(day(r['published_at']) for r in rows
                          if r['canonical_article_id'] is None and r['cluster_id'])
print("\ncanonical CLUSTERED articles by day:")
for d, n in sorted(canon_clustered.items()):
    print(f"  {d}: {n}")
