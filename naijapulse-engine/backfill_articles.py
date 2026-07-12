#!/usr/bin/env python3
"""
backfill_articles.py — Manually populate full_text + image_url on existing rows
that are missing them.

Why this exists:
  - The first ingestion run happened *before* image extraction and the
    (correct) full-text extraction were in place, so those rows have empty
    fields. run_ingestion() skips a URL that already exists, so a re-run
    won't fix them. This script UPDATEs only the rows that need it.

Usage:
    ./venv/bin/python backfill_articles.py
"""

import os
import sys
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import trafilatura
import requests
from bs4 import BeautifulSoup
from supabase import create_client

load_dotenv("/Users/Adegoke/Documents/news/.env")  # adjust if your .env lives elsewhere

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
if not SUPABASE_URL or not SUPABASE_KEY:
    print("Missing SUPABASE_URL / SUPABASE_KEY in environment")
    sys.exit(1)

client = create_client(SUPABASE_URL, SUPABASE_KEY)

UA = {'User-Agent': 'Mozilla/5.0 (compatible; NaijaPulse/1.0)'}


def fetch_full_text(url: str) -> str:
    """Extract the real article body with trafilatura (no timeout kwarg)."""
    try:
        html = trafilatura.fetch_url(url)  # signature: fetch_url(url, ...)
        if not html:
            return ''
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        return text or ''
    except Exception:
        return ''


def fetch_image_url(url: str) -> str:
    """Scrape the lead image (prefer og:image, fallback to first <img>)."""
    try:
        r = requests.get(url, timeout=20, headers=UA)
        if r.status_code != 200:
            return ''
        soup = BeautifulSoup(r.text, 'html.parser')
        og = soup.find('meta', property='og:image')
        if og and og.get('content'):
            return og['content']
        img = soup.find('img')
        if img and img.get('src'):
            return requests.compat.urljoin(url, img['src'])
        return ''
    except Exception:
        return ''


def main():
    # Re-extract EVERY row: the previous runs either stored NULL or fell back
    # to the RSS summary (HTML), so none have genuine full text yet.
    resp = (client.table('articles')
            .select('id,url,summary,full_text,image_url')
            .execute())
    rows = resp.data or []
    print(f"Rows to (re)process: {len(rows)}")

    updated = 0
    for row in rows:
        url = row.get('url')
        if not url:
            continue

        full_text = fetch_full_text(url)
        image_url = fetch_image_url(url)

        # Never leave full_text totally empty
        if not full_text:
            full_text = row.get('summary') or ''

        patch = {'full_text': full_text, 'image_url': image_url}
        try:
            client.table('articles').update(patch).eq('id', row['id']).execute()
            updated += 1
            print(f"  updated {row['id'][:8]} | ft={len(full_text)} chars | "
                  f"img={'yes' if image_url else 'no'}")
        except Exception as e:
            print(f"  FAILED {row['id'][:8]}: {e}")

        time.sleep(0.3)  # be gentle on the source sites

    print(f"\nBackfill complete. Updated {updated}/{len(rows)} rows.")


if __name__ == '__main__':
    main()
