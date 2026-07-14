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
from urllib.parse import urlparse
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

# 25+ sources across Nigerian (politics/economy), Sports, International, African, and Entertainment categories
SOURCES = [
    # === NIGERIAN POLITICS / GENERAL NEWS ===
    {"name": "Punch", "rss_url": "https://punchng.com/feed", "homepage_url": "https://punchng.com", "country": "NG"},
    {"name": "Vanguard", "rss_url": "https://www.vanguardngr.com/feed", "homepage_url": "https://www.vanguardngr.com", "country": "NG"},
    {"name": "Premium Times", "rss_url": "https://www.premiumtimesng.com/feed", "homepage_url": "https://www.premiumtimesng.com", "country": "NG"},
    {"name": "Daily Post", "rss_url": "https://dailypost.ng/feed", "homepage_url": "https://dailypost.ng", "country": "NG"},
    {"name": "ThisDay", "rss_url": "https://www.thisdaylive.com/index.php/feed", "homepage_url": "https://www.thisdaylive.com", "country": "NG"},
    {"name": "Tribune", "rss_url": "https://tribuneonlineng.com/feed", "homepage_url": "https://tribuneonlineng.com", "country": "NG"},
    {"name": "Daily Trust", "rss_url": "https://dailytrust.com/feed", "homepage_url": "https://dailytrust.com", "country": "NG"},
    {"name": "Guardian NG", "rss_url": "https://guardian.ng/feed", "homepage_url": "https://guardian.ng", "country": "NG"},
    {"name": "The Nation", "rss_url": "https://thenationonlineng.net/feed", "homepage_url": "https://thenationonlineng.net", "country": "NG"},
    {"name": "BusinessDay", "rss_url": "https://businessday.ng/feed", "homepage_url": "https://businessday.ng", "country": "NG"},
    {"name": "TheCable", "rss_url": "https://www.thecable.ng/feed", "homepage_url": "https://www.thecable.ng", "country": "NG"},
    {"name": "Channels TV", "rss_url": "https://www.channelstv.com/feed/", "homepage_url": "https://www.channelstv.com", "country": "NG"},
    {"name": "Sahara Reporters", "rss_url": "https://saharareporters.com/rss.xml", "homepage_url": "https://saharareporters.com", "country": "NG"},
    {"name": "Peoples Gazette", "rss_url": "https://gazettengr.com/feed/", "homepage_url": "https://gazettengr.com", "country": "NG"},
    {"name": "Leadership", "rss_url": "https://leadership.ng/feed/", "homepage_url": "https://leadership.ng", "country": "NG"},

    # === NIGERIAN BUSINESS / ECONOMY ===
    {"name": "Nairametrics", "rss_url": "https://nairametrics.com/feed/", "homepage_url": "https://nairametrics.com", "country": "NG"},
    {"name": "The Whistler", "rss_url": "https://thewhistler.ng/feed/", "homepage_url": "https://thewhistler.ng", "country": "NG"},
    {"name": "Financial Watch", "rss_url": "https://financialwatchngr.com/feed/", "homepage_url": "https://financialwatchngr.com", "country": "NG"},
    {"name": "Blueprint", "rss_url": "https://blueprint.ng/feed/", "homepage_url": "https://blueprint.ng", "country": "NG"},

    # === SPORTS (International + Nigerian) ===
    {"name": "ESPN", "rss_url": "http://static.espncricinfo.com/rss/stories/english/61.xml", "homepage_url": "https://www.espncricinfo.com", "country": "International"},
    {"name": "BBC Sport", "rss_url": "http://feeds.bbci.co.uk/sport/rss.xml", "homepage_url": "https://www.bbc.com/sport", "country": "GB"},
    {"name": "Premium Times Sports", "rss_url": "https://www.premiumtimesng.com/category/sports/feed/", "homepage_url": "https://www.premiumtimesng.com", "country": "NG"},
    {"name": "Sporting Life", "rss_url": "https://sportinglife.ng/feed/", "homepage_url": "https://sportinglife.ng", "country": "NG"},

    # === INTERNATIONAL NEWS ===
    {"name": "BBC News", "rss_url": "https://feeds.bbci.co.uk/news/rss.xml", "homepage_url": "https://www.bbc.com/news", "country": "GB"},
    {"name": "Al Jazeera", "rss_url": "https://www.aljazeera.com/xml/rss/all.xml", "homepage_url": "https://www.aljazeera.com", "country": "QA"},
    {"name": "France24 Africa", "rss_url": "https://www.france24.com/en/africa/rss", "homepage_url": "https://www.france24.com/en/africa", "country": "FR"},

    # === AFRICAN SOURCES (ex-Nigeria) ===
    {"name": "MyJoyOnline", "rss_url": "https://www.myjoyonline.com/feed/", "homepage_url": "https://www.myjoyonline.com", "country": "GH"},
    {"name": "Standard Digital", "rss_url": "https://www.standardmedia.co.ke/rss", "homepage_url": "https://www.standardmedia.co.ke", "country": "KE"},

    # === NIGERIAN ENTERTAINMENT ===
    {"name": "Information Nigeria", "rss_url": "https://informationng.com/feed", "homepage_url": "https://informationng.com", "country": "NG"},
    {"name": "BellaNaija", "rss_url": "https://www.bellanaija.com/feed", "homepage_url": "https://www.bellanaija.com", "country": "NG"},
    {"name": "tooXclusive", "rss_url": "https://tooxclusive.com/feed/", "homepage_url": "https://tooxclusive.com", "country": "NG"},
]

# Several Nigerian outlets (Punch, Vanguard, Guardian NG, The Nation, ...) block
# feedparser's default User-Agent with a 403 challenge page, which feedparser then
# fails to parse as XML. Fetch with a browser UA + Accept headers first, then hand
# the raw bytes to feedparser. Without this, those four feeds return 0 entries.
BROWSER_HDR = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


def _domain_of(url: str) -> str:
    """Extract the bare host (registered domain) from a URL, for the Google
    News fallback. Strips a leading 'www.'."""
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def google_news_feed_url(domain: str) -> str:
    """Google News RSS search scoped to a single outlet domain. Used as a
    fallback when an outlet's own feed is blocked (Cloudflare managed challenge)
    or serves an empty feed. Returns recent headlines for that outlet without
    hitting the outlet's bot protection.
    """
    return (f"https://news.google.com/rss/search?q=site:{domain}"
            f"&hl=en-NG&gl=NG&ceid=NG:en")

# ============================================================================
# Supabase Client Layer
# ============================================================================

class SupabaseDB:
    def __init__(self, url: str, key: str):
        self.supabase: Client = create_client(url, key)
        # If this raises, let it raise — do not silently swap to a different
        # storage backend with different guarantees. A pipeline run that thinks
        # it's writing to Supabase but is actually writing to local SQLite is a
        # silent data-loss bug, not a resilience feature.

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
                    'country': src.get('country', 'NG'),
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

    def get_existing_urls(self, urls: List[str]) -> Set[str]:
        """Batch existence check.

        PostgREST/Postgres rejects a single .in_() filter once the serialized
        query (e.g. 100 long Google News URLs) grows past its limits, returning
        HTTP 400 ("JSON could not be generated"). Chunk the URLs into small
        batches so each round trip stays well under that ceiling.
        """
        if not urls:
            return set()
        seen: Set[str] = set()
        chunk_size = 25
        for i in range(0, len(urls), chunk_size):
            batch = urls[i:i + chunk_size]
            try:
                result = self.supabase.table('articles').select('url').in_('url', batch).execute()
                seen.update(row['url'] for row in (result.data or []))
            except Exception as e:
                logger.warning(f"Batch existence check failed for chunk {i // chunk_size}: {e}")
        return seen

    def insert_article(self, article_data: Dict) -> Optional[str]:
        """Insert article; return the new row's id, or None on failure/duplicate."""
        try:
            result = self.supabase.table('articles').insert(article_data).execute()
            if result.data:
                return result.data[0].get('id')
            return None
        except Exception as e:
            if 'duplicate key' in str(e).lower() or 'unique constraint' in str(e).lower():
                logger.debug(f"Duplicate article skipped: {article_data.get('url', 'unknown')}")
                return None
            logger.error(f"Failed to insert article: {e}")
            return None

# ============================================================================
# Ingestion Logic
# ============================================================================

# Phase 2 (embedding) imports — kept local so ingestion doesn't hard-depend on
# Ollama being importable. Tolerated if the module is missing.
try:
    from embed_core import embed_texts, fetch_text, store_embedding, EMBED_MODEL
    _EMBED_AVAILABLE = True
except Exception:  # pragma: no cover
    _EMBED_AVAILABLE = False


def compute_hash(text: str) -> str:
    """Compute SHA256 hash of text for content deduplication."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]

def parse_rss_feed(feed_url: str, timeout: int = 30) -> tuple:
    """Parse RSS feed and return (entries, error).

    Fetches the feed with a browser User-Agent (several Nigerian outlets block
    feedparser's default UA with a 403 challenge page) and passes the raw bytes
    to feedparser, which is far more lenient than parsing a URL directly.
    """
    try:
        resp = requests.get(feed_url, headers=BROWSER_HDR, timeout=timeout)
        if resp.status_code != 200:
            return None, f"http_{resp.status_code}"
        feed = feedparser.parse(resp.content)
        if feed.bozo:
            logger.warning(f"Feed parse warning for {feed_url}: {feed.bozo_exception}")
        if not feed.entries:
            # Likely a challenge/HTML page rather than real XML.
            return None, "no_entries"
        return feed.entries, None
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.RequestException as e:
        return None, f"request_error:{e}"
    except Exception as e:
        logger.error(f"Failed to parse feed {feed_url}: {e}")
        return None, str(e)

def fetch_page(url: str, timeout: int = 20) -> Optional[str]:
    """Single download of the raw HTML, reused by both extraction steps.

    Finding 8: every article used to be downloaded twice (once by
    trafilatura.fetch_url in extract_full_text, once by requests.get in
    extract_image_url). Now the page is fetched exactly once here and handed to
    both extractors, halving scraping-related network time and load.
    """
    try:
        resp = requests.get(url, headers=BROWSER_HDR, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
    except requests.exceptions.RequestException:
        pass
    return None


def extract_full_text_from_html(html: str) -> tuple:
    """Extract full text from already-fetched HTML using trafilatura.

    Returns (text, status). Preserves the status strings the ingestion stats
    counters rely on (download_failed / extraction_failed).
    """
    if not html:
        return None, "download_failed"
    extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not extracted:
        return None, "extraction_failed"
    return extracted, None


def extract_image_url_from_html(html: str, base_url: str) -> tuple:
    """Extract a lead image URL from already-fetched HTML.

    Returns (image_url, None) on success or (None, reason) on failure.
      1. Prefer the Open Graph ``og:image`` meta tag (most news sites expose it).
      2. Fallback to the first ``<img>`` element, with relative URLs resolved.
    """
    if not html:
        return None, "no_html"
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return og["content"], None
    img = soup.find("img")
    if img and img.get("src"):
        return requests.compat.urljoin(base_url, img["src"]), None
    return None, "no_image"

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
    html = fetch_page(url) if (url and not summary_only) else None
    if url and not summary_only:
        full_text, extraction_status = extract_full_text_from_html(html)
        if full_text:
            content_hash = compute_hash(f"{title}{summary}{full_text[:500]}")

    # If trafilatura could not get the body, do not leave full_text empty:
    # fall back to the RSS summary so downstream embeddings/clustering still
    # have something richer than a blank field.
    if not full_text:
        full_text = summary or ''

    image_url = None
    if url and not summary_only:
        image_url, _ = extract_image_url_from_html(html, url)

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
    feeds_gnews_fallback: int = 0
    articles_total: int = 0
    articles_new: int = 0
    extraction_success: int = 0
    extraction_failed: int = 0
    extraction_blocked: int = 0
    extraction_timeout: int = 0
    extraction_other: int = 0
    extraction_summary_only: int = 0  # fallback/GNews feeds: captured via RSS summary, no full-text attempt
    failures: List[Dict] = None
    embedded: int = 0
    embed_failed: int = 0

    def __post_init__(self):
        if self.failures is None:
            self.failures = []

    @property
    def feed_success_rate(self) -> float:
        return (self.feeds_success / self.feeds_total * 100) if self.feeds_total else 0.0

    @property
    def extraction_attempted(self) -> int:
        """Articles we actually tried to extract full text from (excludes
        duplicates and summary-only fallback captures)."""
        return (self.extraction_success + self.extraction_failed
                + self.extraction_blocked + self.extraction_timeout
                + self.extraction_other)

    @property
    def extraction_success_rate(self) -> float:
        # Measure quality over *attempted* extractions, not over every entry
        # processed (which includes already-existing duplicates skipped before
        # extraction). Otherwise re-runs on a populated DB collapse the rate and
        # spuriously fail the acceptance gate, aborting the whole pipeline.
        attempted = self.extraction_attempted
        if not attempted:
            return 100.0
        return self.extraction_success / attempted * 100

    def to_dict(self) -> Dict:
        return {
            'feeds_total': self.feeds_total,
            'feeds_success': self.feeds_success,
            'feeds_failed': self.feeds_failed,
            'feeds_gnews_fallback': self.feeds_gnews_fallback,
            'feed_success_rate': round(self.feed_success_rate, 2),
            'articles_total': self.articles_total,
            'articles_new': self.articles_new,
            'extraction_success': self.extraction_success,
            'extraction_failed': self.extraction_failed,
            'extraction_blocked': self.extraction_blocked,
            'extraction_timeout': self.extraction_timeout,
            'extraction_other': self.extraction_other,
            'extraction_summary_only': self.extraction_summary_only,
            'extraction_success_rate': round(self.extraction_success_rate, 2),
            'embedded': self.embedded,
            'embed_failed': self.embed_failed,
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
        used_fallback = False
        if error or not entries:
            # Direct feed blocked (Cloudflare managed challenge) or empty ->
            # fall back to Google News RSS scoped to this outlet's domain.
            dom = _domain_of(source.get('homepage_url') or rss_url)
            if dom:
                gentries, gerr = parse_rss_feed(google_news_feed_url(dom))
                if gentries:
                    entries, error, used_fallback = gentries, None, True
                    logger.info(f"  direct feed failed ({error}); using Google News fallback for {dom}")
        if error or not entries:
            logger.error(f"Failed to fetch feed for {source_name} (direct + GNews): {error}")
            stats.failures.append({'source': source_name, 'reason': error or 'no_entries'})
            stats.feeds_failed += 1
            continue

        stats.feeds_success += 1
        if used_fallback:
            stats.feeds_gnews_fallback += 1
            logger.info(f"  [Google News] {len(entries)} entries")
        else:
            logger.info(f"  Found {len(entries)} entries in feed")

        # Fallback feeds point at news.google.com redirect URLs, so skip
        # trafilatura full-text extraction (it would fail on the redirect page)
        # and keep the RSS summary/snippet instead.
        entry_summary_only = summary_only or used_fallback

        # Batch existence check: one round trip for the whole source instead of
        # one per entry (Finding 7 — collapses up to 50 round trips into 1).
        entries_batch = entries[:50]
        entry_links = [e.get('link', '') for e in entries_batch if e.get('link')]
        existing = db.get_existing_urls(entry_links)

        for entry in entries_batch:
            stats.articles_total += 1
            link = entry.get('link', '')
            if not link or link in existing:
                if link:
                    logger.debug(f"  Skipping duplicate: {entry.get('title', 'No title')}")
                continue

            article_data, extraction_status = process_entry(entry, source_id, summary_only=entry_summary_only)

            if extraction_status == "extraction_failed":
                stats.extraction_failed += 1
            elif extraction_status == "blocked":
                stats.extraction_blocked += 1
            elif extraction_status == "timeout":
                stats.extraction_timeout += 1
            elif extraction_status == "other":
                stats.extraction_other += 1
            elif extraction_status == "skipped":
                # summary-only / fallback feed: captured via RSS summary, no
                # full-text extraction was attempted. Count separately so it
                # neither inflates nor deflates the extraction success rate.
                stats.extraction_summary_only += 1
            else:
                stats.extraction_success += 1

            new_id = db.insert_article(article_data)
            if new_id:
                stats.articles_new += 1
                logger.debug(f"  Inserted: {article_data['title'][:50]}...")
                # Phase 2 (best-effort): embed inline so fresh articles get vectors
                # without a re-scan. Never fail ingestion if Ollama is down/slow.
                if _EMBED_AVAILABLE and db.supabase is not None:
                    try:
                        vec = embed_texts([f"{article_data['title']}\n\n{article_data.get('summary') or ''}"])
                        store_embedding(db.supabase, new_id, vec[0])
                        stats.embedded += 1
                    except Exception as e:
                        stats.embed_failed += 1
                        logger.warning(f"  Inline embed skipped for new article: {e}")
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
    print(f"  via Google News fallback: {stats.feeds_gnews_fallback}")
    print(f"Feeds failed:           {stats.feeds_failed}")
    print(f"Feed success rate:      {stats.feed_success_rate:.1f}% (target: >=90%)")
    print()
    print(f"Articles processed:     {stats.articles_total}")
    print(f"Articles new:           {stats.articles_new}")
    print(f"Full-text extracted:    {stats.extraction_success}")
    print(f"Summary-only (fallback):{stats.extraction_summary_only}")
    print(f"Extraction failed:      {stats.extraction_failed}")
    print(f"Extraction blocked:     {stats.extraction_blocked}")
    print(f"Extraction timeout:     {stats.extraction_timeout}")
    print(f"Extraction other:       {stats.extraction_other}")
    print(f"Extraction success rate: {stats.extraction_success_rate:.1f}% "
          f"(target: >=70%, over {stats.extraction_attempted} attempted full-text extractions)")
    print()
    print(f"Embedded (inline):      {stats.embedded}")
    print(f"Embed failed:           {stats.embed_failed}")
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
