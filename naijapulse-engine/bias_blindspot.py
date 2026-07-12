#!/usr/bin/env python3
"""
Phase 5 — Bias tagging + blindspot detection for the NaijaPulse core engine.

For every cluster (= a row in the `stories` table — that is the real cluster
table; the legacy `clusters` table is empty/unused and `articles.cluster_id`
is a FK to `stories.id`), compute a bias distribution across its MEMBER
ARTICLES' source leanings and flag lopsided coverage as a blindspot.

COUNTING CONVENTION (must match Phase 3's intent — do NOT double-count wire
copy as independent coverage):
    * Only CANONICAL articles count. An article with canonical_article_id SET
      is a duplicate that inherited its cluster from its canonical article in
      Phase 4, so it is EXCLUDED here (counting it would re-inflate the lean
      tally, exactly what Phase 3 was built to prevent).
    * bias_distribution = count of CANONICAL member articles per normalized
      lean category (NOT distinct sources — the spec says "count articles").
    * bias_coverage_pct  = % of the cluster's canonical articles whose source
      has a source_bias row. Tells us how much to trust the distribution when
      some sources aren't tagged yet.
    * is_blindspot       = see rule documented in _evaluate_blindspot().

This script is the SINGLE OWNER of all four bias columns on `stories`
(bias_distribution, is_blindspot, bias_coverage_pct, blindspot_checked_at).
The old Stage E inside cluster_stories.py has been neutralized so it no longer
writes these columns.

Usage:
    ./venv/bin/python bias_blindspot.py            # recompute all clusters
"""

import os
import sys
import logging
import datetime
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Minimum number of bias-tagged canonical articles before we dare call a
# blindspot. Below this we stay silent (is_blindspot = False) rather than make
# claims we don't have the data to support.
MIN_SAMPLE_TAGGED = 3

# A lean is "dominant" (lopsided) at this many articles while another lean has 0.
DOMINANT_THRESHOLD = 3

# Keywords that mark a story as a POLITICAL topic. Blindspot evaluation only runs
# on political stories, so sports/entertainment/health/lifestyle items (which
# contain none of these terms) are never flagged. Substring match against the
# story's representative_title + member headlines, lowercased.
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
# normalization (Stage A)
# --------------------------------------------------------------------------
def _normalize_lean(raw: Optional[str]) -> Optional[str]:
    """Trim + lowercase for comparison. Whitespace/casing are collapsed so that
    'Opposition-Aligned', 'opposition aligned', 'OPPOSITION-ALIGNED' all map to
    the same bucket. Returns None for null/empty input."""
    if raw is None:
        return None
    s = raw.strip().lower()
    return s or None


def _collision_key(lean: str) -> str:
    """Aggressive key for near-duplicate detection: drop all non-alphanumerics.
    'opposition-aligned' and 'opposition aligned' both -> 'oppositionaligned'."""
    return "".join(ch for ch in lean if ch.isalnum())


def load_and_normalize_source_bias(client) -> Tuple[
        Dict[str, str], List[str], Set[str], List[Tuple[str, str]]]:
    """Return (lean_by_source, near_dupes_log, vocabulary, raw_distinct).

    lean_by_source : source_id -> normalized ownership_lean
    near_dupes_log : list of human-readable near-duplicate warnings
    vocabulary     : set of all distinct normalized leans (full category set,
                     used so bias_distribution always enumerates every lean,
                     including zeros)
    """
    rows = (client.table("source_bias")
            .select("source_id, ownership_lean")
            .execute()
            .data) or []

    lean_by_source: Dict[str, str] = {}
    # collision_key -> list of (raw, normalized) to detect near-dupes
    by_collision: Dict[str, List[Tuple[str, str]]] = {}
    raw_distinct: List[str] = []

    for r in rows:
        raw = r.get("ownership_lean")
        norm = _normalize_lean(raw)
        if norm is None:
            continue
        lean_by_source[r["source_id"]] = norm
        raw_distinct.append(raw)
        by_collision.setdefault(_collision_key(norm), []).append((raw, norm))

    # Flag near-duplicates: multiple DISTINCT normalized values that collapse to
    # the SAME collision key (e.g. 'opposition aligned' vs 'opposition-aligned'),
    # or multiple raw spellings mapping to one normalized value. We do NOT merge
    # them — we surface them for the operator to confirm.
    near_dupes_log: List[Tuple[str, str]] = []
    for key, variants in by_collision.items():
        distinct_norms = {v[1] for v in variants}
        distinct_raws = {v[0] for v in variants}
        if len(distinct_norms) > 1 or len(distinct_raws) > 1:
            near_dupes_log.append(
                (key, " | ".join(sorted(distinct_raws))))

    vocabulary = set(lean_by_source.values())
    return lean_by_source, near_dupes_log, vocabulary, raw_distinct


# --------------------------------------------------------------------------
# per-cluster computation (Stages B + C)
# --------------------------------------------------------------------------
def compute_cluster(client, sid: str, lean_by_source: Dict[str, str],
                    vocabulary: Set[str], missing_bias_sources: Set[str],
                    representative_title: Optional[str] = None) -> Dict:
    """Compute bias_distribution + coverage + blindspot flag for one cluster."""
    members = (client.table("articles")
               .select("id, source_id, title")
               .eq("cluster_id", sid)
               .is_("canonical_article_id", "null")  # canonical-only
               .execute()
               .data) or []

    total_canonical = len(members)

    # full vocabulary, initialized to zero so the distribution always lists
    # every lean (matches the spec example {"pro-establishment": 2,
    # "independent": 1, "opposition-aligned": 0}).
    counts = {lean: 0 for lean in vocabulary}
    tagged = 0  # canonical articles whose source HAS a source_bias row
    member_titles = []

    for m in members:
        s = m.get("source_id")
        lean = lean_by_source.get(s)
        if lean is None:
            if s:
                missing_bias_sources.add(s)
            continue
        counts[lean] += 1
        tagged += 1
        t = m.get("title")
        if t:
            member_titles.append(t)

    # bias_coverage_pct = % of canonical articles with a matching source_bias row
    bias_coverage_pct = (
        round(100.0 * tagged / total_canonical, 2) if total_canonical else 0.0
    )

    # Topic-relevance gate: blindspot detection only applies to POLITICAL
    # stories. Build the scan text from the representative title + member
    # headlines. Sports/entertainment/health/lifestyle stories are excluded
    # here, BEFORE the blindspot rule runs.
    scan_text = " ".join(
        [representative_title or ""] + member_titles
    )
    is_political = _is_political_topic(scan_text)

    # Only evaluate the blindspot rule on political stories.
    is_blindspot = _evaluate_blindspot(counts, tagged) if is_political else False

    return {
        "bias_distribution": counts,
        "bias_coverage_pct": bias_coverage_pct,
        "is_blindspot": is_blindspot,
        "is_political": is_political,
        "total_canonical": total_canonical,
        "tagged": tagged,
    }


def _is_political_topic(text: str) -> bool:
    """Return True if `text` (a story's representative_title + member headlines)
    looks like a political story. Substring match against POLITICAL_KEYWORDS.

    Stories about sports, entertainment, health, or lifestyle contain none of
    these terms, so they return False and never reach blindspot evaluation.
    """
    if not text:
        return False
    low = text.lower()
    return any(kw in low for kw in POLITICAL_KEYWORDS)


def _evaluate_blindspot(counts: Dict[str, int], tagged: int) -> bool:
    """Blindspot rule (corrected — directional leans ONLY):

    Compare ONLY the two directional leans, pro_government vs anti_government.
    `mixed`/`independent` count toward the minimum-sample gate (`tagged >=
    MIN_SAMPLE_TAGGED`) but are NEVER part of the flag comparison itself.

    Flag true only if one of {pro_government, anti_government} has
    >= DOMINANT_THRESHOLD (3) articles while the other has exactly 0.

    The minimum-sample gate must be met first (tagged >= MIN_SAMPLE_TAGGED),
    otherwise we stay silent — we don't make claims we lack the data to support.
    """
    if tagged < MIN_SAMPLE_TAGGED:
        return False
    pro = counts.get("pro_government", 0)
    anti = counts.get("anti_government", 0)
    return (pro >= DOMINANT_THRESHOLD and anti == 0) or (
        anti >= DOMINANT_THRESHOLD and pro == 0
    )


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def run_bias(client) -> Dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    lean_by_source, near_dupes, vocabulary, raw_distinct = \
        load_and_normalize_source_bias(client)

    if near_dupes:
        logger.warning("Stage A: %d near-duplicate lean value(s) detected — "
                       "NOT auto-merged, please confirm:", len(near_dupes))
        for key, variants in near_dupes:
            logger.warning("   collision_key=%r variants=%s", key, variants)
    else:
        logger.info("Stage A: %d distinct normalized lean(s); no near-duplicate "
                    "buckets: %s", len(vocabulary), sorted(vocabulary))

    stories = (client.table("stories")
               .select("id, representative_title")
               .execute()
               .data) or []
    missing_bias_sources: Set[str] = set()

    flagged = 0
    below_gate = 0
    non_political_excluded = 0
    updated = 0
    for st in stories:
        sid = st["id"]
        res = compute_cluster(client, sid, lean_by_source, vocabulary,
                              missing_bias_sources,
                              representative_title=st.get("representative_title"))
        client.table("stories").update({
            "bias_distribution": res["bias_distribution"],
            "bias_coverage_pct": res["bias_coverage_pct"],
            "is_blindspot": res["is_blindspot"],
            "blindspot_checked_at": now,
        }).eq("id", sid).execute()
        updated += 1
        if res["is_blindspot"]:
            flagged += 1
        if res["tagged"] < MIN_SAMPLE_TAGGED:
            below_gate += 1
        if not res["is_political"]:
            non_political_excluded += 1

    if missing_bias_sources:
        logger.warning("Stage B: %d source(s) had canonical articles but NO "
                       "source_bias row (excluded from counts, kept in coverage "
                       "denominator): %s", len(missing_bias_sources),
                       sorted(missing_bias_sources))

    stats = {
        "stories_total": len(stories),
        "stories_updated": updated,
        "blindspots_flagged": flagged,
        "below_min_sample_gate": below_gate,
        "non_political_excluded": non_political_excluded,
        "distinct_leans": len(vocabulary),
        "near_duplicate_leans": len(near_dupes),
        "sources_missing_bias": len(missing_bias_sources),
    }
    logger.info("Phase 5 done: %s", stats)
    return stats


def main() -> int:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("Missing SUPABASE_URL / SUPABASE_KEY environment variables")
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    stats = run_bias(client)
    print("\n" + "=" * 72)
    print("PHASE 5 - BIAS TAGGING + BLINDSPOT DETECTION  (run report)")
    print("=" * 72)
    for k, v in stats.items():
        print(f"  {k:24} {v}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
