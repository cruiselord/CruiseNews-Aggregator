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
import uuid
from collections import Counter
from functools import lru_cache
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
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


def _fetch_canonical_counts_by_source(client) -> Dict[str, int]:
    """source_id -> canonical article count, computed in SQL (Finding 9).

    Pushes the per-source count aggregation into Postgres via the
    canonical_counts_by_source() RPC so the API never pulls the entire
    articles table across the network. Returns one row per source instead of
    one row per article.
    """
    rows = client.rpc("canonical_counts_by_source").execute().data or []
    return {r["source_id"]: int(r["canonical_count"]) for r in rows}


def _fetch_article_counts_by_source(client) -> Dict[str, Dict[str, int]]:
    """source_id -> {total_count, canonical_count}, computed in SQL (Finding 9).

    /pipeline-health needs BOTH the total and the canonical count per source,
    so it uses article_counts_by_source(), which returns both in one row per
    source, instead of scanning the full articles table.
    """
    rows = client.rpc("article_counts_by_source").execute().data or []
    return {r["source_id"]: {"total": int(r["total_count"]),
                             "canonical": int(r["canonical_count"])}
            for r in rows}


def _fetch_canonical_titles_by_story(client) -> Dict[str, List[str]]:
    """cluster_id -> list of canonical member headlines.

    Used to compute is_political_topic per story (representative_title +
    member headlines) without N round-trips.

    NOTE: this one is intentionally NOT pushed to SQL (Finding 9). It needs the
    actual title *text* back (not just counts), so a full transfer of the
    cluster_id/title columns is unavoidable here — the SQL-side alternative only
    helps when you want aggregates, not row data. Kept as a deliberate exception.
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
import logging
import time

# Configure logger for detailed request/response logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("/tmp/phase6_api.log"), logging.StreamHandler()],
)

app = FastAPI(
    title="NaijaPulse Engine - Phase 6 Read-only API",
    version="6.0.0",
    description="Read-only HTTP surface over the pipeline's Supabase data.",
)

# CORS: the UI is served from this same process at "/" (same-origin, no CORS
# needed there), but it can also be opened standalone (file://) or from another
# localhost port, in which case the browser needs CORS to call the API. This is
# a read-only GET surface, so a permissive origin list is acceptable here —
# pair it with RLS + anon-SELECT-only policies before any production exposure.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# Middleware to log each request's method, path, status, and duration
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        try:
            response: StarletteResponse = await call_next(request)
            status = response.status_code
        except Exception as exc:
            status = 500
            raise
        finally:
            duration = (time.time() - start) * 1000  # ms
            logging.info(
                f"{request.method} {request.url.path} -> {status} in {duration:.2f}ms"
            )
        return response

app.add_middleware(LoggingMiddleware)



@app.get("/")
def root():
    # Serve the front-end at the root when it is present; this beats the
    # static mount for the exact "/" path (explicit routes win). When the UI
    # folder is absent we fall back to a small API info document.
    if os.path.isdir(_UI_DIR):
        return FileResponse(os.path.join(_UI_DIR, "index.html"))
    return {
        "service": "naijapulse-engine phase6 read-only api",
        "status": "ok",
        "endpoints": ["/stories", "/stories/{id}", "/sources",
                      "/pipeline-health"],
        "note": "read-only; article bodies are never returned",
    }


@app.get("/api")
def api_info():
    return {
        "service": "naijapulse-engine phase6 read-only api",
        "status": "ok",
        "endpoints": ["/stories", "/stories/{id}", "/sources",
                      "/pipeline-health"],
        "note": "read-only; article bodies are never returned",
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
    # A malformed id (e.g. a stale/expired link) is "not found", not a 500.
    try:
        uuid.UUID(story_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="story not found")

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
    # Chunked: a single .in_() over all canonical ids can blow past PostgREST's
    # query-size ceiling (same class of bug as Finding 7 in ingest_supabase).
    also_counts: Dict[str, int] = {}
    if canonical_ids:
        dup_rows = []
        for i in range(0, len(canonical_ids), 100):
            batch = canonical_ids[i:i + 100]
            dup_rows.extend(
                (client.table("articles")
                 .select("canonical_article_id")
                 .in_("canonical_article_id", batch)
                 .execute()
                 .data) or []
            )
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
                 .select("source_id, ownership_lean, confidence, notes, "
                         "regional_base")
                 .execute()
                 .data) or []
    bias_by_source = {r["source_id"]: r for r in bias_rows}

    # Live count of canonical articles contributed per source.
    # Finding 9: computed in SQL via RPC, not by scanning the whole articles
    # table in Python on every request.
    canonical_per_source = _fetch_canonical_counts_by_source(client)

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
            "regional_base": b.get("regional_base") if b else None,
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
    # Finding 9: computed in SQL via RPC (one row per source) instead of
    # scanning the whole articles table in Python on every request.
    counts_by_source = _fetch_article_counts_by_source(client)
    sources_map = _fetch_sources_map(client)
    per_source = [
        {
            "source_id": sid,
            "source_name": sources_map.get(sid),
            "total_articles": c["total"],
            "canonical_articles": c["canonical"],
        }
        for sid, c in counts_by_source.items()
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
async def _read_response_body(response) -> bytes:
    """Materialize a response body regardless of response class.

    FastAPI/Starlette may return a JSONResponse (which exposes a ready ``body``
    bytes attribute) or — in newer versions, e.g. 0.139 / Starlette 1.3.1 — a
    StreamingResponse whose body is an unconsumed async iterator. Handle both.
    """
    body = getattr(response, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    # StreamingResponse path: consume the iterator.
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        chunks.append(chunk)
    return b"".join(chunks)


@app.middleware("http")
async def strip_full_text(request, call_next):
    response = await call_next(request)
    # Robustly detect a JSON body regardless of response class. Check the
    # content-type rather than isinstance(JSONResponse), because newer
    # FastAPI/Starlette wrap dict returns as a StreamingResponse.
    ctype = response.headers.get("content-type", "") or ""
    if "application/json" not in ctype:
        return response
    try:
        raw = await _read_response_body(response)
        payload = json.loads(raw)
        _scrub(payload)
        new_body = json.dumps(payload).encode("utf-8")
        # Build a fresh Response so headers (Content-Length included) are
        # recomputed from the new body, instead of mutating body in place
        # on an already-constructed Response.
        return Response(
            content=new_body,
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items()
                     if k.lower() != "content-length"},
            media_type=response.media_type,
        )
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


# --------------------------------------------------------------------------
# static UI mount
# --------------------------------------------------------------------------
# Serve the front-end from this same process so the app is reachable at "/"
# (same-origin -> no CORS needed for the in-app fetches). FastAPI routes above
# take precedence over this mount, so /stories etc. still hit the API.
_UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
if os.path.isdir(_UI_DIR):
    app.mount("/", StaticFiles(directory=_UI_DIR, html=True), name="ui")
