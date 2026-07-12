"""Acceptance test 1 - continuity (precise version).

Manufacture a NEW canonical article that is a near-copy of a member of an existing
ACTIVE story, but published 2 days LATER, and leave its cluster_id NULL. Re-run
clustering. Confirm the manufactured article ATTACHES to the SAME pre-existing
story via Stage A (rather than spawning its own brand-new story).

We do NOT assert the global story count is unchanged: a re-run also re-processes
the previously-unclustered "noise" singletons, which may legitimately attach to or
form other stories. We only assert that THIS article went to the existing story.
"""
import os, sys, uuid
from dotenv import load_dotenv
from supabase import create_client
import cluster_stories as cs
load_dotenv()

client = cs.make_client()
now = cs._now_iso()

existing_ids = {s['id'] for s in (client.table('stories').select('id').execute().data or [])}
before = len(existing_ids)
print(f"Stories before re-run: {before}")

# pick an active story with >=2 canonical members
stories = client.table('stories').select('id, article_count').gte('article_count', 2).eq('status','active').execute().data or []
assert stories, "need an active story with >=2 members"
target = stories[0]['id']
print(f"Target (pre-existing) story: {target}")

# pick a canonical member and copy its text + embedding
member = client.table('articles').select('id, title, summary, source_id').eq('cluster_id', target).is_('canonical_article_id','null').limit(1).execute().data[0]
emb = client.table('embeddings').select('vector').eq('article_id', member['id']).eq('model', cs.EMBED_MODEL).execute().data[0]['vector']

later = '2026-07-14T09:00:00+00:00'   # 2 days after the freshest data
new_id = str(uuid.uuid4())
client.table('articles').insert({
    'id': new_id, 'source_id': member['source_id'],
    'url': f"https://manufactured.test/{new_id}",
    'title': member['title'], 'summary': member.get('summary') or '',
    'published_at': later, 'fetched_at': now,
}).execute()
client.table('embeddings').insert({'article_id': new_id, 'model': cs.EMBED_MODEL, 'vector': emb}).execute()
print(f"Manufactured article {new_id} (published {later}) as later-day near-copy of member {member['id']}")

# re-run clustering
cs.run_clustering(client)

assigned = client.table('articles').select('cluster_id').eq('id', new_id).execute().data[0]['cluster_id']
after_ids = {s['id'] for s in (client.table('stories').select('id').execute().data or [])}
after = len(after_ids)
new_stories = after_ids - existing_ids
print(f"Manufactured article cluster_id = {assigned}")
print(f"Stories after re-run: {after}  (delta +{after - before}; {len(new_stories)} newly created this run)")

attached_to_existing = (assigned == target) and (target in existing_ids)
no_own_story = assigned in existing_ids   # it did NOT get a freshly-spawned story
print()
if attached_to_existing and no_own_story:
    print("CONTINUITY TEST: PASS - newer article attached to the EXISTING story via Stage A;")
    print("                     it did not spawn its own new story.")
    result = 'PASS'
else:
    print("CONTINUITY TEST: FAIL")
    result = 'FAIL'

# cleanup manufactured rows, then restore the target story's tallies
client.table('embeddings').delete().eq('article_id', new_id).execute()
client.table('articles').delete().eq('id', new_id).execute()
cs._recompute_story(client, target, now)
print(f"(cleaned up manufactured article; target story tallies restored)")
sys.exit(0 if result == 'PASS' else 1)
