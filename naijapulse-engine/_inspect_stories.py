import os
from dotenv import load_dotenv
from supabase import create_client
from collections import Counter
load_dotenv()
c = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

stories = c.table('stories').select('id, article_count, representative_title, first_seen_at, last_updated_at').execute().data or []
ac = [s['article_count'] for s in stories]
print("stories:", len(stories))
print("article_count distribution:", dict(sorted(Counter(ac).items())))
print("stories with >=2 canonical members:", sum(1 for x in ac if x>=2))
print("stories with 1 canonical member:", sum(1 for x in ac if x==1))

un = c.table('articles').select('id', count='exact').is_('canonical_article_id','null').is_('cluster_id','null').execute()
print("canonical articles still unassigned:", un.count)
dups = c.table('articles').select('id, canonical_article_id, cluster_id').not_.is_('canonical_article_id','null').execute().data or []
print("total duplicates:", len(dups), "duplicates with cluster_id:", sum(1 for d in dups if d['cluster_id']))
print()
print("=== 14 sample stories (size / date / title) ===")
for s in sorted(stories, key=lambda x: -x['article_count'])[:14]:
    print(f"  [{s['article_count']:2}] {str(s['first_seen_at'])[:10]}  {s['representative_title'][:72]}")
