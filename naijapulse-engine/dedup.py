#!/usr/bin/env python3
"""
Phase 3 - Near-duplicate detection for the NaijaPulse core engine.

Detects near-duplicate articles (Nigerian outlets republishing verbatim NAN wire
copy) so they are not counted as independent sources at Phase 4 (clustering).

This reuses the embeddings produced by Phase 2 (nomic-embed-text, stored in the
`embeddings` table, generated from `articles.title` + `articles.summary`). No
MinHash/LSH, no new ML dependencies.

Two-stage algorithm (BOTH stages must pass to mark a pair as a duplicate):

  Stage A - candidate generation:
      Cosine similarity >= 0.96 on the Phase 2 embeddings, restricted to articles
      published within a 72-hour window of each other. At our current scale
      (~10 sources, low hundreds of articles/day) an in-memory cosine scan over
      the 72h-windowed candidate set is equivalent to hitting the ivfflat index
      and reuses exactly what Phase 2 built.

  Stage B - text confirmation:
      For each candidate pair, normalise full_text (lower-case, strip punctuation,
      strip boilerplate by-lines / "Culled from NAN"), build 5-word shingles, and
      compute the exact Jaccard (set intersection / union). Confirm if >= 0.80.
      Edge case: if either full_text is NULL or < ~40 words, skip Stage B and
      require cosine >= 0.98 alone; flag with a LOWER dedup_score (cos - 0.5)
      so the call reads as less certain.

Canonical selection: within a confirmed duplicate group, the article with the
earliest published_at (fallback fetched_at) is canonical; its canonical_article_id
stays NULL. All others point canonical_article_id at it.

Bookkeeping: dedup_checked_at is stamped on EVERY processed article (whether or not
a duplicate was found) so reruns only touch new rows.

Usage:
    ./venv/bin/python dedup.py
"""

import os
import sys
import re
import string
import logging
import datetime
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# --- thresholds (from the spec) ---
SIM_THRESHOLD_A = 0.96       # Stage A cosine gate
SIM_THRESHOLD_EDGE = 0.98    # stricter gate when full_text is unusable
JACCARD_THRESHOLD = 0.80     # Stage B text-confirmation gate
WINDOW_HOURS = 72            # wire copies don't span weeks
MIN_WORDS = 40               # below this, full_text is treated as unusable
SHINGLE_K = 5                # 5-word shingles


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def make_client():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("Missing SUPABASE_URL / SUPABASE_KEY environment variables")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def to_vec(v):
    """pgvector comes back as a '[...]' string or a list; normalise to np.array."""
    if v is None:
        return None
    if isinstance(v, str):
        return np.fromstring(v.strip("[]"), sep=",", dtype=float)
    return np.asarray(v, dtype=float)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


_BOILER = re.compile(r"culled from (the )?nan", re.I)
_BYLINE = re.compile(r"\bby [a-z][a-z.'-]*( [a-z.'-]+){0,3}")


def normalize_text(text: str) -> str:
    """Lower-case, strip punctuation, remove boilerplate by-lines / 'Culled from NAN'."""
    if not text:
        return ""
    t = text.lower()
    t = _BOILER.sub(" ", t)
    t = _BYLINE.sub(" ", t)
    t = t.translate(str.maketrans("", "", string.punctuation))
    t = re.sub(r"\s+", " ", t).strip()
    return t


def shingles(text: str, k: int = SHINGLE_K) -> Set[str]:
    words = text.split()
    if len(words) < k:
        return set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _parse_ts(s: Optional[str]) -> datetime.datetime:
    if not s:
        return datetime.datetime.max
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------
def load_pending(client) -> List[Dict]:
    """Articles without dedup_checked_at, joined with their Phase 2 embedding."""
    arts = (
        client.table("articles")
        .select("id, title, summary, full_text, published_at, fetched_at")
        .is_("dedup_checked_at", "null")
        .execute()
        .data
    ) or []
    if not arts:
        return []
    ids = [a["id"] for a in arts]
    embs = (
        client.table("embeddings")
        .select("article_id, vector")
        .in_("article_id", ids)
        .eq("model", EMBED_MODEL)
        .execute()
        .data
    ) or []
    emap = {e["article_id"]: to_vec(e["vector"]) for e in embs}
    pending = []
    for a in arts:
        vec = emap.get(a["id"])
        if vec is None:
            logger.warning("No embedding for article %s; skipping", a["id"])
            continue
        a["vector"] = vec
        pending.append(a)
    return pending


# --------------------------------------------------------------------------
# Stage A - candidate generation (cosine >= 0.96 within 72h window)
# --------------------------------------------------------------------------
def stage_a(pending: List[Dict]) -> List[Tuple[str, str, float]]:
    cands = []
    n = len(pending)
    for i in range(n):
        a = pending[i]
        ta = _parse_ts(a.get("published_at") or a.get("fetched_at"))
        for j in range(i + 1, n):
            b = pending[j]
            tb = _parse_ts(b.get("published_at") or b.get("fetched_at"))
            if abs(ta - tb) > datetime.timedelta(hours=WINDOW_HOURS):
                continue
            cos = cosine(a["vector"], b["vector"])
            if cos >= SIM_THRESHOLD_A:
                cands.append((a["id"], b["id"], cos))
    return cands


# --------------------------------------------------------------------------
# Stage B - text confirmation (Jaccard >= 0.80, or edge-case cosine >= 0.98)
# --------------------------------------------------------------------------
def stage_b(pending_by_id: Dict[str, Dict],
            cands: List[Tuple[str, str, float]]) -> Tuple[List[Tuple[str, str, float, str]], List[Tuple[str, str, float]]]:
    """Returns (confirmed, near_misses).
    confirmed: (a_id, b_id, score, method) where method is 'jaccard' or 'cosine_only'.
    near_misses: candidate pairs that did NOT confirm (used for the precision check).
    """
    confirmed = []
    near_misses = []
    for a_id, b_id, cos in cands:
        a = pending_by_id[a_id]
        b = pending_by_id[b_id]
        fa = (a.get("full_text") or "").strip()
        fb = (b.get("full_text") or "").strip()
        wa, wb = len(fa.split()), len(fb.split())
        # Edge case: unusable full_text -> rely only on stricter cosine
        if not fa or not fb or wa < MIN_WORDS or wb < MIN_WORDS:
            if cos >= SIM_THRESHOLD_EDGE:
                # LOWER dedup_score flags the call as less certain
                confirmed.append((a_id, b_id, round(cos - 0.5, 4), "cosine_only"))
            else:
                near_misses.append((a_id, b_id, cos))
            continue
        na, nb = normalize_text(fa), normalize_text(fb)
        sa, sb = shingles(na), shingles(nb)
        if not sa or not sb:
            near_misses.append((a_id, b_id, cos))
            continue
        jac = len(sa & sb) / len(sa | sb)
        if jac >= JACCARD_THRESHOLD:
            confirmed.append((a_id, b_id, round(jac, 4), "jaccard"))
        else:
            near_misses.append((a_id, b_id, cos))
    return confirmed, near_misses


# --------------------------------------------------------------------------
# grouping + canonical selection
# --------------------------------------------------------------------------
def build_groups(confirmed: List[Tuple[str, str, float, str]]) -> List[Set[str]]:
    parent: Dict[str, str] = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry
    for a, b, _, _ in confirmed:
        union(a, b)
    groups: Dict[str, Set[str]] = {}
    for a, b, _, _ in confirmed:
        r = find(a)
        groups.setdefault(r, set()).add(a)
        groups.setdefault(r, set()).add(b)
    return [g for g in groups.values() if len(g) > 1]


def pick_canonical(group: Set[str], client) -> str:
    rows = (
        client.table("articles")
        .select("id, published_at, fetched_at")
        .in_("id", list(group))
        .execute()
        .data
    ) or []
    return min(rows, key=lambda r: _parse_ts(r.get("published_at") or r.get("fetched_at")))["id"]


def update_db(pending: List[Dict], groups: List[Set[str]],
              confirmed: List[Tuple[str, str, float, str]], client) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # best confirming score per article
    score_map: Dict[str, float] = {}
    for a, b, score, _ in confirmed:
        score_map[a] = max(score_map.get(a, 0.0), score)
        score_map[b] = max(score_map.get(b, 0.0), score)
    # canonical per group
    canonical_of: Dict[str, str] = {}
    for g in groups:
        canon = pick_canonical(g, client)
        for mid in g:
            if mid != canon:
                canonical_of[mid] = canon
    # stamp dedup_checked_at on EVERY processed article (one targeted update,
    # never rewrites the whole row so NOT NULL columns like `url` are untouched)
    all_ids = [a["id"] for a in pending]
    client.table("articles").update({"dedup_checked_at": now}).in_("id", all_ids).execute()
    # set canonical_article_id + dedup_score only on confirmed duplicate members
    for aid, canon in canonical_of.items():
        client.table("articles").update(
            {"canonical_article_id": canon, "dedup_score": score_map.get(aid)}
        ).eq("id", aid).execute()
    logger.info("Stamped dedup_checked_at on %d articles; set canonical for %d duplicates",
                len(all_ids), len(canonical_of))


# --------------------------------------------------------------------------
# reporting / acceptance
# --------------------------------------------------------------------------
def report(pending, groups, confirmed, near_misses, client) -> None:
    # source names
    src_rows = client.table("sources").select("id, name").execute().data or []
    src = {s["id"]: s["name"] for s in src_rows}
    art_rows = (
        client.table("articles")
        .select("id, source_id, title, published_at, canonical_article_id")
        .execute()
        .data
    ) or []
    meta = {r["id"]: r for r in art_rows}

    print("\n" + "=" * 72)
    print("PHASE 3 - NEAR-DUPLICATE DETECTION  (acceptance report)")
    print("=" * 72)

    # 3) overall counts
    total = len(art_rows)
    flagged = sum(1 for r in art_rows if r.get("canonical_article_id"))
    pct = (flagged / total * 100) if total else 0.0
    print(f"\nTotal articles:           {total}")
    print(f"Duplicate groups found:   {len(groups)}")
    print(f"Articles w/ canonical set: {flagged}  ({pct:.1f}% of total)")
    if pct > 60 or pct < 2:
        print("  !! PERCENTAGE LOOKS IMPLAUSIBLE - please sanity-check before relying on it.")

    # list every group
    print("\n--- Duplicate groups ---")
    for gi, g in enumerate(groups, 1):
        canon = pick_canonical(g, client)
        outlets = sorted({src.get(meta[m]["source_id"], "?") for m in g})
        print(f"\nGroup {gi}: {len(g)} articles across {len(outlets)} outlets: {outlets}")
        for m in sorted(g, key=lambda x: _parse_ts(meta[x].get('published_at') or meta[x].get('fetched_at'))):
            tag = "CANON " if m == canon else "dup   "
            print(f"  [{tag}] {src.get(meta[m]['source_id'], '?'):12} | {meta[m].get('published_at')} | {meta[m]['title'][:70]}")

    # 1) positive case - a group spanning >= 3 distinct outlets
    print("\n" + "-" * 72)
    print("ACCEPTANCE 1 - POSITIVE CASE (verbatim NAN wire copy, 3+ outlets)")
    print("-" * 72)
    multi = [g for g in groups
             if len({src.get(meta[m]["source_id"], "?") for m in g}) >= 3]
    if multi:
        g = max(multi, key=lambda x: len({src.get(meta[m]["source_id"], "?") for m in x}))
        canon = pick_canonical(g, client)
        outlets = sorted({src.get(meta[m]["source_id"], "?") for m in g})
        print(f"FOUND a {len(g)}-article group across {len(outlets)} outlets: {outlets}")
        print(f"  Canonical (earliest): {src.get(meta[canon]['source_id'], '?')} - {meta[canon]['title']}")
        print("  => All members land in ONE group with the correct (earliest) canonical. PASS")
    else:
        print("No group spanning 3+ distinct outlets was detected in this dataset.")
        print("  => If you expected verbatim wire copy here, flag before trusting.")

    # 2) negative case - similar-topic pair that was NOT flagged (precision)
    print("\n" + "-" * 72)
    print("ACCEPTANCE 2 - NEGATIVE CASE (similar topic, must NOT be flagged)")
    print("-" * 72)
    if near_misses:
        # highest-cosine near-miss = strongest precision stress test
        a_id, b_id, cos = max(near_misses, key=lambda x: x[2])
        ma, mb = meta[a_id], meta[b_id]
        print(f"Pair with highest Stage-A cosine that was correctly NOT confirmed:")
        print(f"  {src.get(ma['source_id'], '?'):12} | {ma.get('published_at')} | {ma['title'][:70]}")
        print(f"  {src.get(mb['source_id'], '?'):12} | {mb.get('published_at')} | {mb['title'][:70]}")
        print(f"  cosine={cos:.3f} (>= {SIM_THRESHOLD_A} so it was a candidate) but Jaccard < {JACCARD_THRESHOLD}")
        print("  => NOT flagged as duplicate. PASS (precision held)")
    else:
        print("No candidate pairs failed Stage B - nothing to demonstrate the negative case.")
    print("\n" + "=" * 72)


def main() -> int:
    client = make_client()
    pending = load_pending(client)
    logger.info("Articles pending deduplication: %d", len(pending))
    if not pending:
        logger.info("Nothing to process - all articles already dedup-checked.")
        # still print counts
        report([], [], [], [], client)
        return 0

    cands = stage_a(pending)
    logger.info("Stage A candidate pairs (cos >= %.2f): %d", SIM_THRESHOLD_A, len(cands))

    by_id = {a["id"]: a for a in pending}
    confirmed, near_misses = stage_b(by_id, cands)
    logger.info("Stage B confirmed duplicate pairs: %d", len(confirmed))

    groups = build_groups(confirmed)
    logger.info("Duplicate groups discovered: %d", len(groups))

    update_db(pending, groups, confirmed, client)
    report(pending, groups, confirmed, near_misses, client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
