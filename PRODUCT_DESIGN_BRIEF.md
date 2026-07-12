# NaijaPulse — Product & Design Brief (Phases 1–6)

**Audience:** Design agent → iOS / Android / Web engineering teams
**Author:** Technical Product Manager (TPM), NaijaPulse
**Status:** Backend complete & verified (Phases 1–5 pipeline + Phase 6 read-only API). This document defines the **front-end product**: what to build, why, and the edge cases that matter.
**How to use this doc:** Sections 1–5 are the "why." Sections 6–9 are the "what to design." Section 10–13 are the "how to build/structure it." Section 14 is the handoff checklist.

---

## 1. Vision & Positioning

**NaijaPulse makes Nigerian news *legible*.** It ingests articles from the country's major outlets, groups articles about the same real-world event into a single **Story**, and shows — transparently — *how the media landscape is covering that event*, including **who is (and isn't) covering it**, and **whether coverage is lopsided along pro/anti-government lines**.

We are deliberately **not** a news reader. We are a **media-bias & coverage-transparency layer** for Nigeria. Think *Ground News*, but born in Nigeria, with a local ownership-lean taxonomy and a purpose-built **"blindspot" detector** for lopsided political coverage.

> **One-line positioning:** *"See every Nigerian outlet's take on the same story — and spot who's missing from the conversation."*

---

## 2. What we've already built (backend recap)

A 5-stage data pipeline feeds a read-only API. The front end consumes **only** that API.

| Phase | Name | Output |
|------|------|--------|
| 1 | Ingestion | RSS → `articles` (title, summary, full_text, image_url, source) + `ingest_report.json` |
| 2 | Embeddings | Local Ollama `nomic-embed-text` vectors of `title+summary` → `embeddings` |
| 3 | De-duplication | Near-duplicate wire-copy detection → `canonical_article_id` (one canonical per group) |
| 4 | Clustering | Canonical articles → `stories` (same-event clusters); `representative_title` |
| 5 | Bias / Blindspot | Per-story `bias_distribution`, `bias_coverage_pct`, `is_blindspot`; `source_bias` table |
| 6 | **Read-only API** | `GET` endpoints over Supabase (the only surface the UI talks to) |

**Hard backend constraints the UI must respect:**
- **GET-only, no auth.** The API never writes. There is **no login, no user accounts**. Design accordingly — no auth screens.
- **`full_text` is NEVER returned.** The UI shows headline + summary + outbound link to the source. **No in-app article reader.** This is a legal/biz boundary (Ground News model).
- **Read-only snapshot.** The API reflects the last pipeline run. There is no real-time mutation from the UI.

---

## 3. Domain model (the nouns the UI renders)

```
Source           — a news outlet (Punch, Vanguard, TheCable, …)
  ├─ ownership_lean : mixed | independent | pro_government | anti_government | null
  ├─ confidence    : high | medium | low | null
  └─ notes         : free text

Article          — one published item
  ├─ canonical?    : if duplicate, points at its canonical article
  ├─ source        : → Source
  └─ also_reported_by : # of other outlets carrying the same story (dedup convergence)

Story            — a cluster of same-event articles (the core unit)
  ├─ representative_title
  ├─ article_count
  ├─ bias_distribution : {mixed, independent, pro_government, anti_government}
  ├─ bias_coverage_pct  : % of members whose source is bias-tagged
  ├─ is_political_topic : computed (keyword scan)
  ├─ is_blindspot       : lopsided pro/anti-government coverage
  └─ members[]          : canonical articles (duplicates collapsed)
```

**The 4 bias leans** (this taxonomy is a product differentiator — see §11):
`mixed`, `independent`, `pro_government`, `anti_government`.

---

## 4. API contract (exact — build the client from this)

Base URL (local): `http://localhost:8000`. All responses are JSON. All methods `GET`.

### `GET /stories` — list (paginated, filterable)
Query params: `offset` (default 0), `limit` (default 20, max 500), `is_blindspot` (bool), `min_articles` (int), `is_political_topic` (bool), `sort` (`last_updated_at` | `article_count`, default `last_updated_at` desc).

```json
{
  "total": 163,
  "offset": 0,
  "limit": 20,
  "stories": [
    {
      "id": "uuid",
      "representative_title": "After Oyo Schoolchildren, Teachers Regain Freedom, ADC Demands Rescue of Borno, Kwara Kidnap Victims",
      "article_count": 27,
      "bias_distribution": {"mixed": 19, "independent": 6, "pro_government": 2, "anti_government": 0},
      "is_blindspot": false,
      "is_political_topic": true,
      "bias_coverage_pct": 100.0,
      "first_seen_at": "2026-07-12T08:34:12+00:00",
      "last_updated_at": "2026-07-12T19:55:23+00:00"
    }
  ]
}
```
*No member articles in the list view — that's the detail endpoint.*

### `GET /stories/{id}` — one story + members
```json
{
  "id": "uuid",
  "representative_title": "…",
  "article_count": 27,
  "bias_distribution": {"mixed": 19, "independent": 6, "pro_government": 2, "anti_government": 0},
  "is_blindspot": false,
  "is_political_topic": true,
  "bias_coverage_pct": 100.0,
  "first_seen_at": "…",
  "last_updated_at": "…",
  "members": [
    {
      "title": "…", "summary": "…", "url": "https://…", "image_url": "https://…|null",
      "source_name": "Punch", "published_at": "…", "also_reported_by": 2
    }
  ]
}
```
**404** (not 200) if the id doesn't exist.

### `GET /sources` — outlets + bias + volume
```json
{
  "total": 12,
  "sources": [
    {
      "id": "uuid", "name": "Punch", "homepage_url": "https://punchng.com",
      "country": "NG", "active": true, "created_at": "…",
      "ownership_lean": "mixed", "confidence": "high", "notes": "…",
      "canonical_article_count": 42
    }
  ]
}
```
`ownership_lean` / `confidence` / `notes` are **null** when a source has no `source_bias` row — design a graceful "unknown" state.

### `GET /pipeline-health` — data-quality / trust dashboard
```json
{
  "total_articles": 500,
  "total_canonical_articles": 498,
  "total_stories": 163,
  "per_source_article_counts": [
    {"source_id": "uuid", "source_name": "Punch", "total_articles": 44, "canonical_articles": 42}
  ],
  "rss_feed_status": {
    "feeds_total": 10, "feeds_success": 10, "feeds_failed": 0,
    "feeds_gnews_fallback": 4, "feed_success_rate": 100.0,
    "extraction_success": 203, "extraction_success_rate": 67.89
  },
  "min_sample_gate": {"threshold": 3, "stories_below": 116},
  "bias_coverage_buckets": {"100%": 163, "50-99%": 0, "1-49%": 0, "0%": 0},
  "topic_gate": {"stories_excluded_by_topic_gate": 88},
  "blindspot": {"flagged": 0, "eligible_political_and_tagged_ge_3": 30}
}
```

### `GET /` — health/info (used for liveness checks).

---

## 5. Personas

1. **The Civic Reader** (primary) — a Nigerian who wants to understand *both sides / all outlets* on a big story and distrust single-source narratives.
2. **The Journalist / Researcher** — needs to see coverage gaps and source lean quickly; exports/screenshots.
3. **The Media-Literacy Educator** — uses the bias visualizations to teach how outlets differ.
4. **(Internal) The Ops/Trust viewer** — watches `pipeline-health` to know if the data is fresh and complete.

---

## 6. User stories (epics → granular)

**Epic A — Discover & read stories**
- As a reader, I want a ranked feed of current Stories so I can see what Nigeria is talking about.
- As a reader, I want to open a Story and see every outlet's headline + a link, so I can compare framing.
- As a reader, I want to see *"N outlets reported this"* so I know how big/convergent the story is.
- As a reader, I want to filter by political topic / min article count so I can focus.

**Epic B — Understand bias & blindspots**
- As a reader, I want each Story to show its bias distribution (mixed/independent/pro/anti) as a visual, not numbers.
- As a reader, I want a clear "⚠ Blindspot" flag when political coverage is lopsided, with an explanation.
- As a reader, I want to know *coverage confidence* (`bias_coverage_pct`) so I don't over-trust thin samples.

**Epic C — Source transparency**
- As a reader, I want to browse Sources and see each outlet's ownership lean + confidence.
- As a reader, I want to see how much each Source contributed (volume).

**Epic D — Trust & freshness**
- As any user, I want a "data health" view so I know the numbers are current and complete.
- As a user, I want honest empty states (e.g., "no blindspots yet") rather than fake data.

**Epic E — Cross-platform parity**
- As a user, I expect the same core flows on iOS, Android, and Web, with platform-native feel.

---

## 7. Information architecture & screen inventory

**Shared screen set (all three platforms):**
1. **Stories feed** — list of Story cards, sort/filter controls.
2. **Story detail** — representative title, bias donut, blindspot badge, member list (outlet chips + "N outlets"), link-outs.
3. **Sources** — grid/list of outlet cards (lean badge, confidence, volume).
4. **Source detail** (optional/stretch) — one outlet's lean profile + its stories.
5. **Blindspots** (filter view of Stories where `is_blindspot=true`) — currently empty; must handle gracefully.
6. **Pipeline health / About-data** — trust dashboard (feeds, extraction, coverage, min-sample gate).

**Navigation patterns:**
- **Mobile (iOS/Android):** bottom tab bar → `Stories · Blindspots · Sources · Health`. Filters open as a sheet/modal.
- **Web:** left sidebar (nav) + main content + sticky filter bar. More room for the bias donut + member grid side-by-side.

---

## 8. Core features to showcase (the "beauty" of the backend)

1. **The Bias Donut / Stacked Bar** — the hero visualization. One Story → 4-segment distribution. *This is the product's signature visual.* Make it gorgeous and instantly readable (color-coded leans, legend, hover/tap for counts).
2. **"N outlets reported this" (convergence)** — `also_reported_by` per member. Frame as *media convergence*: "Reported by Punch, Vanguard + 2 more." This is a Ground News-style differentiator.
3. **Blindspot badge** — when `is_blindspot=true`, a prominent, explainable warning: *"Heavy pro-government coverage, zero anti-government — on a political story."* Even at 0 today, the UI must treat it as a first-class, rare, high-value state.
4. **Coverage confidence meter** — `bias_coverage_pct` + min-sample gate. Show "based on 27 tagged articles" vs "only 2 — low confidence."
5. **Source transparency cards** — ownership lean as a colored chip, confidence as a small label, volume as a count.
6. **Pipeline health / trust screen** — turns dry numbers (feeds 10/10, extraction 67.89%, coverage 100%) into a credibility story. Great for differentiation and for an "About / Methodology" section.

---

## 9. Edge cases & empty/error states (design these — they're common, not rare)

| Case | Backend reality | UI must… |
|------|----------------|-----------|
| **No blindspots** | `blindspot.flagged = 0` (today) | Show a calm "No blindspots detected yet" empty state. Do **not** fake or hide the tab. |
| **Empty filter result** | `is_blindspot=true` → `total: 0` | Show a friendly empty state + "clear filters" CTA. Never a blank screen. |
| **Unknown source lean** | `ownership_lean: null` | Render a neutral "Lean: unknown" chip, not an error. |
| **Low coverage / thin sample** | `bias_coverage_pct < 100` or `article_count < 3` | Show reduced-confidence treatment (muted donut, "low sample" note). Don't present as definitive. |
| **Non-political story** | `is_political_topic: false` | Still show it; just suppress blindspot/bias emphasis (it's sports/entertainment/etc.). |
| **Missing image** | `image_url: null` | Use a branded placeholder, never a broken image. |
| **Missing summary** | `summary: null` | Fall back to title only. |
| **Bad story id** | API returns **404** | Friendly not-found screen with "back to stories." |
| **Stale data** | API is a snapshot | Surface "last updated" (`last_updated_at`) so users know freshness. |
| **Feed/extraction issues** | `feed_success_rate` / `extraction_success_rate` may dip | Health screen shows it honestly; don't mask. |
| **Long representative titles** | Some are very long | Design title typography to wrap/clamp gracefully. |
| **`also_reported_by = 0`** | Canonical with no duplicates | Show "1 outlet" / no convergence badge — normal. |

---

## 10. What makes us stand out (differentiators — lean into these)

1. **Nigeria-native, not a Western import.** No major product does multi-outlet Nigerian bias transparency. Lead with local relevance.
2. **The Blindspot detector.** Pro/anti-government *lopsidedness* surfaced explicitly — a uniquely Nigerian governance-angle feature (not just left/right).
3. **Convergence ("N outlets reported this").** Dedup-powered proof of how the media converges or diverges on one event.
4. **Radical transparency.** We *show* our own data quality (`pipeline-health`): feeds, extraction rate, coverage. Trust by disclosure.
5. **Event-level clustering.** One Story = one real-world event across all outlets, with a single comparable bias view.
6. **4-lean taxonomy** (`mixed / independent / pro_government / anti_government`) — richer than binary left/right.

---

## 11. Design principles & visual direction

- **Calm, credible, journalistic.** Newsroom aesthetic, not social-media noise. Generous whitespace, strong typographic hierarchy.
- **Color = lean, consistently.** Fix a canonical palette: e.g. `pro_government` = one hue, `anti_government` = another, `mixed` = blend, `independent` = neutral. Use it **everywhere** (donut, chips, legends) so color becomes learnable.
- **Data-ink first.** The bias donut and convergence count are the heroes; chrome should recede.
- **Explainability over flash.** Every bias/blindspot UI element should be self-explaining or one tap from an explanation (methodology modal).
- **Honest emptiness.** Empty/low-confidence states are designed, not defaulted.
- **Dark + light** themes recommended (news consumers read at night).

---

## 12. Proposed cross-platform file / folder structure

Monorepo with a **single design-token source of truth** consumed by all three platforms.

```
naijapulse/
├─ apps/
│  ├─ web/                 # Next.js (App Router) + TypeScript + Tailwind
│  │   ├─ app/
│  │   │   ├─ (tabs)/stories/page.tsx
│  │   │   ├─ stories/[id]/page.tsx
│  │   │   ├─ sources/page.tsx
│  │   │   ├─ blindspots/page.tsx
│  │   │   └─ health/page.tsx
│  │   ├─ components/       # BiasDonut, StoryCard, SourceChip, ConvergenceBadge, HealthPanel
│  │   ├─ lib/api.ts        # typed fetch client (params → URL)
│  │   └─ types.ts          # Story, Source, Health (mirror API §4)
│  ├─ ios/                 # Xcode project, SwiftUI
│  │   ├─ NaijaPulse/Features/{Stories,Sources,Blindspots,Health}/
│  │   ├─ NaijaPulse/UI/Components/{BiasDonut,StoryCard,SourceChip}.swift
│  │   ├─ NaijaPulse/Data/APIClient.swift + Models.swift
│  │   └─ NaijaPulse/Design/Theme.swift   # consumes design tokens
│  └─ android/             # Android Studio, Kotlin + Jetpack Compose
│      ├─ app/src/main/java/.../feature/stories|sources|blindspots|health/
│      ├─ app/src/main/java/.../ui/component/{BiasDonut,StoryCard,SourceChip}.kt
│      ├─ app/src/main/java/.../data/ApiClient.kt + model/ (data classes)
│      └─ app/src/main/res/values/theme_tokens.xml
├─ packages/
│  ├─ design-tokens/       # tokens.json → CSS vars (web), Assets.xcassets (iOS), theme.xml (Android)
│  └─ api-types/           # platform-agnostic domain models (generated/agreed from §4)
├─ design/
│  ├─ figma-library.fig    # shared component library + Figma Variables (mirrors design-tokens)
│  └─ icons/
├─ docs/
│  ├─ PRODUCT_DESIGN_BRIEF.md   # this file
│  └─ API_CONTRACT.md           # the exact §4 contract, expanded
└─ README.md
```

**Key architectural rules for the eng teams:**
- The API client is **read-only GET**, no auth header needed. Base URL configurable (localhost in dev, env var in prod).
- Generate `types`/`Models`/`data classes` **once** from §4 and mirror across platforms — single source of truth in `packages/api-types`.
- **Design tokens are the contract between design and engineering.** Figma Variables ↔ `packages/design-tokens` ↔ each platform's native theme.

---

## 13. Interaction & navigation specifics

- **Stories feed:** pull-to-refresh (mobile) / refresh button (web); sort toggle (Recent / Most-covered); filter chips (Political, Min articles, Blindspots).
- **Story detail:** bias donut pinned at top; member list below as outlet rows, each tappable → opens source URL in browser (no in-app reader). Convergence badge on each.
- **Sources:** alphabetical or volume-sorted grid; lean chip + confidence label + volume count.
- **Health:** cards for Articles / Canonical / Stories, Feed status, Extraction rate, Coverage buckets, Blindspot eligible count. Include a "Methodology" expandable.
- **Deep linking:** `/stories/{id}` must be shareable (web) and handled by mobile route handlers.

---

## 14. Success metrics (what "good" looks like)

- **Adoption:** DAU/MAU on Stories feed; Story detail open rate.
- **Transparency engagement:** bias-donut interactions; Blindspots tab visits; Health screen visits.
- **Trust:** session includes a "methodology/health" view ≥ X%.
- **Retention:** return visits (hinting the feed stays fresh — depends on pipeline cadence).
- **Completeness:** eventually, real blindspots flagged (proves the detector works on live data).

---

## 15. Out of scope / future (do NOT design yet)
.
- Write/moderation endpoints (API is read-only by design).
- Real-time streaming (snapshot model).


---

## 16. Handoff checklist for the design agent

- [ ] Read §3 (API contract) + `docs/API_CONTRACT.md` — all UI data comes from here.
- [ ] Design the **Bias Donut** first (hero visual); lock the 4-lean color system.
- [ ] Design all **empty/error states** in §9 (they're expected, not exceptional).
- [ ] Produce **parallel explorations** for Web, iOS, and Android from the shared screen set (§7).
- [ ] Define **design tokens** (color/type/space/elevation) and export to Figma Variables.
- [ ] Include a **methodology/about** surface (ties to `pipeline-health` transparency).
- [ ] Respect constraints: **no login, no in-app reader, link-out only, GET-only.**

---

*Generated by the NaijaPulse TPM. Backend status: Phases 1–6 complete and curl-verified (see `PROGRESS.md`, `PHASE6_BUILD.md`, `HOW_TO_USE.md`). This brief is the authoritative front-end product definition until revised.*
