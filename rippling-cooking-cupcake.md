# NaijaPulse Front Page — Live Data + New Home Components

## Context

`naijapulse-prototype.html` is a polished but **static** mock: hardcoded `STORIES`/`SOURCES` data, emoji-gradient thumbnails, fake `163 stories / 12 sources` counts, and 8 topic pills with no backing data. Meanwhile the pipeline already produces **real** data in Supabase, surfaced read-only via `phase6_api.py` (`/stories`, `/sources`, `/stories/{id}`, `/pipeline-health`).

Verification against live data confirmed a near-exact structural match:
- `bias_distribution` keys (`mixed`/`independent`/`pro_government`/`anti_government`) == the prototype's ribbon keys.
- `source_bias.ownership_lean` values == the four-lean taxonomy; `confidence` (high/med/low) matches.
- 255 stories, 33 sources (26 with bias), 1,121 canonical articles have real `image_url`, 0 blindspots.
- **Two gaps:** (1) no `topic` column — only a political/non-political flag; (2) `regional_base` exists (south_west 12 / national 11 / north 3) but isn't returned by `/sources` and has no coordinates.

Goal: turn the mock into a **live, data-driven front page** that wows — wiring the real API, adding a client-side topic classifier, a clickable story read view, a Bias Spectrum hero + Regional Coverage panel + Today's Balance index + 24h timeline, and a world-map fly-to-state animation — while keeping the prototype's strong, on-brand design language (near-black + green + Fraunces serif + the four-lean bias ribbon as the signature).

## Decisions (confirmed with user)
- Wire to the **live API** (with mock fallback if the server is down).
- **Build all** proposed components + a stories read view + the map.
- **Topic classifier** built client-side (no backend topic column).
- **Map:** world basemap (Leaflet CDN) that flies to the source's Nigerian **state** on click; state-level precise, LGA/cities shown as a curated list per state.
- **File layout:** split into `naijapulse-engine/ui/` and mount it statically in FastAPI.

## File structure (new)
```
naijapulse-engine/ui/
  index.html     # markup shell: sidebar, topbar, topic bar, views (stories/hero/regions/balance/timeline/detail/sources/health/map), ad slots
  styles.css     # migrated tokens + all component styles from the prototype (kept on-brand)
  app.js         # API client, field mapping, render (rail/feed/sources/health/hero/regions/balance/timeline/detail), nav, controls, mock fallback
  classifier.js  # topic keyword maps + scoring classifier; populates topic pills with live counts
  geo.js         # regional_base -> {centroid, states[]}; source id/name -> {city, state, lat, lng}; Nigeria states GeoJSON loader (CDN + embedded fallback)
  map.js         # Leaflet init, flyTo state, highlight polygon, source/region -> location panel
```

## Backend changes (`phase6_api.py`) — minimal, read-only
1. **CORS:** add `CORSMiddleware` allowing `http://localhost:*` + `file://` (null origin) for GET. (Read-only GET surface; RLS still needs enabling separately — flagged below, not auto-applied.)
2. **`/sources`:** include `regional_base` in the returned row (join already pulls `source_bias`; just add the field). Enables Regional Coverage panel + map without new tables.
3. **Static mount:** `app.mount("/", StaticFiles(directory="ui", html=True), name="ui")` so `http://localhost:8000/` serves the UI same-origin (FastAPI routes still win for `/stories` etc.).

## Frontend build (per file)

### app.js — API client + mapping + render
- `API_BASE = "http://localhost:8000"` (configurable). `fetchJSON()` with try/catch → on failure load embedded minimal mock so the page still renders.
- Mapping: `/stories` → `{id, title: cleanTitle(representative_title), count: article_count, dist: bias_distribution, blindspot, coverage: bias_coverage_pct, updated: relTime(last_updated_at), political, topic: classifyTopic(title), img:false}`. `cleanTitle` strips the ` - Source Name` suffix (36 titles). `relTime` formats `last_updated_at` → "2h ago".
- Rail + featured hero fetch `/stories/{id}` for the **first member `image_url`** (only ~7 rail + 1 hero — keeps it light); long feed uses the on-brand gradient fallback.
- `/sources` → `{id, name, url: homepage_url, lean: ownership_lean, confidence, vol: canonical_article_count, notes, regional_base}`.
- `/pipeline-health` → real stat cards (total_articles, total_canonical_articles, total_stories, blindspot.flagged) replacing hardcoded 500/498/163/0.
- Reuse existing helpers from the prototype: `ribbonHTML`, `donutSVG`, `confidenceChip`, `leanChip`, `thumbHTML`.
- **Story detail (read view):** on card click fetch `/stories/{id}`; render real `members` (title, summary, url, image_url, source_name, published_at, also_reported_by). Join member→lean via the loaded `/sources` list (members have `source_name`, not lean). **Side-filter tabs:** All / Pro-gov / Anti-gov / Mixed / Independent (this is the "slider" — filter articles by side). Keep donut + ribbon + coverage meter. Each article card: real image, title, summary, source, link-out (`target=_blank`), "N outlets also reported".

### classifier.js — topic classifier
- Keyword maps for: politics, security, economy, elections, naira, floods/climate, sports, entertainment. **Scoring** approach: count hits per topic across `representative_title + member titles`, assign the max; tie-break by a fixed priority; zero hits → "General".
- Populate the 8 topic pills with **live counts**; wire pill filtering (reuse existing `state.topic` logic). "All Topics" shows everything.

### Home components (new)
- **Bias Spectrum hero:** featured story (top by `article_count`) rendered large with an **oversized four-lean distribution** (stadium bar / big donut) as the opening signature; CTA opens its detail. Leads the page on the transparency thesis.
- **Regional Coverage panel:** aggregate `/sources` by `regional_base` (SW / national / north) → counts + mini bars; clicking a region triggers the map fly-to.
- **Today's Balance index:** derived 0–100 metric across political stories (≥3 tagged) measuring how balanced vs lopsided coverage is (from `bias_distribution`). One memorable, subject-specific number (not a vanity stat).
- **24h coverage timeline:** bucket stories by hour from `last_updated_at` over last 24h → inline-SVG sparkline (no lib).

### geo.js + map.js — world map, fly-to state
- **Leaflet** via CDN (unpkg) + world tiles (dark CartoDB to match theme).
- `geo.js`:
  - `REGION_META`: `south_west` → {label, centroid, states:[Lagos,Ogun,Oyo,Osun,Ondo,Ekiti]}; `national` → {label, centroid, states:[FCT + all]}; `north` → {label, centroid, states:[Kano,Kaduna,Katsina,Sokoto,Bauchi,…]}.
  - `SOURCE_GEO`: keyed by source id/name → {city, state, lat, lng} — **authored** for the 26 biased sources (Lagos/Abuja/Kano…). No backend column needed.
  - `loadStatesGeoJSON()`: fetch a Nigeria states GeoJSON from a public CDN, **fallback** to an embedded simplified GeoJSON of state centroids so it works offline.
- `map.js`: init world view; add states outline layer. On click of a **source card** or a **story's source**, resolve its geo (source→city/state, else region→centroid), `flyTo([lat,lng], zoom 7)`, highlight the state polygon, and open a side panel showing **state name + curated nearby LGAs/cities**. Honest about depth: state-level precise; LGA names are a curated list per state, not precise LGA polygons.
- **Graceful fallback:** if Leaflet/tile CDN fails to load, render a static region list panel instead of a blank map.

## Quality floor (carried from prototype)
Responsive down to mobile, visible keyboard focus, `prefers-reduced-motion` respected, light/dark toggle preserved.

## Out of scope (flagged, not built here)
- **RLS:** all 6 tables have RLS disabled (anon can read+write). Recommend enabling RLS with anon-`SELECT`-only policies as a separate step. I will **not** auto-apply the remediation SQL — presenting it for your decision.
- Persisting `topic` / source coordinates in the DB (kept client-side per your preference).
- Precise LGA polygon map (too large; curated lists instead).

## Verification
1. `cd naijapulse-engine && ./venv/bin/uvicorn phase6_api:app --port 8000`
2. Open `http://localhost:8000/` → UI loads and fetches **live** data (255 stories, 33 sources, real health counts).
3. Confirm: rail/feed show real stories; sources grid shows 33 with leans; topic pills show live counts and filter; clicking a story opens the detail with real members grouped by side (All/Pro/Anti/Mixed/Independent) and link-outs.
4. Click a source card → world map flies to its Nigerian state, highlights it, shows state + LGAs. Click a region in the Regional Coverage panel → same.
5. Bias Spectrum hero, Today's Balance index, and 24h timeline render from live data.
6. Toggle light/dark; verify reduced-motion; resize to mobile.
7. Kill the API → page falls back to embedded mock (no crash).
8. Spot-check `/pipeline-health` and `/stories/{id}` responses to confirm mapping.
9. Screenshot the home + detail + map via chrome-devtools to confirm the "wow" visually.
