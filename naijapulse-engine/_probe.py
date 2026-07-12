import os
from dotenv import load_dotenv
from supabase import create_client
load_dotenv()
c = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
r = c.table('stories').select('id', count='exact').execute()
print('stories count attr:', repr(getattr(r, 'count', 'NONE')), 'data len:', len(r.data or []))
canon = c.table('articles').select('id', count='exact').eq('cluster_id', '00000000-0000-0000-0000-000000000000').execute()
print('articles count attr:', repr(getattr(canon, 'count', 'NONE')))
