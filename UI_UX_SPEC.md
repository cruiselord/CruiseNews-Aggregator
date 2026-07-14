# NaijaPulse — UI/UX Specification for AI Design Generation

**Purpose:** A self-contained brief for an AI UI/UX generator (e.g. v0.dev, Galileo, Figma AI, Uizard, or a design LLM) to produce screens for the NaijaPulse front end. Everything the generator needs — product context, data model, API contract, screen list, user stories, recommended visualizations, and a modern 2026 news aesthetic — is below. No codebase access required.

**Backend status (verified):** Phases 1–5 data pipeline + Phase 6 read-only API are complete and curl-verified. The front end consumes **only** the API described in §4.

---

## 1. What NaijaPulse is (the "why")

NaijaPulse is a **media-bias & coverage-transparency layer for Nigerian news**. It is **not** a news reader. It ingests articles from ~10 major Nigerian outlets, groups articles about the same real-world event into a single **Story**, and shows — transparently — how the media landscape is covering that event: which outlets reported it, whether coverage is lopsided along pro/anti-government lines (a **blindspot**), and how much of the sample is bias-tagged (a **confidence** measure).

> Positioning line: *"See every Nigerian outlet's take on the same story — and spot who's missing from the conversation."*

Inspirations: **Ground News** (multi-outlet comparison + bias) and modern digital newspapers (clean, fast, ad-supported).

---

## 2. Hard constraints the UI MUST respect

These come straight from the backend contract — violating them breaks the product or the law:

1. **GET-only, no auth.** The API never writes; there is **no login and no user accounts yet**. Do **not** design auth/login screens as functional — see §10 for "Coming soon" treatment.
2. **`full_text` is NEVER returned.** Show only `title`, `summary`, `image_url`, `source_name`, `published_at`, `url`, `also_reported_by`. **No in-app article reader.** Every article links **out** to the source site. This is a legal/biz boundary.
3. **Read-only snapshot.** The UI reflects the last pipeline run. There is no real-time mutation from the UI. Always show `last_updated_at` so users understand freshness.
4. **No fake data, no fake blindspots.** The backend is honest; the UI must be too (graceful empty states, not invented content).

---

## 3. Data model (the nouns the UI renders)

```
Source (news outlet)
  ├─ id, name, homepage_url, country ("NG"), active
  ├─ ownership_lean : "pro_government" | "anti_government" | "mixed" | "independent" | null
  ├─ confidence     : "high" | "medium" | "low" | null
  ├─ notes          : free text
  └─ canonical_article_count : int  (volume contributed)

Article (one published item)
  ├─ id, title, summary, image_url (nullable), url, published_at, source_name
  ├─ canonical?     : duplicates collapse to one canonical
  └─ also_reported_by : int  (# of OTHER outlets carrying the same story)

Story (cluster of same-event articles — THE core unit)
  ├─ id, representative_title (can be long)
  ├─ article_count  : int
  ├─ bias_distribution : { mixed:int, independent:int, pro_government:int, anti_government:int }
  ├─ bias_coverage_pct  : float 0–100  (% of members whose source is bias-tagged)
  ├─ is_political_topic : bool (computed by keyword scan)
  ├─ is_blindspot       : bool (lopsided pro/anti-government coverage on a political story)
  ├─ first_seen_at, last_updated_at : timestamps
  └─ members[]          : canonical articles (duplicates already collapsed)

PipelineHealth (data-quality / trust dashboard)
  ├─ total_articles, total_canonical_articles, total_stories
  ├─ per_source_article_counts[] : {source_name, total_articles, canonical_articles}
  ├─ rss_feed_status : {feeds_total, feeds_success, feeds_failed, feeds_gnews_fallback, feed_success_rate, extraction_success, extraction_success_rate}
  ├─ min_sample_gate : {threshold:3, stories_below:int}
  ├─ bias_coverage_buckets : {"100%":int,"50-99%":int,"1-49%":int,"0%":int}
  ├─ topic_gate : {stories_excluded_by_topic_gate:int}
  └─ blindspot : {flagged:int, eligible_political_and_tagged_ge_3:int}
```

**The 4 bias leans (product differentiator):** `pro_government`, `anti_government`, `mixed`, `independent`. Use a fixed color per lean everywhere (see §8).

---

## 4. API contract (exact — build the client from this)

Base URL (local): `http://localhost:8000`. All responses JSON. All methods **GET**.

### `GET /stories` — list (paginated, filterable)
Params: `offset` (0), `limit` (20, max 500), `is_blindspot` (bool), `min_articles` (int), `is_political_topic` (bool), `sort` (`last_updated_at` | `article_count`, default `last_updated_at` desc).

```json
{
  "total": 163,
  "offset": 0, "limit": 20,
  "stories": [
    {
      "id": "uuid",
      "representative_title": "After Oyo Schoolchildren, Teachers Regain Freedom…",
      "article_count": 27,
      "bias_distribution": {"mixed":19,"independent":6,"pro_government":2,"anti_government":0},
      "is_blindspot": false,
      "is_political_topic": true,
      "bias_coverage_pct": 100.0,
      "first_seen_at": "2026-07-12T08:34:12+00:00",
      "last_updated_at": "2026-07-12T19:55:23+00:00"
    }
  ]
}
```

### `GET /stories/{id}` — one story + members
Same story fields, plus:
```json
{
  "members": [
    {"title":"…","summary":"…","url":"https://…","image_url":"https://…|null",
     "source_name":"Punch","published_at":"…","also_reported_by":2}
  ]
}
```
Returns **404** (not 200) if the id doesn't exist.

### `GET /sources` — outlets + bias + volume
```json
{
  "total": 12,
  "sources": [
    {"id":"uuid","name":"Punch","homepage_url":"https://punchng.com","country":"NG",
     "active":true,"created_at":"…","ownership_lean":"mixed","confidence":"high",
     "notes":"…","canonical_article_count":42}
  ]
}
```
`ownership_lean`/`confidence`/`notes` are **null** when a source has no bias row — design a graceful "unknown" state.

### `GET /pipeline-health` — trust dashboard
```json
{
  "total_articles": 500, "total_canonical_articles": 498, "total_stories": 163,
  "per_source_article_counts": [
    {"source_id":"uuid","source_name":"Punch","total_articles":44,"canonical_articles":42}
  ],
  "rss_feed_status": {"feeds_total":10,"feeds_success":10,"feeds_failed":0,
    "feeds_gnews_fallback":4,"feed_success_rate":100.0,
    "extraction_success":203,"extraction_success_rate":67.89},
  "min_sample_gate": {"threshold":3,"stories_below":116},
  "bias_coverage_buckets": {"100%":163,"50-99%":0,"1-49%":0,"0%":0},
  "topic_gate": {"stories_excluded_by_topic_gate":88},
  "blindspot": {"flagged":0,"eligible_political_and_tagged_ge_3":30}
}
```

### `GET /` — liveness/info.

---

## 5. Personas

1. **The Civic Reader** (primary) — wants all outlets' takes on a big story; distrusts single-source narratives.
2. **The Journalist / Researcher** — needs coverage gaps + source lean fast; exports/screenshots.
3. **The Media-Literacy Educator** — uses the bias visualizations to teach.
4. **(Internal) Ops/Trust viewer** — watches `pipeline-health` for freshness/completeness.

---

## 6. User stories (use these to generate multiple screens)

**Epic A — Discover & read stories**
- As a reader, I want a ranked feed of current Stories so I can see what Nigeria is talking about.
- As a reader, I want to open a Story and compare every outlet's headline + a link-out.
- As a reader, I want "N outlets reported this" so I know how big/convergent the story is.
- As a reader, I want to filter by political topic / min article count / blindspots.

**Epic B — Understand bias & blindspots**
- As a reader, I want each Story's bias shown as a *visual*, not numbers.
- As a reader, I want a clear "⚠ Blindspot" flag with an explanation when coverage is lopsided.
- As a reader, I want a *coverage confidence* indicator so I don't over-trust thin samples.

**Epic C — Source transparency**
- As a reader, I want to browse Sources and see each outlet's ownership lean + confidence + volume.

**Epic D — Trust & freshness**
- As any user, I want a "data health" view so I know the numbers are current and complete.
- As a user, I want honest empty states ("no blindspots yet") rather than fake data.

**Epic E — Cross-platform parity**
- As a user, I expect the same core flows on Web, iOS, and Android, with platform-native feel.

---

## 7. Screen inventory (generate these)

**Shared (all platforms):**
1. **Stories feed** — Story cards, sort/filter bar.
2. **Story detail** — representative title, bias donut, blindspot badge, member list (outlet rows + "N outlets"), link-outs.
3. **Sources** — grid/list of outlet cards (lean chip, confidence, volume).
4. **Source detail** (stretch) — one outlet's lean profile + its stories.
5. **Blindspots** — filtered Stories where `is_blindspot=true` (often empty — design the empty state).
6. **Pipeline health / About-data** — trust dashboard.
7. **Article link-out** — opens source URL in browser (no in-app reader).

**Navigation:**
- **Mobile (iOS/Android):** bottom tab bar → `Stories · Blindspots · Sources · Health`. Filters = sheet/modal.
- **Web:** left sidebar + main content + sticky filter bar; room for donut + member grid side-by-side.

---

## 8. Visual direction (modern 2026 news site)

- **Aesthetic:** calm, credible, journalistic — newsroom not social feed. Generous whitespace, strong type hierarchy, content-first.
- **Lean color system (fix these, use everywhere):**
  - `pro_government` → **#2563EB** (blue)
  - `anti_government` → **#DC2626** (red)
  - `mixed` → **#7C3AED** (purple, blend)
  - `independent` → **#6B7280** (neutral gray)
  - "unknown lean" → **#D1D5DB** (light gray)
- **Themes:** light + dark (news is read at night). Define tokens for color/type/space/radius/elevation.
- **Typography:** a clean serif for headlines (e.g. *Source Serif*, *Lora*) + neutral sans for body/UI (e.g. *Inter*). High contrast (WCAG AA).
- **Components:** cards with subtle elevation, rounded 12–16px, 1px hairline borders; outlet "chips"; convergence badges; a signature **bias donut**.
- **Motion:** minimal — soft fade/slide on navigation, skeleton loaders, pull-to-refresh on mobile.
- **Ads:** reserve Google AdSense slots (see §9) — leaderboard under the top nav, in-feed native card every ~5 items, rectangle in Story detail sidebar. Keep them clearly labeled "Ad" and non-intrusive.
- **Monetization-ready but honest:** ads are the near-term revenue path; user accounts/interests are "Coming soon" (§10).

---

## 9. Recommended visualizations & interactions (the "beauty")

1. **Bias Donut (hero visual)** — 4-segment donut per Story. Color-coded leans, legend, tap/hover for counts. This is the product's signature. Make it gorgeous and instantly readable.
2. **Convergence badge — "N outlets reported this"** — from `also_reported_by`. e.g. *"Reported by Punch, Vanguard + 2 more."* Ground News-style differentiator.
3. **Blindspot badge** — when `is_blindspot=true`, a prominent, explainable warning: *"Heavy pro-government coverage, zero anti-government — on a political story."* Rare, high-value state; design it as first-class.
4. **Coverage confidence meter** — `bias_coverage_pct` + min-sample gate. "Based on 27 tagged articles" vs "only 2 — low confidence." Muted treatment for thin samples.
5. **Source transparency cards** — lean chip (colored) + confidence label + volume count.
6. **Pipeline health dashboard** — turn dry numbers (feeds 10/10, extraction 67.89%, coverage 100%) into a credibility story: stat cards, per-source bar chart, coverage bucket bars, blindspot-eligible count. Great for an "About / Methodology" section.
7. **Filter controls** — chips/toggles for *Political only*, *Min articles* (slider or stepper), *Blindspots only*; sort toggle *Recent / Most-covered*.
8. **Sliders & range inputs** — `min_articles` as a slider (e.g. 1–50); optionally a "coverage confidence" slider to filter stories by `bias_coverage_pct`.

---

## 10. "Coming soon" features (show as disabled/teasers, NOT functional)

Back-end does not support these yet. Represent them as tasteful "Coming soon" surfaces so the product looks complete without faking behavior:

- **Sign up / Sign in** — a nav item that opens a "Create account — Coming soon" modal/sheet.
- **My Interests / Topics** — a placeholder screen: "Pick the topics you care about. Coming soon." (No `categories` exist in data yet — present category tabs as "Coming soon" stubs, not real filters.)
- **Bookmarks / Saved stories** — disabled heart icon with tooltip "Coming soon."
- **Categories of news** (Politics, Business, Sports, Entertainment…) — we do **not** have category data today. Show category nav as muted "Coming soon" chips; do not wire them to filters.

---

## 11. Edge cases & empty/error states (design these — they're common)

| Case | Backend reality | UI must… |
|------|----------------|-----------|
| No blindspots | `blindspot.flagged = 0` | Calm "No blindspots detected yet" empty state. Don't fake/hide the tab. |
| Empty filter result | `is_blindspot=true` → `total: 0` | Friendly empty state + "clear filters" CTA. Never blank. |
| Unknown source lean | `ownership_lean: null` | Neutral "Lean: unknown" chip, not an error. |
| Low coverage / thin sample | `bias_coverage_pct < 100` or `article_count < 3` | Muted donut + "low sample" note. Not definitive. |
| Non-political story | `is_political_topic: false` | Show it; suppress blindspot/bias emphasis (sports/etc.). |
| Missing image | `image_url: null` | Branded placeholder, never broken image. |
| Missing summary | `summary: null` | Fall back to title only. |
| Bad story id | API returns **404** | Friendly not-found screen + "back to stories." |
| Stale data | Snapshot model | Surface `last_updated_at`. |
| Feed/extraction dip | rates may drop | Health screen shows honestly; don't mask. |
| Long titles | Some very long | Clamp/wrap gracefully. |
| `also_reported_by = 0` | Canonical w/ no dupes | "1 outlet" / no convergence badge — normal. |

---

## 12. Suggested screen-by-screen layout (for the generator)

**Stories feed (web):** Left sidebar (logo, nav: Stories/Blindspots/Sources/Health, "Sign in — Coming soon"). Top: greeting + sticky filter bar (Political toggle, Min-articles slider, Blindspots toggle, Sort). Main: responsive card grid; each card = outlet source chip, representative title (clamped 2 lines), mini bias bar, "N outlets" + "Updated 2h ago", one native ad card inserted every 5th position.

**Stories feed (mobile):** Bottom tab bar. Same cards as full-width list; filters open as a bottom sheet. Pull-to-refresh.

**Story detail (web):** Two-column. Left (wide): representative title, blindspot/confidence badges, member list (outlet rows: source chip, title, summary, "N outlets", link-out arrow). Right (sidebar): **bias donut** (pinned), convergence summary, rectangle ad. Dark/light toggle top-right.

**Story detail (mobile):** Bias donut pinned at top (full width), badges below, member list scrolls; link-out opens browser.

**Sources (web+mobile):** Grid of outlet cards — name, lean chip (colored), confidence label, volume count, homepage link. Sort by volume or A–Z.

**Blindspots:** Reuses Stories feed filtered to `is_blindspot=true`; prominent empty state when zero.

**Pipeline health:** Stat cards (Articles / Canonical / Stories), feed-status card (success rate, extraction rate), per-source bar chart, coverage-bucket bars, blindspot-eligible count, "Methodology" expandable linking to transparency ethos.

---

## 13. Handoff checklist for the AI generator

- [ ] Build from §4 API contract only (GET, no auth, link-out, no `full_text`).
- [ ] Design the **bias donut** first; lock the 4-lean color system (§8).
- [ ] Design all **empty/error states** in §11.
- [ ] Produce **parallel explorations** for Web, iOS, Android from the shared screen set (§7).
- [ ] Reserve **ad slots** (leaderboard, in-feed, rectangle) labeled "Ad".
- [ ] Add **"Coming soon"** treatments for Sign in / Interests / Categories / Bookmarks (§10) — disabled, not fake.
- [ ] Define **design tokens** (color/type/space/radius/elevation) + light/dark.
- [ ] Include a **methodology/about** surface tied to `pipeline-health`.
- [ ] Respect constraints: **no login, no in-app reader, link-out only, GET-only, honest emptiness.**

---

*This spec is the authoritative front-end definition for NaijaPulse until revised. Backend: Phases 1–6 complete and verified. Pair with `PRODUCT_DESIGN_BRIEF.md` for deeper product rationale.*
