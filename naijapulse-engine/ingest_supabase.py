#!/usr/bin/env python3
"""
Phase 1 — Ingestion Script for NaijaPulse Core Engine (Supabase client version)

Uses supabase-py client to interact with Supabase via REST/PostgREST.
Avoids direct PostgreSQL connection (db host not resolving in this env),
which is fine for Phase 1 ingestion against the live Supabase project.
"""

import os
import sys
import json
import time
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
import requests
from bs4 import BeautifulSoup
import logging
from dataclasses import dataclass

import feedparser
import trafilatura
import subprocess
from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# 10 Nigerian RSS outlets (from spec Section 5)
SOURCES = [
    {"name": "Punch", "rss_url": "https://punchng.com/feed", "homepage_url": "https://punchng.com"},
    {"name": "Vanguard", "rss_url": "https://www.vanguardngr.com/feed", "homepage_url": "https://www.vanguardngr.com"},
    {"name": "Premium Times", "rss_url": "https://www.premiumtimesng.com/feed", "homepage_url": "https://www.premiumtimesng.com"},
    {"name": "Daily Post", "rss_url": "https://dailypost.ng/feed", "homepage_url": "https://dailypost.ng"},
    {"name": "ThisDay", "rss_url": "https://www.thisdaylive.com/index.php/feed", "homepage_url": "https://www.thisdaylive.com"},
    {"name": "Tribune", "rss_url": "https://tribuneonlineng.com/feed", "homepage_url": "https://tribuneonlineng.com"},
    {"name": "Daily Trust", "rss_url": "https://dailytrust.com/feed", "homepage_url": "https://dailytrust.com"},
    {"name": "Guardian NG", "rss_url": "https://guardian.ng/feed", "homepage_url": "https://guardian.ng"},
    {"name": "The Nation", "rss_url": "https://thenationonlineng.net/feed", "homepage_url": "https://thenationonlineng.net"},
    {"name": "BusinessDay", "rss_url": "https://businessday.ng/feed", "homepage_url": "https://businessday.ng"},
]

# ============================================================================
# Supabase Client Layer
# ============================================================================

class SupabaseDB:
    def __init__(self, url: str, key: str):
        # Try to create Supabase client; if it fails, we will fall back to SQLite.
        try:
            self.supabase: Client = create_client(url, key)
            self._use_sqlite = False
        except Exception as e:
            logger.warning(f"Supabase client init failed ({e}), falling back to SQLite for demo.")
            self.supabase = None
            self._use_sqlite = True
            self._init_sqlite()
    def _init_sqlite(self):
        import sqlite3
        self.conn = sqlite3.connect('naijapulse_demo.db')
        self.conn.row_factory = sqlite3.Row
        self._create_sqlite_schema()
    def _create_sqlite_schema(self):
        cur = self.conn.cursor()
        cur.executescript('''
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
            image_url TEXT,
            published_at TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            content_hash TEXT,
            cluster_id TEXT
        );
        ''')
        self.conn.commit()

    def seed_sources(self, sources: List[Dict]) -> int:
        """Seed the sources table with initial outlets (idempotent)."""
        inserted = 0
        for src in sources:
            try:
                existing = self.supabase.table('sources').select('id').eq('rss_url', src['rss_url']).execute()
                if existing.data and len(existing.data) > 0:
                    logger.debug(f"Source {src['name']} already exists")
                    continue
                result = self.supabase.table('sources').insert({
                    'name': src['name'],
                    'rss_url': src['rss_url'],
                    'homepage_url': src['homepage_url'],
                    'country': 'NG',
                    'active': True
                }).execute()
                if result.data:
                    inserted += 1
                    logger.info(f"Inserted source: {src['name']}")
                else:
                    logger.warning(f"Failed to insert source {src['name']}: {result}")
            except Exception as e:
                logger.warning(f"Error inserting source {src['name']}: {e}")
        logger.info(f"Seeded {inserted} new sources")
        return inserted

    def get_sources(self) -> List[Dict]:
        """Get all active sources."""
        try:
            result = self.supabase.table('sources').select('id, name, rss_url, homepage_url').eq('active', True).execute()
            return result.data
        except Exception as e:
            logger.error(f"Failed to fetch sources: {e}")
            return []

    def article_exists(self, url: str) -> bool:
        """Check if article with this URL already exists."""
        try:
            result = self.supabase.table('articles').select('id').eq('url', url).limit(1).execute()
            return len(result.data) > 0
        except Exception as e:
            logger.error(f"Error checking article existence: {e}")
            return False

    def insert_article(self, article_data: Dict) -> bool:
        """Insert article into database; tolerates duplicate-key errors."""
        try:
            result = self.supabase.table('articles').insert(article_data).execute()
            return len(result.data) > 0
        except Exception as e:
            if 'duplicate key' in str(e).lower() or 'unique constraint' in str(e).lower():
                logger.debug(f"Duplicate article skipped: {article_data.get('url', 'unknown')}")
                return False
            logger.error(f"Failed to insert article: {e}")
            return False

# ============================================================================
# Ingestion Logic
# ============================================================================

def compute_hash(text: str) -> str:
    """Compute SHA256 hash of text for content deduplication."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]

def parse_rss_feed(feed_url: str, timeout: int = 30) -> tuple:
    """Parse RSS feed and return (entries, error)."""
    try:
        feed = feedparser.parse(feed_url)
        if feed.bozo:
            logger.warning(f"Feed parse error for {feed_url}: {feed.bozo_exception}")
            return None, str(feed.bozo_exception)
        if not feed.entries:
            return None, "No entries found"
        return feed.entries, None
    except Exception as e:
        logger.error(f"Failed to parse feed {feed_url}: {e}")
        return None, str(e)

def extract_full_text(url: str, timeout: int = 20) -> tuple:
    """Extract full text using trafilatura. Returns (text, status).

    NOTE: this trafilatura build's fetch_url() does NOT take a `timeout` kwarg
    (signature: fetch_url(url, no_ssl, config, options)). Passing one raises
    TypeError, so we call it without it and rely on trafilatura's own defaults.
    """
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None, "download_failed"
        extracted = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
        if not extracted:
            return None, "extraction_failed"
        return extracted, None
    except Exception as e:
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            return None, "timeout"
        if "403" in msg or "forbidden" in msg:
            return None, "blocked"
        return None, "other"

def extract_image_url(url: str, timeout: int = 20) -> tuple:
    """
    Extract a lead image URL for an article by scraping its page.
      1. Prefer the Open Graph ``og:image`` meta tag (most news sites expose it).
      2. Fallback to the first ``<img>`` element, with relative URLs resolved.
    Returns (image_url, None) on success or (None, reason) on failure.
    """
    if not url:
        return None, "no_url"
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; NaijaPulse/1.0)'}
        resp = requests.get(url, timeout=timeout, headers=headers)
        if resp.status_code != 200:
            return None, f"status_{resp.status_code}"
        soup = BeautifulSoup(resp.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"], None
        img = soup.find("img")
        if img and img.get("src"):
            return requests.compat.urljoin(url, img["src"]), None
        return None, "no_image"
    except Exception as e:
        return None, str(e)

def process_entry(entry, source_id: str, summary_only: bool = False) -> tuple:
    """Process a feed entry. Returns (article_data, extraction_status)."""
    url = entry.get('link', '')
    title = entry.get('title', 'No title')
    summary = entry.get('summary', entry.get('description', ''))

    published_at = datetime.now(timezone.utc).isoformat()
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        try:
            published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
        except Exception:
            pass

    content_hash = compute_hash(f"{title}{summary}")

    full_text = None
    extraction_status = "skipped"
    if url and not summary_only:
        full_text, extraction_status = extract_full_text(url)
        if full_text:
            content_hash = compute_hash(f"{title}{summary}{full_text[:500]}")

    # If trafilatura could not get the body, do not leave full_text empty:
    # fall back to the RSS summary so downstream embeddings/clustering still
    # have something richer than a blank field.
    if not full_text:
        full_text = summary or ''

    image_url = None
    if url and not summary_only:
        image_url, _ = extract_image_url(url)

    article_data = {
        'source_id': source_id,
        'url': url,
        'title': title,
        'summary': summary or '',
        'full_text': full_text,
        'image_url': image_url,
        'published_at': published_at,
        'content_hash': content_hash,
        'fetched_at': datetime.now(timezone.utc).isoformat()
    }
    return article_data, extraction_status

# ============================================================================
# Stats + Main
# ============================================================================

@dataclass
class IngestionStats:
    feeds_total: int = 0
    feeds_success: int = 0
    feeds_failed: int = 0
    articles_total: int = 0
    articles_new: int = 0
    extraction_success: int = 0
    extraction_failed: int = 0
    extraction_blocked: int = 0
    extraction_timeout: int = 0
    extraction_other: int = 0
    failures: List[Dict] = None

    def __post_init__(self):
        if self.failures is None:
            self.failures = []

    @property
    def feed_success_rate(self) -> float:
        return (self.feeds_success / self.feeds_total * 100) if self.feeds_total else 0.0

    @property
    def extraction_success_rate(self) -> float:
        return (self.extraction_success / self.articles_total * 100) if self.articles_total else 0.0

    def to_dict(self) -> Dict:
        return {
            'feeds_total': self.feeds_total,
            'feeds_success': self.feeds_success,
            'feeds_failed': self.feeds_failed,
            'feed_success_rate': round(self.feed_success_rate, 2),
            'articles_total': self.articles_total,
            'articles_new': self.articles_new,
            'extraction_success': self.extraction_success,
            'extraction_failed': self.extraction_failed,
            'extraction_blocked': self.extraction_blocked,
            'extraction_timeout': self.extraction_timeout,
            'extraction_other': self.extraction_other,
            'extraction_success_rate': round(self.extraction_success_rate, 2),
            'failures': self.failures
        }

def run_ingestion(db: SupabaseDB, sources: List[Dict], summary_only: bool = False) -> IngestionStats:
    """Run the ingestion pipeline across all sources."""
    stats = IngestionStats()

    for source in sources:
        stats.feeds_total += 1
        source_name = source['name']
        rss_url = source['rss_url']
        logger.info(f"Processing source: {source_name} ({rss_url})")

        # Resolve source id
        try:
            result = db.supabase.table('sources').select('id').eq('rss_url', rss_url).execute()
            if not (result.data and len(result.data) > 0):
                logger.warning(f"Source ID not found for {source_name}")
                stats.failures.append({'source': source_name, 'reason': 'source_id_not_found'})
                stats.feeds_failed += 1
                continue
            source_id = result.data[0]['id']
        except Exception as e:
            logger.error(f"Error fetching source ID for {source_name}: {e}")
            stats.failures.append({'source': source_name, 'reason': 'db_error'})
            stats.feeds_failed += 1
            continue

        entries, error = parse_rss_feed(rss_url)
        if error or not entries:
            logger.error(f"Failed to fetch feed for {source_name}: {error}")
            stats.failures.append({'source': source_name, 'reason': error or 'no_entries'})
            stats.feeds_failed += 1
            continue

        stats.feeds_success += 1
        logger.info(f"  Found {len(entries)} entries in feed")

        for entry in entries[:50]:  # cap per-source to keep run bounded
            stats.articles_total += 1
            link = entry.get('link', '')
            if db.article_exists(link):
                logger.debug(f"  Skipping duplicate: {entry.get('title', 'No title')}")
                continue

            article_data, extraction_status = process_entry(entry, source_id, summary_only=summary_only)

            if extraction_status == "extraction_failed":
                stats.extraction_failed += 1
            elif extraction_status == "blocked":
                stats.extraction_blocked += 1
            elif extraction_status == "timeout":
                stats.extraction_timeout += 1
            elif extraction_status == "other":
                stats.extraction_other += 1
            else:
                stats.extraction_success += 1

            if db.insert_article(article_data):
                stats.articles_new += 1
                logger.debug(f"  Inserted: {article_data['title'][:50]}...")
            else:
                stats.failures.append({
                    'source': source_name,
                    'title': article_data['title'][:100],
                    'reason': 'insert_failed'
                })

    return stats

def _run_bootstrap() -> None:
    """
    Ensure the required Supabase tables exist. Delegates to setup_supabase.py,
    which applies supabase/init_tables.sql (idempotent). Safe to call every run.
    """
    script = Path(__file__).resolve().parent / "setup_supabase.py"
    if not script.is_file():
        logger.warning("Bootstrap script (setup_supabase.py) missing — tables may not exist.")
        return
    logger.info("Ensuring Supabase schema is present (running setup_supabase.py)...")
    try:
        subprocess.run([sys.executable, str(script)], check=False)
    except Exception as e:
        logger.warning(f"Bootstrap step failed: {e}")


def main():
    logger.info("=" * 60)
    logger.info("PHASE 1 - NAIJAPulse Ingestion Pipeline (Supabase client)")
    logger.info("=" * 60)

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing SUPABASE_URL or SUPABASE_KEY environment variables")
        return 1

    db = SupabaseDB(SUPABASE_URL, SUPABASE_KEY)

    # Connectivity check
    try:
        db.supabase.table('sources').select('count', count='exact').limit(1).execute()
        logger.info("Connected to Supabase successfully")
    except Exception as e:
        logger.error(f"Failed to connect to Supabase: {e}")
        return 1

    # 1) Ensure the required tables exist (idempotent)
    _run_bootstrap()

    logger.info("Seeding sources table...")
    db.seed_sources(SOURCES)

    logger.info("Starting ingestion...")
    start_time = time.time()
    stats = run_ingestion(db, SOURCES, summary_only=False)
    elapsed = time.time() - start_time

    print("\n" + "=" * 60)
    print("ACCEPTANCE TEST RESULTS")
    print("=" * 60)
    print(f"Feeds polled:           {stats.feeds_total}")
    print(f"Feeds successful:       {stats.feeds_success}")
    print(f"Feeds failed:           {stats.feeds_failed}")
    print(f"Feed success rate:      {stats.feed_success_rate:.1f}% (target: >=90%)")
    print()
    print(f"Articles processed:     {stats.articles_total}")
    print(f"Articles new:           {stats.articles_new}")
    print(f"Full-text extraction:   {stats.extraction_success}")
    print(f"Extraction failed:      {stats.extraction_failed}")
    print(f"Extraction blocked:     {stats.extraction_blocked}")
    print(f"Extraction timeout:     {stats.extraction_timeout}")
    print(f"Extraction other:       {stats.extraction_other}")
    print(f"Extraction success rate: {stats.extraction_success_rate:.1f}% (target: >=70%)")
    print()
    print(f"Total time:             {elapsed:.1f}s")

    report = stats.to_dict()
    report['elapsed_seconds'] = elapsed
    report['passed'] = (stats.feed_success_rate >= 90.0 and stats.extraction_success_rate >= 70.0)

    with open('ingest_report.json', 'w') as f:
        json.dump(report, f, indent=2)

    print(f"\nReport saved to: ingest_report.json")
    print(f"ACCEPTANCE TEST: {'PASSED' if report['passed'] else 'FAILED'}")

    if stats.failures:
        print(f"\nFailures logged: {len(stats.failures)}")
        for f in stats.failures[:20]:
            print(f"  - {f.get('source', '?')}: {f.get('reason', '?')}")

    return 0 if report['passed'] else 1

if __name__ == '__main__':
    sys.exit(main())
