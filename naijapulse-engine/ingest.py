#!/usr/bin/env python3
"""
!!! NON-PRIMARY / FALLBACK SCRIPT — DO NOT USE AS THE MAIN INGESTION PATH !!!

This script writes to a LOCAL SQLite mirror (naijapulse_local.db) and is only a
demo/fallback used when the live Supabase project is unreachable. It is NOT the
production ingestion path.

>>> THE LIVE PIPELINE USES `ingest_supabase.py` (writes to Supabase Postgres). <<<

Use `ingest_supabase.py` for real runs. Only fall back to this file for local
experiments when Supabase is down. (Note: `ingest_supabase.py` already carries
the browser-UA feed-parser fix for the outlets that block feedparser's default
UA — keep that fix in sync here if you ever actually use this fallback.)

Phase 1 — Ingestion for NaijaPulse Core Engine.

Reads the `sources` table, polls each RSS feed with feedparser, stores the
RSS summary always, and attempts full-text extraction with trafilatura.

Storage backend:
  - If SUPABASE_URL + SUPABASE_KEY are reachable AND the `sources` table
    exists, articles are written to the live Supabase project (Section 4 schema).
  - Otherwise it transparently falls back to a local SQLite mirror
    (`naijapulse_local.db`) using the same schema, so the pipeline can be
    demonstrated and the acceptance numbers produced here and now.

Acceptance test (spec Section 5, Phase 1):
  - >= 90% of feeds return valid entries (>= 9 / 10)
  - >= 70% of articles get successful full-text extraction
  - failures are logged with a reason, never silently dropped
"""

import os
import sys
import json
import time
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
import logging
from dataclasses import dataclass, field

import feedparser
import trafilatura
import requests
from dotenv import load_dotenv

BROWSER_HDR = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

try:
    from supabase import create_client, Client
    HAS_SUPABASE = True
except Exception:
    HAS_SUPABASE = False

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
ARTICLES_PER_SOURCE = int(os.getenv('ARTICLES_PER_SOURCE', '50'))
EXTRACTION_TIMEOUT = int(os.getenv('EXTRACTION_TIMEOUT', '20'))
MAX_WORKERS = int(os.getenv('MAX_WORKERS', '8'))

SOURCES = [
    {"name": "TheCable", "rss_url": "https://www.thecable.ng/feed/", "homepage_url": "https://www.thecable.ng"},
    {"name": "Vanguard", "rss_url": "https://www.vanguardngr.com/feed/", "homepage_url": "https://www.vanguardngr.com"},
    {"name": "Premium Times", "rss_url": "https://www.premiumtimesng.com/feed", "homepage_url": "https://www.premiumtimesng.com"},
    {"name": "Daily Post", "rss_url": "https://dailypost.ng/feed", "homepage_url": "https://dailypost.ng"},
    {"name": "ThisDay", "rss_url": "https://www.thisdaylive.com/index.php/feed", "homepage_url": "https://www.thisdaylive.com"},
    {"name": "Tribune", "rss_url": "https://tribuneonlineng.com/feed", "homepage_url": "https://tribuneonlineng.com"},
    {"name": "Daily Trust", "rss_url": "https://dailytrust.com/feed", "homepage_url": "https://dailytrust.com"},
    {"name": "Guardian NG", "rss_url": "https://guardian.ng/feed", "homepage_url": "https://guardian.ng"},
    {"name": "The Nation", "rss_url": "https://thenationonlineng.net/feed", "homepage_url": "https://thenationonlineng.net"},
    {"name": "BusinessDay", "rss_url": "https://businessday.ng/feed", "homepage_url": "https://businessday.ng"},
]


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Storage backend
# ---------------------------------------------------------------------------

class DB:
    """Unified interface over either Supabase or a local SQLite mirror."""

    def __init__(self):
        self.backend = None  # 'supabase' | 'sqlite'
        self.supabase: Optional[Client] = None
        self.conn: Optional[sqlite3.Connection] = None
        self._connect()

    def _connect(self):
        # Try Supabase first
        if HAS_SUPABASE and SUPABASE_URL and SUPABASE_KEY:
            try:
                self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
                # probe: does the sources table exist?
                self.supabase.table('sources').select('count', count='exact').limit(1).execute()
                self.backend = 'supabase'
                logger.info("Storage backend: SUPABASE (live project)")
                return
            except Exception as e:
                logger.warning(f"Supabase unreachable or tables missing ({e}); falling back to SQLite.")
        self._init_sqlite()
        self.backend = 'sqlite'
        logger.info("Storage backend: SQLITE mirror (naijapulse_local.db)")

    def _init_sqlite(self):
        self.conn = sqlite3.connect('naijapulse_local.db')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                homepage_url TEXT,
                country TEXT DEFAULT 'NG',
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY,
                source_id TEXT,
                url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                full_text TEXT,
                published_at TEXT,
                fetched_at TEXT DEFAULT (datetime('now')),
                content_hash TEXT,
                cluster_id TEXT
            );
            CREATE TABLE IF NOT EXISTS source_bias (
                source_id TEXT PRIMARY KEY,
                ownership_lean TEXT,
                regional_lean TEXT,
                confidence TEXT,
                notes TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS stories (
                id TEXT PRIMARY KEY,
                representative_title TEXT,
                first_seen_at TEXT,
                last_updated_at TEXT,
                article_count INTEGER DEFAULT 0,
                bias_distribution TEXT,
                is_blindspot INTEGER DEFAULT 0
            );
        """)
        self.conn.commit()

    # --- sources ---
    def seed_sources(self, sources: List[Dict]) -> int:
        inserted = 0
        for src in sources:
            if self.backend == 'supabase':
                try:
                    existing = self.supabase.table('sources').select('id').eq('rss_url', src['rss_url']).execute()
                    if existing.data:
                        continue
                    res = self.supabase.table('sources').insert({
                        'name': src['name'], 'rss_url': src['rss_url'],
                        'homepage_url': src['homepage_url'], 'country': 'NG', 'active': True
                    }).execute()
                    if res.data:
                        inserted += 1
                except Exception as e:
                    logger.warning(f"seed source {src['name']} failed: {e}")
            else:
                cur = self.conn.execute("SELECT 1 FROM sources WHERE rss_url=?", (src['rss_url'],))
                if cur.fetchone():
                    continue
                sid = compute_hash(src['rss_url'])
                self.conn.execute(
                    "INSERT INTO sources (id, name, rss_url, homepage_url, country, active) VALUES (?,?,?,?, 'NG', 1)",
                    (sid, src['name'], src['rss_url'], src['homepage_url']))
                inserted += 1
        if self.backend == 'sqlite':
            self.conn.commit()
        logger.info(f"Seeded {inserted} new sources")
        return inserted

    def get_sources(self) -> List[Dict]:
        if self.backend == 'supabase':
            try:
                res = self.supabase.table('sources').select('id, name, rss_url, homepage_url').eq('active', True).execute()
                return res.data or []
            except Exception as e:
                logger.error(f"get_sources failed: {e}")
                return []
        cur = self.conn.execute("SELECT id, name, rss_url, homepage_url FROM sources WHERE active=1")
        return [dict(r) for r in cur.fetchall()]

    def resolve_source_id(self, rss_url: str) -> Optional[str]:
        if self.backend == 'supabase':
            res = self.supabase.table('sources').select('id').eq('rss_url', rss_url).execute()
            if res.data:
                return res.data[0]['id']
            return None
        cur = self.conn.execute("SELECT id FROM sources WHERE rss_url=?", (rss_url,))
        r = cur.fetchone()
        return r['id'] if r else None

    # --- articles ---
    def article_exists(self, url: str) -> bool:
        if self.backend == 'supabase':
            try:
                res = self.supabase.table('articles').select('id').eq('url', url).limit(1).execute()
                return len(res.data) > 0
            except Exception:
                return False
        cur = self.conn.execute("SELECT 1 FROM articles WHERE url=?", (url,))
        return cur.fetchone() is not None

    def insert_article(self, art: Dict) -> bool:
        if self.backend == 'supabase':
            try:
                res = self.supabase.table('articles').insert(art).execute()
                return len(res.data) > 0
            except Exception as e:
                if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
                    return False
                logger.error(f"insert_article failed: {e}")
                return False
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO articles
                   (id, source_id, url, title, summary, full_text, published_at, content_hash, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (compute_hash(art['url']), art['source_id'], art['url'], art['title'],
                 art['summary'], art['full_text'], art['published_at'], art['content_hash'], art['fetched_at']))
            return self.conn.total_changes > 0
        except Exception as e:
            logger.error(f"sqlite insert failed: {e}")
            return False


# ---------------------------------------------------------------------------
# Ingestion logic
# ---------------------------------------------------------------------------

def parse_rss_feed(feed_url: str) -> tuple:
    """Fetch a feed with a browser UA (Cloudflare blocks feedparser's default
    UA) and parse it. Returns (entries, error_or_None)."""
    try:
        # Pull the raw feed with requests + browser headers, then hand the
        # bytes to feedparser. This bypasses feedparser's default UA which
        # several Nigerian outlets block with a 403 challenge page.
        resp = requests.get(feed_url, headers=BROWSER_HDR, timeout=30)
        if resp.status_code != 200:
            return None, f"http_{resp.status_code}"
        feed = feedparser.parse(resp.content)
        if feed.bozo:
            logger.warning(f"Feed parse warning for {feed_url}: {feed.bozo_exception}")
        if not feed.entries:
            # Could be a challenge/HTML page, not real XML
            return None, "no_entries"
        return feed.entries, None
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.RequestException as e:
        return None, f"request_error:{e}"
    except Exception as e:
        return None, str(e)


def extract_full_text(url: str) -> tuple:
    """Fetch article HTML with requests (browser UA) and extract with
    trafilatura. Returns (text_or_None, status)."""
    try:
        resp = requests.get(url, headers=BROWSER_HDR, timeout=EXTRACTION_TIMEOUT)
        if resp.status_code == 403:
            return None, "blocked"
        if resp.status_code != 200:
            return None, f"http_{resp.status_code}"
        extracted = trafilatura.extract(
            resp.text, include_comments=False, include_tables=False
        )
        if not extracted:
            return None, "extraction_failed"
        return extracted, None
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.RequestException as e:
        msg = str(e).lower()
        if "403" in msg or "forbidden" in msg:
            return None, "blocked"
        if "timed out" in msg:
            return None, "timeout"
        return None, "other"


def build_article(entry, source_id: str) -> Dict:
    url = entry.get('link', '')
    title = entry.get('title', 'No title')
    summary = entry.get('summary', entry.get('description', ''))
    published_at = datetime.now(timezone.utc).isoformat()
    if getattr(entry, 'published_parsed', None):
        try:
            published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    content_hash = compute_hash(f"{title}{summary}")
    return {
        'source_id': source_id,
        'url': url,
        'title': title,
        'summary': summary or '',
        'full_text': None,
        'published_at': published_at,
        'content_hash': content_hash,
        'fetched_at': datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class Stats:
    feeds_total: int = 0
    feeds_success: int = 0
    feeds_failed: int = 0
    articles_seen: int = 0
    articles_attempted: int = 0
    articles_new: int = 0
    extraction_success: int = 0
    extraction_failed: int = 0
    extraction_blocked: int = 0
    extraction_timeout: int = 0
    extraction_other: int = 0
    failures: List[Dict] = field(default_factory=list)

    @property
    def feed_success_rate(self) -> float:
        return (self.feeds_success / self.feeds_total * 100) if self.feeds_total else 0.0

    @property
    def extraction_success_rate(self) -> float:
        # Denominator = articles we actually attempted extraction on
        # (not all feed entries, which include already-stored duplicates).
        return (self.extraction_success / self.articles_attempted * 100) if self.articles_attempted else 0.0

    def to_dict(self) -> Dict:
        return {
            'backend': None,  # filled by caller
            'feeds_total': self.feeds_total,
            'feeds_success': self.feeds_success,
            'feeds_failed': self.feeds_failed,
            'feed_success_rate': round(self.feed_success_rate, 2),
            'articles_seen': self.articles_seen,
            'articles_attempted': self.articles_attempted,
            'articles_new': self.articles_new,
            'extraction_success': self.extraction_success,
            'extraction_failed': self.extraction_failed,
            'extraction_blocked': self.extraction_blocked,
            'extraction_timeout': self.extraction_timeout,
            'extraction_other': self.extraction_other,
            'extraction_success_rate': round(self.extraction_success_rate, 2),
            'failures': self.failures,
        }


def run(db: DB) -> Stats:
    stats = Stats()
    sources = db.get_sources()
    if not sources:
        logger.error("No sources to poll. Seeding may have failed.")
        return stats

    for source in sources:
        stats.feeds_total += 1
        name = source['name']
        rss_url = source['rss_url']
        logger.info(f"[{stats.feeds_total}/{len(sources)}] {name}  ({rss_url})")

        source_id = db.resolve_source_id(rss_url)
        if not source_id:
            stats.feeds_failed += 1
            stats.failures.append({'source': name, 'reason': 'source_id_not_found'})
            continue

        entries, err = parse_rss_feed(rss_url)
        if err or not entries:
            stats.feeds_failed += 1
            stats.failures.append({'source': name, 'reason': err or 'no_entries'})
            logger.error(f"  feed failed: {err}")
            continue

        stats.feeds_success += 1
        logger.info(f"  {len(entries)} entries; processing up to {ARTICLES_PER_SOURCE}")

        # Build candidate articles, skipping ones already stored
        candidates = []
        for entry in entries[:ARTICLES_PER_SOURCE]:
            stats.articles_seen += 1
            link = entry.get('link', '')
            if db.article_exists(link):
                logger.debug(f"  skip dup: {entry.get('title', '')[:50]}")
                continue
            candidates.append(build_article(entry, source_id))

        if not candidates:
            continue

        # Parallel full-text extraction
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut_to_art = {ex.submit(extract_full_text, a['url']): a for a in candidates}
            for fut in as_completed(fut_to_art):
                art = fut_to_art[fut]
                text, status = fut.result()
                if status is None:
                    art['full_text'] = text
                    art['content_hash'] = compute_hash(f"{art['title']}{art['summary']}{text[:500]}")
                    stats.extraction_success += 1
                else:
                    art['full_text'] = None
                    if status == 'extraction_failed':
                        stats.extraction_failed += 1
                    elif status == 'blocked':
                        stats.extraction_blocked += 1
                    elif status == 'timeout':
                        stats.extraction_timeout += 1
                    else:
                        stats.extraction_other += 1
                if db.insert_article(art):
                    stats.articles_new += 1
                else:
                    stats.failures.append({'source': name, 'title': art['title'][:100], 'reason': 'insert_failed'})

        if db.backend == 'sqlite':
            db.conn.commit()
        logger.info(f"  done: +{len(candidates)} articles, extraction ok={stats.extraction_success}")

    return stats


def main():
    logger.info("=" * 64)
    logger.info("PHASE 1 — NAIJAPulse Ingestion")
    logger.info("=" * 64)

    db = DB()
    db.seed_sources(SOURCES)

    start = time.time()
    stats = run(db)
    elapsed = time.time() - start

    report = stats.to_dict()
    report['backend'] = db.backend
    report['elapsed_seconds'] = round(elapsed, 1)
    report['passed'] = (stats.feed_success_rate >= 90.0 and stats.extraction_success_rate >= 70.0)

    print("\n" + "=" * 64)
    print("ACCEPTANCE TEST RESULTS")
    print("=" * 64)
    print(f"Storage backend:        {db.backend}")
    print(f"Feeds polled:           {stats.feeds_total}")
    print(f"Feeds successful:       {stats.feeds_success}")
    print(f"Feeds failed:           {stats.feeds_failed}")
    print(f"Feed success rate:      {stats.feed_success_rate:.1f}%   (target >= 90%)")
    print()
    print(f"Articles seen:          {stats.articles_seen}")
    print(f"Articles newly stored:  {stats.articles_new}")
    print(f"  full-text OK:         {stats.extraction_success}")
    print(f"  extraction_failed:   {stats.extraction_failed}")
    print(f"  blocked (403):       {stats.extraction_blocked}")
    print(f"  timeout:             {stats.extraction_timeout}")
    print(f"  other:               {stats.extraction_other}")
    print(f"Extraction success:     {stats.extraction_success_rate:.1f}%   (target >= 70%)")
    print()
    print(f"Elapsed:                {elapsed:.1f}s")

    with open('ingest_report.json', 'w') as f:
        json.dump(report, f, indent=2)

    print(f"\nReport: ingest_report.json")
    print(f"ACCEPTANCE TEST: {'PASSED ✓' if report['passed'] else 'NOT PASSED ✗'}")

    if stats.failures:
        print(f"\nFailures logged ({len(stats.failures)}):")
        for f in stats.failures[:30]:
            print(f"  - {f.get('source','?')}: {f.get('reason','?')}")

    if db.backend == 'sqlite':
        print("\nNOTE: wrote to local SQLite mirror because the live Supabase "
              "Postgres host was not reachable from this environment. Apply "
              "schema.sql in the Supabase SQL editor, then re-run with the "
              "tables present to write live.")

    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
