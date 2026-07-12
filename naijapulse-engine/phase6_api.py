#!/usr/bin/env python3
"""
Phase 6 — Read-only Query/API layer (FastAPI) for the NaijaPulse engine.

A thin, GET-only HTTP surface over the existing Supabase data so the whole
pipeline (phases 1-5) can be validated end-to-end. No UI, no writes, no auth.

Endpoints:
    GET /                          health/info
    GET /stories                   paginated, filterable list of stories
    GET /stories/{id}              one story + collapsed member article list
    GET /sources                   every source joined to its source_bias row
    GET /pipeline-health           diagnostic counts (read-only, not a product)

HARD CONTRACT (enforced everywhere):
    * full_text NEVER appears in any response body / field name.
    * Article fields returned: title, summary, url, image_url, source_name,
      published_at, also_reported_by (canonical only).
    * Never return: embedding vectors, dedup_score, content_hash, fetched_at,
      centroid_embedding.

Run:
    cd naijapulse-engine
    ./venv/bin/uvicorn phase6_api:app --port 8000

See PHASE6_BUILD.md for the full spec and acceptance tests.
"""

import os
import json
from collections import Counter
from functools import lru_cache
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from supabase import create_client

# Load the engine-local .env (SUPABASE_URL / SUPABASE_KEY) regardless of the
# uvicorn working directory, then match the documented client pattern exactly:
#   create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# RSS feed status lives in this file (not the DB). Resolved relative to this
# module so it works regardless of the uvicorn working directory.
_REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ingest_report.json")

# Minimum number of bias-tagged canonical articles before we dare call a
# blindspot. Mirrors bias_blindspot.py MIN_SAMPLE_TAGGED.
MIN_SAMPLE_TAGGED = 3

# Keywords that mark a story as a POLITICAL topic. Substring match against the
# story's representative_title + member headlines, lowercased. Reused verbatim
# from bias_blindspot.py so the API and Phase 5 agree on what "political" means.
POLITICAL_KEYWORDS = (
    "election", "inec", "government", "govt", "minister", "senate", "policy",
    "security", "herdsmen", "insurgency", "corruption", "presidency", "president",
    "governor", "cabinet", "parliament", "legislature", "lawmaker", "lawmakers",
    "budget", "subsidy", "apc", "pdp", "labour party", "political", "politician",
    "campaign", "vote", "voting", "polling", "party", "military", "terrorism",
    "terrorist", "bandit", "bandits", "kidnap", "abduction", "abducted", "police",
    "army", "nscdc", "dhq", "court", "tribunal", "judiciary", "supreme court",
    "assembly", "candidate", "democracy", "protest", "strike", "union", "fuel",
    "naira", "inflation", "central bank", "cbn", "tax", "diplomacy", "ambassador",
    "embassy", "war", "sanction", "coup", "referendum", "constitution",
)


# --------------------------------------------------------------------------
# supabase client (lazy, singleton)
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_client():
    return create_client(os.environ["SUPABASE_URL"],
                         os.environ["SUPABASE_KEY"])


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _is_political_topic(text: str) -> bool:
    """Substring match of POLITICAL_KEYWORDS against lowercased text."""
    if not text:
        return False
    low = text.lower()
    return any(kw in low for kw in POLITICAL_KEYWORDS)


def _safe_distribution(story: dict) -> Dict[str, int]:
    """bias_distribution is a jsonb dict; tolerate NULL/non-dict."""
    bd = story.get("bias_distribution")
    return bd if isinstance(bd, dict) else {}


def _tagged(story: dict) -> int:
    """Sum of bias_distribution values = number of bias-tagged canonical
    articles in the story (every lean, including zeros, is enumerated)."""
    return sum(_safe_distribution(story).values())


def _load_report() -> dict:
    """Read RSS feed status from ingest_report.json (not the DB)."""
    try:
        with open(_REPORT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def _fetch_sources_map(client) -> Dict[str, str]:
    """source_id -> source name (for article.source_name join)."""
    rows = (client.table("sources")
            .select("id, name")
            .execute()
            .data) or []
    return {r["id"]: r.get("name") for r in rows}


def _fetch_canonical_titles_by_story(client) -> Dict[str, List[str]]:
    """cluster_id -> list of canonical member headlines.

    Used to compute is_political_topic per story (representative_title +
    member headlines) without N round-trips.
    """
    rows = (client.table("articles")
            .select("cluster_id, title")
            .is_("canonical_article_id", "null")
            .execute()
            .data) or []
    by_story: Dict[str, List[str]] = {}
    for r in rows:
        cid = r.get("cluster_id")
        if cid is None:
            continue
        by_story.setdefault(cid, []).append(r.get("title") or "")
    return by_story


def _story_is_political(story: dict,
                        canonical_titles: Dict[str, List[str]]) -> bool:
    """Computed (not stored) political-topic flag for one story."""
    titles = canonical_titles.get(story["id"], [])
    scan = " ".join([story.get("representative_title") or ""] + titles)
    return _is_political_topic(scan)


# --------------------------------------------------------------------------
# app
# --------------------------------------------------------------------------
app = FastAPI(
    title="NaijaPulse Engine - Phase 6 Read-only API",
    version="6.0.0",
    description="Read-only HTTP surface over the pipeline's Supabase data.",
)


@app.get("/")
def root():
    return {
        "service": "naijapulse-engine phase6 read-only api",
        "status": "ok",
        "endpoints": ["/stories", "/stories/{id}", "/sources",
                      "/pipeline-health"],
        "note": "read-only; full_text is never returned",
    }


# --------------------------------------------------------------------------
# /stories
# --------------------------------------------------------------------------
@app.get("/stories")
def list_stories(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=500),
    is_blindspot: Optional[bool] = None,
    min_articles: Optional[int] = Query(None, ge=0),
    is_political_topic: Optional[bool] = None,
    sort: str = Query("last_updated_at",
                      pattern="^(last_updated_at|article_count)$"),
):
    client = get_client()

    # Stored filters are pushed to SQL; the computed political filter is applied
    # in Python after we fetch member headlines.
    q = client.table("stories").select(
        "id, representative_title, article_count, bias_distribution, "
        "is_blindspot, bias_coverage_pct, first_seen_at, last_updated_at")
    if is_blindspot is not None:
        q = q.eq("is_blindspot", is_blindspot)
    if min_articles is not None:
        q = q.gte("article_count", min_articles)
    stories = q.execute().data or []

    canonical_titles = _fetch_canonical_titles_by_story(client)

    # Annotate each story with the computed political flag, then apply the
    # optional political filter.
    for st in stories:
        st["is_political_topic"] = _story_is_political(st, canonical_titles)
    if is_political_topic is not None:
        stories = [s for s in stories
                   if s["is_political_topic"] == is_political_topic]

    # Sort (default last_updated_at desc).
    reverse = True
    stories.sort(key=lambda s: s.get(sort) if s.get(sort) is not None else "",
                reverse=reverse)

    total = len(stories)
    page = stories[offset:offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "stories": page,
    }


# --------------------------------------------------------------------------
# /stories/{id}
# --------------------------------------------------------------------------
@app.get("/stories/{story_id}")
def get_story(story_id: str):
    client = get_client()

    rows = (client.table("stories")
            .select("id, representative_title, article_count, bias_distribution, "
                    "is_blindspot, bias_coverage_pct, first_seen_at, "
                    "last_updated_at")
            .eq("id", story_id)
            .execute()
            .data) or []
    if not rows:
        raise HTTPException(status_code=404, detail="story not found")
    story = rows[0]

    # Canonical member articles only (duplicates collapsed).
    members = (client.table("articles")
               .select("id, title, summary, url, image_url, source_id, "
                       "published_at")
               .eq("cluster_id", story_id)
               .is_("canonical_article_id", "null")
               .execute()
               .data) or []

    canonical_ids = [m["id"] for m in members]

    # also_reported_by = count of duplicates whose canonical_article_id points
    # at each canonical article (Ground News "N outlets" pattern).
    also_counts: Dict[str, int] = {}
    if canonical_ids:
        dup_rows = (client.table("articles")
                    .select("canonical_article_id")
                    .in_("canonical_article_id", canonical_ids)
                    .execute()
                    .data) or []
        also_counts = dict(Counter(r["canonical_article_id"] for r in dup_rows))

    sources_map = _fetch_sources_map(client)
    canonical_titles = _fetch_canonical_titles_by_story(client)

    member_list = []
    for m in members:
        member_list.append({
            "title": m.get("title"),
            "summary": m.get("summary"),
            "url": m.get("url"),
            "image_url": m.get("image_url"),
            "source_name": sources_map.get(m.get("source_id")),
            "published_at": m.get("published_at"),
            "also_reported_by": also_counts.get(m["id"], 0),
        })

    is_political = _story_is_political(story, canonical_titles)

    return {
        "id": story["id"],
        "representative_title": story.get("representative_title"),
        "article_count": story.get("article_count"),
        "bias_distribution": _safe_distribution(story),
        "is_blindspot": story.get("is_blindspot"),
        "is_political_topic": is_political,
        "bias_coverage_pct": story.get("bias_coverage_pct"),
        "first_seen_at": story.get("first_seen_at"),
        "last_updated_at": story.get("last_updated_at"),
        "members": member_list,
    }


# --------------------------------------------------------------------------
# /sources
# --------------------------------------------------------------------------
@app.get("/sources")
def list_sources():
    client = get_client()

    sources = (client.table("sources")
               .select("id, name, homepage_url, country, active, created_at")
               .execute()
               .data) or []

    bias_rows = (client.table("source_bias")
                 .select("source_id, ownership_lean, confidence, notes")
                 .execute()
                 .data) or []
    bias_by_source = {r["source_id"]: r for r in bias_rows}

    # Live count of canonical articles contributed per source.
    articles = (client.table("articles")
                .select("source_id, canonical_article_id")
                .execute()
                .data) or []
    canonical_per_source: Dict[str, int] = Counter()
    for a in articles:
        if a.get("canonical_article_id") is None:
            canonical_per_source[a.get("source_id")] += 1

    out = []
    for s in sources:
        b = bias_by_source.get(s["id"])
        out.append({
            "id": s["id"],
            "name": s.get("name"),
            "homepage_url": s.get("homepage_url"),
            "country": s.get("country"),
            "active": s.get("active"),
            "created_at": s.get("created_at"),
            "ownership_lean": b.get("ownership_lean") if b else None,
            "confidence": b.get("confidence") if b else None,
            "notes": b.get("notes") if b else None,
            "canonical_article_count": canonical_per_source.get(s["id"], 0),
        })

    return {"total": len(out), "sources": out}


# --------------------------------------------------------------------------
# /pipeline-health  (diagnostic, not a product feature)
# --------------------------------------------------------------------------
@app.get("/pipeline-health")
def pipeline_health():
    client = get_client()

    total_articles = (client.table("articles")
                      .select("*", count="exact")
                      .execute()).count or 0
    total_canonical = (client.table("articles")
                       .select("*", count="exact")
                       .is_("canonical_article_id", "null")
                       .execute()).count or 0
    total_stories = (client.table("stories")
                     .select("*", count="exact")
                     .execute()).count or 0

    # Per-source article counts.
    articles = (client.table("articles")
                .select("source_id, canonical_article_id")
                .execute()
                .data) or []
    sources_map = _fetch_sources_map(client)
    per_source_total: Dict[str, int] = Counter()
    per_source_canonical: Dict[str, int] = Counter()
    for a in articles:
        sid = a.get("source_id")
        per_source_total[sid] += 1
        if a.get("canonical_article_id") is None:
            per_source_canonical[sid] += 1
    per_source = [
        {
            "source_id": sid,
            "source_name": sources_map.get(sid),
            "total_articles": per_source_total[sid],
            "canonical_articles": per_source_canonical[sid],
        }
        for sid in per_source_total
    ]

    # Stories: distribution buckets + gates.
    stories = (client.table("stories")
               .select("id, representative_title, bias_distribution, "
                       "is_blindspot, bias_coverage_pct")
               .execute()
               .data) or []
    canonical_titles = _fetch_canonical_titles_by_story(client)

    coverage_buckets = {"100%": 0, "50-99%": 0, "1-49%": 0, "0%": 0}
    below_min_sample = 0
    excluded_by_topic_gate = 0
    flagged = 0
    eligible_for_blindspot = 0

    for st in stories:
        tagged = _tagged(st)
        if tagged < MIN_SAMPLE_TAGGED:
            below_min_sample += 1

        cov = st.get("bias_coverage_pct")
        if cov is None:
            coverage_buckets["0%"] += 1
        elif cov >= 100:
            coverage_buckets["100%"] += 1
        elif cov >= 50:
            coverage_buckets["50-99%"] += 1
        elif cov >= 1:
            coverage_buckets["1-49%"] += 1
        else:
            coverage_buckets["0%"] += 1

        is_political = _story_is_political(st, canonical_titles)
        if not is_political:
            excluded_by_topic_gate += 1
        else:
            if tagged >= MIN_SAMPLE_TAGGED:
                eligible_for_blindspot += 1

        if st.get("is_blindspot"):
            flagged += 1

    report = _load_report()
    rss_status = {
        "feeds_total": report.get("feeds_total"),
        "feeds_success": report.get("feeds_success"),
        "feeds_failed": report.get("feeds_failed"),
        "feeds_gnews_fallback": report.get("feeds_gnews_fallback"),
        "feed_success_rate": report.get("feed_success_rate"),
        "extraction_success": report.get("extraction_success"),
        "extraction_success_rate": report.get("extraction_success_rate"),
    }

    return {
        "total_articles": total_articles,
        "total_canonical_articles": total_canonical,
        "total_stories": total_stories,
        "per_source_article_counts": per_source,
        "rss_feed_status": rss_status,
        "min_sample_gate": {
            "threshold": MIN_SAMPLE_TAGGED,
            "stories_below": below_min_sample,
        },
        "bias_coverage_buckets": coverage_buckets,
        "topic_gate": {
            "stories_excluded_by_topic_gate": excluded_by_topic_gate,
        },
        "blindspot": {
            "flagged": flagged,
            "eligible_political_and_tagged_ge_3": eligible_for_blindspot,
        },
    }


# --------------------------------------------------------------------------
# safety: guarantee full_text can never leak, even via an unexpected field
# --------------------------------------------------------------------------
@app.middleware("http")
async def strip_full_text(request, call_next):
    response = await call_next(request)
    if isinstance(response, JSONResponse):
        # Defensive: if anything ever serializes full_text, drop it. The code
        # above never selects it, so this is a belt-and-braces guard only.
        body = response.body
        try:
            payload = json.loads(body)
            _scrub(payload)
            response.body = json.dumps(payload).encode("utf-8")
        except Exception:
            pass
    return response


def _scrub(obj):
    if isinstance(obj, dict):
        obj.pop("full_text", None)
        for v in obj.values():
            _scrub(v)
    elif isinstance(obj, list):
        for v in obj:
            _scrub(v)
