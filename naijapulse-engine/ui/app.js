/* ===================================================================
   app.js — NaijaPulse front-end controller (3-column shell)
   -------------------------------------------------------------------
   Wires the static 3-column UI to the read-only API (phase6_api.py):
   /stories, /sources, /stories/{id}, /pipeline-health. Falls back to
   an embedded sample dataset if the API is unreachable.

   Surfaces:
     • Topbar nav (Stories / Sources / Pipeline Health)
     • Left rail  — Regional Coverage, Today's Balance, Snapshot,
                    Browse by topic, 24h timeline
     • Center     — Trending carousel, sort/filter bar, paginated feed,
                    story detail (hero + summary + 3-zone body), sources
                    grid + map, pipeline health
     • Right rail — Bias Spectrum + how-to-read panel
   =================================================================== */
(function () {
  "use strict";

  // The read-only API is always served from :8000 (see phase6_api.py).
  // The UI may be opened from :8001 (a separate static server), from :8000
  // itself, or via file:// — so we always call the API by absolute origin.
  // CORS is open (allow_origins=["*"]), so cross-origin calls from :8001 work.
  const API_BASE = "http://localhost:8000";

  const LEAN_META = {
    pro_government: { label: "Pro-government", cls: "pro", color: "var(--lean-pro)" },
    anti_government:{ label: "Anti-government", cls: "anti", color: "var(--lean-anti)" },
    mixed:          { label: "Mixed", cls: "mixed", color: "var(--lean-mixed)" },
    independent:    { label: "Independent", cls: "indep", color: "var(--lean-indep)" },
  };
  const ORDER = ["pro_government", "anti_government", "mixed", "independent"];
  const SHORT_LEAN = { pro_government: "pro-gov", anti_government: "anti-gov", mixed: "mixed", independent: "indep." };

  const PAGE_SIZE = 9;

  let STORIES = [], SOURCES = [], HEALTH = null, USING_MOCK = false;
  let carouselTimer = null;
  let currentView = "stories";
  let homeLeftHTML = "", homeRightHTML = "";
  const MAP = window.NaijaMap, GEO = window.NaijaGeo,
        TOPICS = window.NaijaTopics, STATICMAP = window.NaijaStaticMap;

  let state = {
    sort: "recent", minOutlets: 1, politicalOnly: false,
    hideLowConf: false, blindspotOnly: false, savedOnly: false,
    topic: "all", side: "all", query: "", page: 0,
  };

  /* ---------------- API + mapping ---------------- */
  async function fetchJSON(path) {
    const res = await fetch(API_BASE + path, { headers: { "Accept": "application/json" } });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const ct = res.headers.get("content-type") || "";
    if (!ct.includes("json")) throw new Error("non-JSON response");
    return res.json();
  }

  function normalizeDist(d) {
    d = d || {};
    const out = {};
    ORDER.forEach((k) => (out[k] = Math.max(0, parseInt(d[k] || 0, 10) || 0)));
    return out;
  }
  function tagged(d) { return ORDER.reduce((a, k) => a + d[k], 0); }

  const POLITICAL_WORDS = ["government","govt","president","minister","senate","governor",
    "cabinet","legislature","lawmaker","election","inec","policy","security","herdsmen",
    "insurgency","corruption","subsidy","party","naira","inflation","central bank","cbn",
    "tax","military","police","kidnap","abduction","court","tribunal","judiciary","budget",
    "apc","pdp","campaign","vote","protest","strike","fuel","diplomacy","coup","war"];
  function isPolitical(title) {
    if (!title) return false;
    const low = " " + title.toLowerCase() + " ";
    return POLITICAL_WORDS.some((w) => low.includes(w));
  }

  function cleanTitle(t) {
    if (!t) return t;
    return t.replace(/\s+[–-]\s+[A-Z][A-Za-z0-9 .'’&-]{2,48}$/, "").trim() || t;
  }

  function relTime(iso) {
    if (!iso) return "recent";
    const d = new Date(iso);
    if (isNaN(d)) return "recent";
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 0 || diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + "d ago";
    return d.toLocaleDateString();
  }

  function mapStory(raw) {
    const dist = normalizeDist(raw.bias_distribution);
    const title = cleanTitle(raw.representative_title);
    const political = (typeof raw.is_political_topic === "boolean")
      ? raw.is_political_topic : isPolitical(title);
    return {
      id: raw.id, title: title, rawTitle: raw.representative_title,
      count: raw.article_count || 0, dist: dist,
      blindspot: !!raw.is_blindspot,
      coverage: raw.bias_coverage_pct == null ? 0 : raw.bias_coverage_pct,
      updated: relTime(raw.last_updated_at), iso: raw.last_updated_at || null,
      firstSeen: raw.first_seen_at || null,
      political: political,
      topic: TOPICS.classify(raw.representative_title || ""),
      img: null,
    };
  }
  function mapSource(raw) {
    return {
      id: raw.id, name: raw.name, url: raw.homepage_url,
      lean: raw.ownership_lean || null, confidence: raw.confidence || null,
      vol: raw.canonical_article_count || 0, notes: raw.notes || null,
      regional_base: raw.regional_base || null,
    };
  }

  async function loadData() {
    try {
      const [st, src, h] = await Promise.all([
        fetchJSON("/stories?limit=500"),
        fetchJSON("/sources"),
        fetchJSON("/pipeline-health"),
      ]);
      STORIES = (st.stories || []).map(mapStory);
      SOURCES = (src.sources || []).map(mapSource);
      HEALTH = h;
    } catch (e) {
      USING_MOCK = true;
      STORIES = MOCK.stories.map(mapStory);
      SOURCES = MOCK.sources.map(mapSource);
      HEALTH = MOCK.health;
      const liveEl = document.querySelector(".live-line .mono");
      if (liveEl) liveEl.textContent = "sample";
      toast("Live API unreachable — showing sample data", true);
    }
  }

  /* ---------------- shared helpers ---------------- */
  function ribbonHTML(dist) {
    const total = tagged(dist) || 1;
    return `<div class="ribbon">${ORDER.map((k) => {
      const pct = (dist[k] / total) * 100;
      return pct > 0 ? `<span class="${LEAN_META[k].cls}" style="width:${pct}%"></span>` : "";
    }).join("")}</div>`;
  }
  function confidenceChip(pct) {
    const low = pct < 50;
    return `<span class="confidence-chip"><span class="bar"><i style="width:${pct}%;background:${low ? "var(--gold)" : "var(--accent-bright)"}"></i></span>${Math.round(pct)}% tagged${low ? " · low sample" : ""}</span>`;
  }
  function leanChip(lean) {
    if (!lean) return `<span class="lean-chip unk"><span class="dot" style="background:var(--lean-unk)"></span>Lean unknown</span>`;
    const m = LEAN_META[lean];
    return `<span class="lean-chip ${m.cls}"><span class="dot" style="background:${m.color}"></span>${m.label}</span>`;
  }
  function hue(str) {
    return Math.abs((str || "").split("").reduce((a, c) => a + c.charCodeAt(0), 0)) % 360;
  }
  function thumbHTML(story, icon) {
    if (story.img) {
      return `<div class="photo-gradient"><img src="${story.img}" alt="" loading="lazy"></div>`;
    }
    const h = hue(story.title);
    return `<div class="photo-gradient" data-icon="${icon || "📰"}" style="background:linear-gradient(140deg, hsl(${h} 40% 18%), hsl(${(h + 40) % 360} 35% 10%))"></div>`;
  }

  /* ---------------- SAVED / READING LIST (localStorage) ---------------- */
  const SAVED_KEY = "naijapulse:saved";
  function loadSaved() {
    try { return new Set(JSON.parse(localStorage.getItem(SAVED_KEY) || "[]")); }
    catch (e) { return new Set(); }
  }
  let savedIds = loadSaved();
  function isSaved(id) { return savedIds.has(id); }
  function toggleSaved(id) {
    if (savedIds.has(id)) savedIds.delete(id); else savedIds.add(id);
    try { localStorage.setItem(SAVED_KEY, JSON.stringify([...savedIds])); } catch (e) {}
  }
  const SAVE_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3h12a1 1 0 011 1v17l-7-4-7 4V4a1 1 0 011-1z"/></svg>`;
  function saveBtnHTML(s) {
    const on = isSaved(s.id);
    return `<button class="save-btn${on ? " saved" : ""}" data-save="${s.id}" title="${on ? "Saved — click to remove" : "Save for later"}" aria-label="Save story">${SAVE_SVG}</button>`;
  }
  function wireSaveButtons(scope) {
    (scope || document).querySelectorAll(".save-btn").forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = b.dataset.save;
        toggleSaved(id);
        b.classList.toggle("saved");
        if (state.savedOnly) renderFeed();
      });
    });
  }

  /* ---------------- view switching ---------------- */
  const VIEWS = ["stories", "sources", "health", "blindspot"];
  function showView(name) {
    currentView = name;
    if (name === "blindspot") {
      renderBlindspotPage();   // fills all three zones with blindspot context
    } else {
      restoreHomeRails();      // ensure rails show home content (not a story's)
    }
    VIEWS.forEach((v) => {
      const el = document.getElementById("view-" + v);
      if (el) el.hidden = v !== name;
    });
    document.getElementById("view-story-detail").hidden = true;
    document.getElementById("topicBar").style.display = name === "stories" ? "flex" : "none";
    document.querySelectorAll(".menu-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === name));
    if (name === "sources" && MAP && MAP.isReady && MAP.isReady()) {
      setTimeout(() => MAP.invalidate && MAP.invalidate(), 60);
    }
    if (name === "stories") renderCarousel(); else stopCarousel();
    closeDrawer();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* ---------------- hash routing ---------------- */
  // Each view + each story gets its own addressable URL (#/stories,
  // #/story/<id>, ...). Navigation goes through navigate(), which sets
  // location.hash; a single hashchange listener drives rendering, so the
  // browser Back/Forward buttons, refresh, and shared links all work.
  function parseHash() {
    const raw = (location.hash || "").replace(/^#\/?/, "");
    if (!raw) return { view: "stories", id: null };
    const [first, second] = raw.split("/");
    if (first === "story" && second) return { view: "story", id: decodeURIComponent(second) };
    if (["stories", "sources", "health", "blindspot"].includes(first)) return { view: first, id: null };
    return { view: "stories", id: null };
  }
  function navigate(hash) {
    const target = hash.replace(/^#/, "");            // strip leading # so the browser doesn't double-encode it
    if (location.hash === "#" + target) onRoute();   // same hash: re-run (e.g. re-click)
    else location.hash = target;                          // triggers hashchange -> onRoute
  }
  function onRoute() {
    const { view, id } = parseHash();
    if (view === "story" && id) renderStory(id);
    else showView(view);
  }
  // Thin wrapper so every "open this story" click routes through the URL.
  function openStory(id) { navigate("#/story/" + id); }

  /* ---------------- topic pills (center) ---------------- */
  function renderTopicPills() {
    const counts = TOPICS.countTopics(STORIES);
    const bar = document.getElementById("topicBar");
    const order = ["all", ...TOPICS.TOPIC_ORDER.filter((t) => t !== "general")];
    bar.innerHTML = order.map((t) => {
      const label = t === "all" ? "All Topics" : TOPICS.label(t);
      const n = t === "all" ? STORIES.length : (counts[t] || 0);
      return `<button class="pill ${t === state.topic ? "active" : ""}" data-topic="${t}">${label} <span class="n">${n}</span></button>`;
    }).join("");
  }
  function setTopic(t) {
    state.topic = t;
    state.page = 0;
    renderTopicPills();
    document.querySelectorAll(".topic-card").forEach((c) =>
      c.classList.toggle("active", c.dataset.topic === t));
    renderFeed();
  }

  /* ---------------- LEFT RAIL: Regional Coverage ---------------- */
  function renderRegions() {
    const el = document.getElementById("regionTrack");
    const buckets = { south_west: 0, national: 0, north: 0 };
    SOURCES.forEach((s) => {
      const k = s.regional_base || "national";
      if (k in buckets) buckets[k]++; else buckets.national++;
    });
    const max = Math.max(1, ...Object.values(buckets));
    el.innerHTML = `<div class="region-list">${Object.keys(buckets).map((k) => {
        const rm = GEO.REGION_META[k];
        const n = buckets[k];
        return `<div class="region-row" data-region="${k}">
          <span class="rname"><span class="rdot" style="background:${rm.color}"></span>${rm.label}</span>
          <span class="rtrack"><i style="width:${(n / max) * 100}%"></i></span>
          <span class="rval">${n}</span></div>`;
      }).join("")}</div>`;
    el.querySelectorAll(".region-row").forEach((row) => {
      row.addEventListener("click", () => {
        navigate("#/sources");
        setTimeout(() => {
          const g = MAP.focusRegion(row.dataset.region);
          updateLocPanel({ ...g, sources: SOURCES.filter((s) =>
            (s.regional_base || "national") === row.dataset.region) });
          const panel = document.getElementById("sourcesMapPanel");
          if (panel) panel.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 80);
      });
    });
  }

  /* ---------------- LEFT RAIL: Today's Balance ---------------- */
  function renderBalance() {
    const el = document.getElementById("balanceTrack");
    const eligible = STORIES.filter((s) => s.political && tagged(s.dist) >= 3);
    let balance = null;
    if (eligible.length) {
      const avg = eligible.reduce((a, s) => {
        const t = tagged(s.dist) || 1;
        return a + Math.abs(s.dist.pro_government - s.dist.anti_government) / t;
      }, 0) / eligible.length;
      balance = Math.round(100 * (1 - avg));
    }
    const ring = balance == null
      ? `<div class="balance-num">—</div>`
      : `<div class="balance-top"><div class="balance-num">${balance}<small>/100</small></div></div>
         <div class="gauge-track"><div class="gauge-fill" style="width:${balance}%"></div></div>`;
    const note = balance == null
      ? `<p class="balance-note">Not enough political stories with a confident sample yet.</p>`
      : `<p class="balance-note"><b>${balance}/100</b> balance across the pro/anti-government axis, averaged over
         <b>${eligible.length}</b> political stories (≥3 tagged). Higher means coverage draws from both sides; lower means it leans one way.</p>`;
    el.innerHTML = `<h4>Today's Balance <span class="mono" style="color:var(--text-faint);font-weight:400;">pro/anti axis</span></h4>${ring}${note}`;
  }

  /* ---------------- LEFT RAIL: Snapshot ---------------- */
  function renderSnapshot() {
    const el = document.getElementById("snapTrack");
    const blind = HEALTH && HEALTH.blindspot ? HEALTH.blindspot.flagged : 0;
    const articles = HEALTH ? HEALTH.total_articles : "—";
    const lastRun = HEALTH && HEALTH.last_run_at ? relTime(HEALTH.last_run_at) : "recent";
    el.innerHTML = `<h4>Snapshot <span class="mini-note">last pipeline run</span></h4>
      <div class="snap-grid">
        <div class="snap-cell"><div class="snap-num">${STORIES.length}</div><div class="snap-lbl">Stories</div></div>
        <div class="snap-cell"><div class="snap-num">${SOURCES.length}</div><div class="snap-lbl">Sources</div></div>
        <div class="snap-cell"><div class="snap-num">${blind}</div><div class="snap-lbl">Blindspots</div></div>
        <div class="snap-cell"><div class="snap-num">${articles}</div><div class="snap-lbl">Articles</div></div>
      </div>
      <p class="snap-foot">Read-only snapshot · updated ${lastRun}. Not a live stream.</p>`;
  }

  /* ---------------- LEFT RAIL: Browse by topic ---------------- */
  function renderTopicCards() {
    const el = document.getElementById("topicCards");
    const counts = TOPICS.countTopics(STORIES);
    const order = TOPICS.TOPIC_ORDER.filter((t) => t !== "general");
    el.innerHTML = order.map((t) => {
      const n = counts[t] || 0;
      const active = state.topic === t ? " active" : "";
      return `<button class="topic-card${active}" data-topic="${t}">
        <span class="tc-name">${TOPICS.label(t)}</span>
        <span class="tc-n mono">${n}</span></button>`;
    }).join("");
    el.querySelectorAll(".topic-card").forEach((c) =>
      c.addEventListener("click", () => setTopic(c.dataset.topic)));
  }

  /* ---------------- LEFT RAIL: 24h timeline ---------------- */
  function renderTimeline() {
    const el = document.getElementById("timelineTrack");
    const slots = new Array(24).fill(0);
    const now = Date.now();
    STORIES.forEach((s) => {
      const d = new Date(s.iso || null);
      if (isNaN(d)) return;
      const hr = Math.floor((now - d.getTime()) / 3600000);
      if (hr >= 0 && hr < 24) slots[23 - hr]++;
    });
    const max = Math.max(1, ...slots);
    let rects = "";
    slots.forEach((v, i) => {
      const w = 100 / 24;
      const h = (v / max) * 56;
      rects += `<rect x="${(i * w).toFixed(2)}" y="${(60 - h).toFixed(2)}" width="${(w - 0.6).toFixed(2)}" height="${h.toFixed(2)}" rx="1.5"></rect>`;
    });
    el.innerHTML = `<h4>Coverage · last 24h <span class="mono" style="color:var(--text-faint);font-weight:400;">stories updated</span></h4>
      <div class="timeline-wrap">
        <svg class="timeline-svg" viewBox="0 0 100 64" preserveAspectRatio="none">${rects}</svg>
        <div class="timeline-axis"><span>24h ago</span><span>12h</span><span>now</span></div>
      </div>`;
  }

  /* ---------------- RIGHT RAIL: Bias Spectrum ---------------- */
  function renderBiasSpectrum() {
    const el = document.getElementById("biasSpectrumTrack");
    const totals = { pro_government: 0, anti_government: 0, mixed: 0, independent: 0 };
    STORIES.forEach((s) => ORDER.forEach((k) => (totals[k] += s.dist[k])));
    const total = tagged(totals) || 1;

    let dom = null, dv = -1;
    ORDER.forEach((k) => { if (totals[k] > dv) { dv = totals[k]; dom = k; } });
    const domPct = Math.round((totals[dom] || 0) / total * 100);
    const narrative = dom
      ? `Today's coverage leans <b style="color:${LEAN_META[dom].color}">${LEAN_META[dom].label.toLowerCase()}</b> — ${domPct}% of all tagged articles — but all four lenses are present.`
      : `No confident lean tags recorded yet today.`;

    const legend = ORDER.map((k) => {
      const v = totals[k], pct = Math.round(v / total * 100);
      return `<div class="lr ${v === 0 ? "muted" : ""}"><span class="dot" style="background:${LEAN_META[k].color}"></span>
        <span class="lbl">${LEAN_META[k].label}</span>
        <span class="val">${v}</span><span class="pct">${pct}%</span></div>`;
    }).join("");

    el.innerHTML = `<h4>Bias Spectrum <span class="mini-note">all coverage today</span></h4>
      <div class="spectrum-donut">
        ${donutSVG(totals)}
        <div class="spectrum-total"><span class="big">${total}</span><span class="cap">articles<br>tagged</span></div>
      </div>
      <div class="spectrum-legend">${legend}</div>
      <p class="spectrum-note">${narrative}</p>`;
  }

  /* ---------------- TRENDING CAROUSEL ----------------
     targetId lets the same carousel render on the home feed (#carouselTrack)
     or on a story detail page (#storyCarouselTrack) so readers can jump to
     other stories without leaving the detail view. */
  function renderCarousel(targetId) {
    const track = document.getElementById(targetId || "carouselTrack");
    if (!track) return;
    const top = [...STORIES].sort((a, b) => b.count - a.count).slice(0, 6);
    if (!top.length) { track.innerHTML = ""; return; }

    let idx = 0;                       // rotating window start
    const ROTATE_MS = 5000;

    function frame() {
      const lead = top[idx % top.length];
      const side = [1, 2, 3].map((i) => top[(idx + i) % top.length]);
      const leadBias = ORDER.filter((k) => lead.dist[k] > 0).map((k) =>
        `<div class="tb-row"><span class="dot" style="background:${LEAN_META[k].color}"></span>${LEAN_META[k].label}<b>${lead.dist[k]}</b></div>`).join("");

      track.innerHTML = `
        <div class="trending">
          <article class="trend-lead" data-id="${lead.id}">
            <div class="trend-lead-photo">${thumbHTML(lead, "📰")}</div>
            <div class="trend-lead-body">
              <span class="eyebrow">${lead.political ? "Political · " : ""}Trending now</span>
              <h2>${lead.title}</h2>
              <div class="trend-bias">
                <div class="trend-donut">${donutSVG(lead.dist)}</div>
                <div class="trend-bias-legend">${leadBias}</div>
              </div>
              <div class="trend-meta">${lead.count} outlets reported this · ${lead.updated}</div>
            </div>
          </article>
          <div class="trend-side">
            ${side.map((s) => `
              <div class="trend-side-card" data-id="${s.id}">
                <div class="tsc-thumb">${thumbHTML(s, "📰")}</div>
                <div class="tsc-body">
                  <h4>${s.title}</h4>
                  <span class="badge-conv">${s.count} outlets</span>
                </div>
              </div>`).join("")}
          </div>
        </div>`;

      track.querySelectorAll("[data-id]").forEach((el) =>
        el.addEventListener("click", () => openStory(el.dataset.id)));

      // pull real images for the visible cards (lightweight)
      [lead, ...side].forEach((s) => fetchStoryImage(s.id).then((url) => {
        if (!url) return;
        s.img = url;
        const leadEl = track.querySelector(`.trend-lead[data-id="${s.id}"] .trend-lead-photo`);
        const sideEl = track.querySelector(`.trend-side-card[data-id="${s.id}"] .tsc-thumb`);
        const target = leadEl || sideEl;
        if (target) target.innerHTML = `<div class="photo-gradient"><img src="${url}" alt="" loading="lazy"></div>`;
      }));
    }

    if (carouselTimer) clearInterval(carouselTimer);
    frame();
    carouselTimer = setInterval(() => { idx = (idx + 1) % top.length; frame(); }, ROTATE_MS);
  }
  function stopCarousel() { if (carouselTimer) { clearInterval(carouselTimer); carouselTimer = null; } }

  async function fetchStoryImage(id) {
    try {
      const d = await fetchJSON("/stories/" + id);
      const m = (d.members || []).find((x) => x.image_url);
      return m ? m.image_url : null;
    } catch (e) { return null; }
  }
  async function fetchStoryExtras(id) {
    try {
      const d = await fetchJSON("/stories/" + id);
      const members = d.members || [];
      const m = members.find((x) => x.image_url);
      const summary = members.map((x) => x.summary || "").filter(Boolean)
        .sort((a, b) => b.length - a.length)[0] || null;
      return { img: m ? m.image_url : null, summary };
    } catch (e) { return { img: null, summary: null }; }
  }

  /* ---------------- FEED (paginated) ---------------- */
  function getFilteredStories() {
    let list = STORIES.filter((s) => s.count >= state.minOutlets);
    if (state.politicalOnly) list = list.filter((s) => s.political);
    if (state.blindspotOnly) list = list.filter((s) => s.blindspot);
    if (state.savedOnly) list = list.filter((s) => savedIds.has(s.id));
    if (state.hideLowConf) list = list.filter((s) => s.coverage >= 50);
    if (state.topic !== "all") list = list.filter((s) => s.topic === state.topic);
    if (state.query) {
      const q = state.query.toLowerCase();
      list = list.filter((s) => (s.title || "").toLowerCase().includes(q));
    }
    if (state.sort === "covered") list.sort((a, b) => b.count - a.count);
    return list;
  }

  function renderFeed() {
    const track = document.getElementById("feedTrack");
    const list = getFilteredStories();
    const total = list.length;
    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (state.page > pages - 1) state.page = pages - 1;
    const start = state.page * PAGE_SIZE;
    const pageItems = list.slice(start, start + PAGE_SIZE);

    if (!pageItems.length) {
      track.innerHTML = `<div class="empty-state" style="margin-top:20px;">
        <div class="empty-icon"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg></div>
        <h3>No stories match these filters</h3>
        <p>Try lowering the minimum outlet count, or turn off "Political only."</p>
        <button class="btn-ghost" id="clearFiltersBtn">Clear filters</button></div>`;
      document.getElementById("clearFiltersBtn").addEventListener("click", clearFilters);
      renderPager(total, pages);
      return;
    }

    let html = "";
    pageItems.forEach((s, i) => {
      const biasChips = ORDER.filter((k) => s.dist[k] > 0).map((k) =>
        `<span class="bb-chip ${LEAN_META[k].cls}"><span class="dot" style="background:${LEAN_META[k].color}"></span>${SHORT_LEAN[k]} ${s.dist[k]}</span>`).join("");
      html += `
      <article class="story-card" data-id="${s.id}" style="animation-delay:${Math.min(i, 8) * 0.05}s">
        <div class="thumb">${thumbHTML(s, "📰")}</div>
        ${saveBtnHTML(s)}
        <div class="content">
          <div class="badge-row">
            ${s.blindspot ? `<span class="badge badge-blindspot"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9L2.5 17a2 2 0 001.7 3h15.6a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/></svg>Blindspot</span>` : ""}
            ${s.political ? '<span class="badge badge-political">Political</span>' : `<span class="badge-conv">${TOPICS.label(s.topic)}</span>`}
            <span class="badge-conv">${s.updated}</span>
          </div>
          <h3>${s.title}</h3>
          <div class="bias-breakdown">${biasChips}</div>
          <p class="story-sum" data-id="${s.id}"></p>
          <div class="meta-row"><span class="mono">${s.count} outlets reported this</span>${confidenceChip(s.coverage)}</div>
        </div>
      </article>`;
      if ((start + i + 1) % 5 === 0 && (start + i + 1) < total) {
        html += `<div class="feed-ad"><span class="tag">Advertisement</span><span>Google AdSense · in-feed native</span></div>`;
      }
    });
    track.innerHTML = html;
    track.querySelectorAll(".story-card").forEach((el) =>
      el.addEventListener("click", () => openStory(el.dataset.id)));
    wireSaveButtons(track);
    // pull real photos + a summary line for the visible cards (lightweight)
    pageItems.forEach((s) => {
      const card = track.querySelector(`.story-card[data-id="${s.id}"]`);
      if (!card) return;
      fetchStoryExtras(s.id).then(({ img, summary }) => {
        if (!card.isConnected) return;
        const thumb = card.querySelector(".thumb");
        if (img) thumb.innerHTML = `<div class="photo-gradient"><img src="${img}" alt="" loading="lazy"></div>`;
        const sum = card.querySelector(".story-sum");
        if (sum && summary) sum.textContent = summary;
      });
    });
    renderPager(total, pages);
  }

  function renderPager(total, pages) {
    const el = document.getElementById("feedPager");
    if (total === 0) { el.innerHTML = ""; return; }
    const cur = state.page + 1;
    const from = state.page * PAGE_SIZE + 1;
    const to = Math.min(total, state.page * PAGE_SIZE + PAGE_SIZE);
    let nums = "";
    const win = 2;
    for (let p = 1; p <= pages; p++) {
      if (p === 1 || p === pages || (p >= cur - win && p <= cur + win)) {
        nums += `<button class="pager-page ${p === cur ? "active" : ""}" data-page="${p - 1}">${p}</button>`;
      } else if (p === cur - win - 1 || p === cur + win + 1) {
        nums += `<span class="pager-ellipsis">…</span>`;
      }
    }
    el.innerHTML = `
      <span class="pager-info mono">${from}–${to} of ${total}</span>
      <div class="pager-btns">
        <button class="pager-btn" data-page="${state.page - 1}" ${state.page === 0 ? "disabled" : ""}>Prev</button>
        ${nums}
        <button class="pager-btn" data-page="${state.page + 1}" ${state.page >= pages - 1 ? "disabled" : ""}>Next</button>
      </div>`;
    el.querySelectorAll("[data-page]").forEach((b) => {
      b.addEventListener("click", () => {
        const p = +b.dataset.page;
        if (p < 0 || p > pages - 1) return;
        state.page = p;
        renderFeed();
        document.getElementById("center").scrollTo({ top: 0, behavior: "smooth" });
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    });
  }

  function clearFilters() {
    state = { ...state, minOutlets: 1, politicalOnly: false, blindspotOnly: false, savedOnly: false, hideLowConf: false, topic: "all", query: "", page: 0 };
    const mi = document.getElementById("minOutlets");
    if (mi) { mi.value = 1; document.getElementById("minOutletsVal").textContent = 1; }
    const po = document.getElementById("politicalOnly"); if (po) po.checked = false;
    const bo = document.getElementById("blindspotOnly"); if (bo) bo.checked = false;
    const so = document.getElementById("savedOnly"); if (so) so.checked = false;
    const hc = document.getElementById("hideLowConf"); if (hc) hc.checked = false;
    const si = document.getElementById("searchInput"); if (si) si.value = "";
    renderTopicPills();
    document.querySelectorAll(".topic-card").forEach((c) => c.classList.remove("active"));
    renderFeed();
  }

  /* ---------------- STORY DETAIL ---------------- */
  function donutSVG(dist) {
    const total = tagged(dist) || 1;
    const R = 54, C = 2 * Math.PI * R;
    let acc = 0;
    const circles = ORDER.map((k) => {
      const pct = dist[k] / total;
      const dash = pct * C;
      const el = pct > 0 ? `<circle cx="70" cy="70" r="${R}" fill="none" stroke="${LEAN_META[k].color}" stroke-width="16" stroke-dasharray="${dash} ${C - dash}" stroke-dashoffset="${-acc}" stroke-linecap="butt"/>` : "";
      acc += dash;
      return el;
    }).join("");
    return `<svg width="140" height="140" viewBox="0 0 140 140" style="transform:rotate(-90deg)">
      <circle cx="70" cy="70" r="${R}" fill="none" stroke="var(--glass-border-soft)" stroke-width="16"/>${circles}</svg>`;
  }

  async function renderStory(id) {
    let data;
    try {
      data = await fetchJSON("/stories/" + id);
    } catch (e) {
      document.getElementById("feedTrack").innerHTML = `<div class="empty-state" style="margin-top:20px;">
        <div class="empty-icon"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 9v4M12 17h.01M10.3 3.9L2.5 17a2 2 0 001.7 3h15.6a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/></svg></div>
        <h3>That story could not be found</h3>
        <p>The link may be stale or the story was removed from the snapshot.</p>
        <button class="btn-ghost" id="backFromErr">Back to Stories</button></div>`;
      document.getElementById("backFromErr").addEventListener("click", backToFeed);
      return;
    }

    const s = mapStory({
      id: data.id, representative_title: data.representative_title,
      article_count: data.article_count, bias_distribution: data.bias_distribution,
      is_blindspot: data.is_blindspot, bias_coverage_pct: data.bias_coverage_pct,
      last_updated_at: data.last_updated_at, is_political_topic: data.is_political_topic,
    });

    const leanByName = {};
    SOURCES.forEach((x) => (leanByName[x.name] = x.lean));
    const members = (data.members || []).map((m, i) => ({
      ...m,
      lean: leanByName[m.source_name] || null,
      side: sideOfLean(leanByName[m.source_name] || null),
      idx: i,
    }));

    // related stories (B) + missing-side analysis (A)
    const related = STORIES.filter((x) => x.id !== s.id)
      .map((x) => ({ x, score: (x.topic === s.topic ? 3 : 0) + Math.min(x.count, 25) / 25 }))
      .sort((a, b) => b.score - a.score).slice(0, 5).map((o) => o.x);
    const presentSides = new Set(members.map((m) => m.side));
    const missing = ORDER.filter((k) => !presentSides.has(k));

    // hero photo
    const photo = members.find((m) => m.image_url);
    document.getElementById("storyPhoto").innerHTML = photo
      ? `<img src="${photo.image_url}" alt="" loading="lazy">`
      : `<div class="photo-gradient" data-icon="📰"></div>`;
    document.getElementById("storyHeadline").textContent = s.title;

    // summary / lede
    const bestSummary = members.map((m) => m.summary || "").filter(Boolean)
      .sort((a, b) => b.length - a.length)[0] || null;
    const blindBanner = s.blindspot ? `<div class="blindspot-banner">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9L2.5 17a2 2 0 001.7 3h15.6a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/></svg>
        <div><h4>Blindspot detected</h4><p>Coverage is heavily lopsided on this political story — almost entirely from one side of the pro/anti-government axis.</p></div></div>` : "";
    document.getElementById("storySummary").innerHTML = `
      <div class="badge-row" style="margin-bottom:8px;">
        ${s.political ? '<span class="badge badge-political">Political story</span>' : '<span class="badge-conv">Non-political — bias emphasis suppressed</span>'}
        <span class="badge-conv">Last updated ${s.updated}</span>
        ${saveBtnHTML(s)}
      </div>
      ${blindBanner}
      ${bestSummary ? `<p class="story-lede">${bestSummary}</p>` : ""}
      <p class="story-lede-sub">Reported by <b>${s.count}</b> outlets across the lean spectrum. Read each side below — every article links out to its source.</p>`;

    // LEFT RAIL (universal): bias donut + coverage meter + where covered
    const total = tagged(s.dist);
    const legend = ORDER.map((k) => `
      <div class="legend-row"><span class="dot" style="background:${LEAN_META[k].color}"></span>
        <span class="lbl">${LEAN_META[k].label}</span><span class="val">${s.dist[k]}</span></div>`).join("");
    const states = new Set();
    members.forEach((m) => { const g = GEO.SOURCE_GEO[m.source_name]; if (g && g.state) states.add(g.state); });
    document.querySelector("#leftRail .rail-inner").innerHTML = `
      <div class="rail-card-panel">
        <h4>Bias distribution <span class="mono" style="color:var(--text-faint);font-weight:400;">this story</span></h4>
        <div class="donut-wrap">${donutSVG(s.dist)}<div class="donut-legend">${legend}</div></div>
        <div class="coverage-meter">
          <div class="note">Coverage confidence — based on ${total} of ${s.count} articles</div>
          <div class="track"><div class="fill" style="width:${s.coverage}%"></div></div>
          <div class="note">${s.coverage < 50 ? "Thin sample — treat this reading as directional, not definitive." : Math.round(s.coverage) + "% of members have a known source lean."}</div>
        </div>
      </div>
      <div class="rail-card-panel">
        <h4>Where it's covered <span class="mini-note">source states</span></h4>
        <div class="story-map" id="storyMap"></div>
      </div>`;
    if (STATICMAP && states.size) STATICMAP.render(document.getElementById("storyMap"), [...states]);

    // RIGHT RAIL (universal): convergence + similar topics + key facts
    const topSources = [...members].sort((a, b) => b.also_reported_by - a.also_reported_by).slice(0, 4);
    const convChips = topSources.length
      ? topSources.map((m) => `<span class="conv-chip">${m.source_name || "Unknown"}<b>+${m.also_reported_by}</b></span>`).join("")
      : `<span class="loc-empty">Single-outlet story — no convergence yet.</span>`;
    document.querySelector("#rightRail .rail-inner").innerHTML = `
      <div class="rail-card-panel">
        <h4>Convergence</h4>
        <div class="conv-row">${convChips}</div>
        <p class="conv-note">${s.count} outlet${s.count === 1 ? "" : "s"} carried this story. Higher "also reported by" means broad agreement.</p>
      </div>
      <div class="rail-card-panel">
        <h4>Similar topics</h4>
        <div class="similar-tags">
          <a>${TOPICS.label(s.topic)}</a><a>Nigeria</a><a>Governance</a><a>${s.political ? "Political" : "Human interest"}</a>
        </div>
      </div>
      <div class="rail-card-panel">
        <h4>Read the other side</h4>
        ${missing.length ? `
        <p class="conv-note">Covered by ${presentSides.size} of 4 leans. Missing perspectives:</p>
        <div class="miss-list">
          ${missing.map((k) => {
            const rel = related.find((r) => (r.dist[k] || 0) > 0);
            return `<div class="miss-row">
              <span class="lean-chip ${LEAN_META[k].cls}">${LEAN_META[k].label}</span>
              ${rel ? `<button class="bs-mini-row" data-id="${rel.id}"><span class="dot" style="background:${LEAN_META[k].color}"></span><span class="bs-mini-title">${rel.title}</span></button>`
                    : `<span class="loc-empty">No related story carried this side.</span>`}
            </div>`;
          }).join("")}
        </div>` : `<p class="conv-note">Covered across all four leans — a balanced story.</p>`}
      </div>
      <div class="rail-card-panel key-facts">
        <h4>Key facts</h4>
        <div class="kf-row"><span>First seen</span><b>${relTime(data.first_seen_at)}</b></div>
        <div class="kf-row"><span>Last updated</span><b>${s.updated}</b></div>
        <div class="kf-row"><span>Tagged sample</span><b>${total}/${s.count}</b></div>
        <div class="kf-row"><span>Blindspot</span><b>${s.blindspot ? "Yes" : "No"}</b></div>
      </div>`;

    // CENTER (prominent): side tabs + member list
    state.side = "all";
    const sides = ["all", "pro_government", "anti_government", "mixed", "independent"];
    const sideCounts = {};
    sides.forEach((sd) => (sideCounts[sd] = sd === "all" ? members.length
      : members.filter((m) => m.side === sd).length));
    const center = document.getElementById("storyArticles");
    center.innerHTML = `
      <div class="panel">
        <h4>Reported by ${s.count} outlets — read each side</h4>
        <div class="side-tabs" id="sideTabs">
          ${sides.map((sd) => `<button class="side-tab ${sd === state.side ? "active" : ""}" data-side="${sd}">
            ${sd === "all" ? "" : `<span class="sdot" style="background:${LEAN_META[sd].color}"></span>`}
            ${sd === "all" ? "All sides" : LEAN_META[sd].label}<span class="cnt">${sideCounts[sd]}</span></button>`).join("")}
        </div>
        <div id="articleList"></div>
      </div>`;

    const renderArticles = () => {
      const list = state.side === "all" ? members : members.filter((m) => m.side === state.side);
      const al = document.getElementById("articleList");
      if (!list.length) { al.innerHTML = `<div class="loc-empty">No articles from this side.</div>`; return; }
      al.innerHTML = list.map((m) => {
        const also = m.also_reported_by > 0 ? `<span>Also reported by ${m.also_reported_by} more</span>` : "";
        const img = m.image_url
          ? `<div class="a-thumb"><img src="${m.image_url}" alt="" loading="lazy"></div>`
          : `<div class="a-thumb"><div class="a-fallback">no image</div></div>`;
        return `<div class="article-item">
          ${img}
          <div class="a-body">
            <h5>${m.title || "(untitled)"}</h5>
            <p class="a-sum">${m.summary || ""}</p>
            <div class="a-meta">
              ${leanChip(m.lean)}
              <span>${m.source_name || "Unknown source"}</span>
              ${m.published_at ? `<span>${relTime(m.published_at)}</span>` : ""}
              ${also}
              <a class="a-link" href="${m.url}" target="_blank" rel="noopener" title="Read at ${m.source_name}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 17L17 7M7 7h10v10"/></svg></a>
            </div>
          </div>
        </div>`;
      }).join("");
    };
    renderArticles();
    document.getElementById("sideTabs").addEventListener("click", (e) => {
      const btn = e.target.closest(".side-tab"); if (!btn) return;
      state.side = btn.dataset.side;
      document.querySelectorAll("#sideTabs .side-tab").forEach((b) =>
        b.classList.toggle("active", b === btn));
      renderArticles();
    });

    // B — Related / Meanwhile strip (keep the clickstream going)
    const relEl = document.getElementById("relatedStrip");
    if (related.length) {
      relEl.innerHTML = `<div class="related-head"><h4>Related stories <span class="mini-note">keep reading</span></h4></div>
        <div class="related-grid">` +
        related.map((r) => `
          <article class="related-card" data-id="${r.id}">
            <div class="thumb">${thumbHTML(r, "📰")}</div>
            <div class="content">
              <div class="badge-row">${r.blindspot ? '<span class="badge badge-blindspot">Blindspot</span>' : ""}${r.political ? '<span class="badge badge-political">Political</span>' : ""}<span class="badge-conv">${r.updated}</span></div>
              <h5>${r.title}</h5>
              <div class="meta-row"><span class="mono">${r.count} outlets</span></div>
            </div>
          </article>`).join("") + `</div>`;
      relEl.querySelectorAll(".related-card").forEach((c) =>
        c.addEventListener("click", () => openStory(c.dataset.id)));
      related.forEach((r) => {
        const card = relEl.querySelector(`.related-card[data-id="${r.id}"] .thumb`);
        fetchStoryExtras(r.id).then(({ img }) => {
          if (img && card && card.isConnected) card.innerHTML = `<div class="photo-gradient"><img src="${img}" alt="" loading="lazy"></div>`;
        });
      });
    } else { relEl.innerHTML = ""; }

    // show the detail view
    renderCarousel("storyCarouselTrack");
    VIEWS.forEach((v) => { document.getElementById("view-" + v).hidden = true; });
    document.getElementById("view-story-detail").hidden = false;
    document.getElementById("topicBar").style.display = "none";
    document.querySelectorAll(".menu-btn").forEach((b) => b.classList.remove("active"));
    wireSaveButtons(document.getElementById("view-story-detail"));
    document.querySelectorAll("#rightRail .bs-mini-row").forEach((b) =>
      b.addEventListener("click", () => openStory(b.dataset.id)));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* ---------------- DATA CARDS (homepage rails) ---------------- */
  function renderSourceConcentration() {
    const el = document.getElementById("sourceConcCard");
    if (!el) return;
    const sorted = [...SOURCES].sort((a, b) => b.vol - a.vol);
    const total = sorted.reduce((a, s) => a + s.vol, 0) || 1;
    const top = sorted.slice(0, 5);
    const topShare = Math.round(top.reduce((a, s) => a + s.vol, 0) / total * 100);
    const max = Math.max(1, ...top.map((s) => s.vol));
    const rows = top.map((s) => `
      <div class="conc-row">
        <span class="conc-name">${s.name}</span>
        <span class="conc-track"><i style="width:${s.vol / max * 100}%"></i></span>
        <span class="conc-val mono">${s.vol}</span>
      </div>`).join("");
    el.innerHTML = `
      <h4>Source concentration <span class="mini-note">top 5 share</span></h4>
      <div class="conc-share">Top 5 outlets = <b>${topShare}%</b> of ${total} articles</div>
      <div class="conc-list">${rows}</div>
      <p class="spectrum-note">High concentration means a few outlets shape most of the feed.</p>`;
  }

  function renderConfidenceDist() {
    const el = document.getElementById("confCard");
    if (!el) return;
    const buckets = [["100%", 100, 100], ["50–99%", 50, 99], ["1–49%", 1, 49], ["0%", 0, 0]];
    const counts = buckets.map(([label, lo, hi]) => {
      const n = STORIES.filter((s) => {
        const c = s.coverage;
        return hi === 0 ? c === 0 : (c >= lo && c <= hi);
      }).length;
      return { label, n };
    });
    const max = Math.max(1, ...counts.map((c) => c.n));
    const rows = counts.map((c) => `
      <div class="conf-row"><span class="conf-lbl">${c.label}</span>
        <span class="conf-track"><i style="width:${c.n / max * 100}%"></i></span>
        <span class="conf-val mono">${c.n}</span></div>`).join("");
    el.innerHTML = `
      <h4>Coverage confidence <span class="mini-note">tagged sample</span></h4>
      <div class="conf-list">${rows}</div>
      <p class="spectrum-note">Share of stories with a confident source-lean sample. Low samples are directional, not definitive.</p>`;
  }

  function renderTopicLeanHeatmap() {
    const el = document.getElementById("heatmapCard");
    if (!el || !TOPICS) return;
    const topics = (TOPICS.TOPIC_ORDER || []).filter((t) => t !== "general");
    const agg = {};
    topics.forEach((t) => (agg[t] = { pro_government: 0, anti_government: 0, mixed: 0, independent: 0 }));
    STORIES.forEach((s) => { const t = s.topic; if (agg[t]) ORDER.forEach((k) => (agg[t][k] += s.dist[k])); });
    const maxCell = Math.max(1, ...topics.flatMap((t) => ORDER.map((k) => agg[t][k])));
    const cells = topics.map((t) => {
      const row = ORDER.map((k) => {
        const v = agg[t][k];
        const inten = v / maxCell;
        return `<div class="hm-cell" title="${TOPICS.label(t)} · ${LEAN_META[k].label}: ${v}" style="background:${LEAN_META[k].color};opacity:${(0.1 + inten * 0.9).toFixed(2)}">${v}</div>`;
      }).join("");
      return `<div class="hm-row"><span class="hm-label">${TOPICS.label(t)}</span><div class="hm-cells">${row}</div></div>`;
    }).join("");
    const head = `<div class="hm-row hm-head"><span class="hm-label"></span><div class="hm-cells">${ORDER.map((k) => `<div class="hm-cell hm-h" style="color:${LEAN_META[k].color}">${LEAN_META[k].label.split(" ")[0]}</div>`).join("")}</div></div>`;
    el.innerHTML = `
      <h4>Topic × lean <span class="mini-note">where each lens reports</span></h4>
      <div class="hm">${head}${cells}</div>
      <p class="spectrum-note">Darker cell = more articles from that lean on that topic.</p>`;
  }

  function renderOneSided() {
    const el = document.getElementById("oneSidedCard");
    if (!el) return;
    const buckets = [["≥90% one-sided", 0.9, 2], ["75–89%", 0.75, 0.89], ["60–74%", 0.6, 0.74], ["<60% balanced", 0, 0.59]];
    const counts = buckets.map(([label, lo, hi]) => {
      const n = STORIES.filter((s) => {
        const k = skew(s);
        return hi >= 2 ? k >= lo : (k >= lo && k <= hi);
      }).length;
      return { label, n };
    });
    const max = Math.max(1, ...counts.map((c) => c.n));
    const rows = counts.map((c) => `
      <div class="conf-row"><span class="conf-lbl">${c.label}</span>
        <span class="conf-track"><i style="width:${c.n / max * 100}%;background:${c.label.startsWith("≥90") ? "var(--lean-anti)" : "var(--accent-bright)"}"></i></span>
        <span class="conf-val mono">${c.n}</span></div>`).join("");
    const oneSided = STORIES.filter((s) => skew(s) >= 0.9).length;
    el.innerHTML = `
      <h4>Coverage balance <span class="mini-note">how one-sided</span></h4>
      <div class="conc-share"><b>${oneSided}</b> of ${STORIES.length} stories are ≥90% one‑sided</div>
      <div class="conf-list">${rows}</div>
      <p class="spectrum-note">One‑sidedness isn't wrong — but it shows where the press speaks with one voice.</p>`;
  }

  function renderDataCards() {
    renderSourceConcentration();
    renderConfidenceDist();
    renderTopicLeanHeatmap();
    renderOneSided();
  }

  /* ---------------- MEASURE CARDS (home rails) ---------------- */
  // 1) Bias over time — how the pro/anti/mixed/independent mix shifts by day.
  function renderBiasTime() {
    const el = document.getElementById("biasTimeCard");
    if (!el) return;
    const buckets = {};
    STORIES.forEach((s) => {
      if (!s.firstSeen) return;
      const dt = new Date(s.firstSeen);
      if (isNaN(dt)) return;
      const key = dt.toISOString().slice(0, 10);
      if (!buckets[key]) buckets[key] = { pro_government: 0, anti_government: 0, mixed: 0, independent: 0, n: 0 };
      ORDER.forEach((k) => (buckets[key][k] += s.dist[k] || 0));
      buckets[key].n++;
    });
    const days = Object.keys(buckets).sort().slice(-7);
    if (!days.length) { el.innerHTML = `<h4>Bias over time <span class="mini-note">by day</span></h4><p class="spectrum-note">No dated stories to bucket yet.</p>`; return; }
    const rows = days.map((k) => {
      const b = buckets[k], total = ORDER.reduce((a, kk) => a + b[kk], 0) || 1;
      const segs = ORDER.map((kk) => b[kk]
        ? `<i style="width:${(b[kk] / total) * 100}%;background:${LEAN_META[kk].color}"></i>` : "").join("");
      const label = new Date(k + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric" });
      return `<div class="bt-row"><span class="bt-label">${label}</span><span class="bt-track">${segs}</span><span class="bt-n mono">${b.n}</span></div>`;
    }).join("");
    el.innerHTML = `<h4>Bias over time <span class="mini-note">tagged by day</span></h4>
      <div class="bt-list">${rows}</div>
      <p class="spectrum-note">How the lean mix shifts as stories break. Each bar shows that day's share of tagged articles.</p>`;
  }

  // 2) Source leaderboard — outlets contributing the most canonical articles.
  function renderLeaderboard() {
    const el = document.getElementById("leaderboardCard");
    if (!el) return;
    const top = [...SOURCES].sort((a, b) => b.vol - a.vol).slice(0, 8);
    if (!top.length) { el.innerHTML = ""; return; }
    const max = Math.max(1, ...top.map((s) => s.vol));
    const dot = (lean) => (lean && LEAN_META[lean]) ? LEAN_META[lean].color : "var(--text-faint)";
    const rows = top.map((s) => `
      <div class="conc-row">
        <span class="conc-name"><span class="dot" style="width:7px;height:7px;border-radius:50%;flex:0 0 auto;background:${dot(s.lean)}"></span>${s.name}</span>
        <span class="conc-track"><i style="width:${s.vol / max * 100}%"></i></span>
        <span class="conc-val mono">${s.vol}</span>
      </div>`).join("");
    el.innerHTML = `<h4>Source leaderboard <span class="mini-note">by volume</span></h4>
      <div class="conc-list">${rows}</div>
      <p class="spectrum-note">The outlets shaping the most of the feed right now.</p>`;
  }

  // 3) Topic momentum — topics with the most stories still fresh in the last 24h.
  function renderMomentum() {
    const el = document.getElementById("momentumCard");
    if (!el) return;
    const now = Date.now(), WIN = 24 * 3600 * 1000;
    const agg = {};
    STORIES.forEach((s) => {
      const t = s.topic || "general";
      if (!agg[t]) agg[t] = { total: 0, recent: 0 };
      agg[t].total++;
      if (s.iso) { const dt = new Date(s.iso).getTime(); if (!isNaN(dt) && now - dt <= WIN) agg[t].recent++; }
    });
    const topics = Object.keys(agg).filter((t) => t !== "general")
      .sort((a, b) => agg[b].recent - agg[a].recent).slice(0, 6);
    if (!topics.length) { el.innerHTML = `<h4>Topic momentum <span class="mini-note">last 24h</span></h4><p class="spectrum-note">No recent stories to rank yet.</p>`; return; }
    const maxR = Math.max(1, ...topics.map((t) => agg[t].recent));
    const rows = topics.map((t) => {
      const a = agg[t], pct = a.total ? Math.round((a.recent / a.total) * 100) : 0;
      const arrow = pct >= 60 ? "▲" : (pct >= 30 ? "◆" : "▼");
      return `<div class="mom-row">
        <span class="mom-name">${TOPICS.label(t)}</span>
        <span class="mom-track"><i style="width:${a.recent / maxR * 100}%"></i></span>
        <span class="mom-val mono">${a.recent}<span class="mom-arrow">${arrow}</span></span>
      </div>`;
    }).join("");
    el.innerHTML = `<h4>Topic momentum <span class="mini-note">last 24h</span></h4>
      <div class="mom-list">${rows}</div>
      <p class="spectrum-note">Topics with the most stories still fresh in the last day — what's heating up.</p>`;
  }

  // 4) Echo chambers — events covered only by one side of the pro/anti axis.
  function renderEcho() {
    const el = document.getElementById("echoCard");
    if (!el) return;
    const flagged = STORIES.filter((s) => {
      const p = s.dist.pro_government || 0, a = s.dist.anti_government || 0;
      return (p > 0 && a === 0) || (a > 0 && p === 0);
    }).sort((x, y) => y.count - x.count).slice(0, 5);
    if (!flagged.length) { el.innerHTML = `<h4>Echo chambers <span class="mini-note">one-sided axis</span></h4><p class="spectrum-note">No events covered by only one side of the pro/anti axis right now.</p>`; return; }
    const rows = flagged.map((s) => {
      const side = s.dist.pro_government > 0 ? "pro" : "anti";
      const cls = side === "pro" ? "pro" : "anti";
      return `<button class="bs-mini-row" data-id="${s.id}">
        <span class="bb-chip ${cls}">${side === "pro" ? "pro-gov" : "anti-gov"}</span>
        <span class="bs-mini-title">${s.title}</span>
      </button>`;
    }).join("");
    el.innerHTML = `<h4>Echo chambers <span class="mini-note">one-sided axis</span></h4>
      <p class="conv-note" style="margin-bottom:10px;">Events covered only by one side of the pro/anti axis — the other side is silent.</p>
      <div class="bs-mini-list">${rows}</div>`;
    el.querySelectorAll(".bs-mini-row").forEach((b) =>
      b.addEventListener("click", () => openStory(b.dataset.id)));
  }

  // Homepage measure cards — the ones that belong on the Stories dashboard
  // (temporal bias shape + what's heating up). The source leaderboard and
  // echo-chamber cards moved to the Sources / Blindspots views (see
  // renderSources / renderBlindspotPage), since that's where they're useful.
  function renderMeasureCards() {
    renderBiasTime();
    renderMomentum();
  }

  function sideOfLean(lean) {
    if (!lean) return "independent";
    if (lean === "pro_government") return "pro_government";
    if (lean === "anti_government") return "anti_government";
    if (lean === "mixed") return "mixed";
    return "independent";
  }

  function restoreHomeRails() {
    const l = document.querySelector("#leftRail .rail-inner");
    const r = document.querySelector("#rightRail .rail-inner");
    if (homeLeftHTML) l.innerHTML = homeLeftHTML;
    if (homeRightHTML) r.innerHTML = homeRightHTML;
    renderRegions(); renderBalance(); renderSnapshot(); renderTopicCards(); renderTimeline();
    renderBiasSpectrum();
    renderBlindspotCard();   // re-attach handlers (innerHTML above drops them)
    renderDataCards();
    renderMeasureCards();
  }

  /* ---------------- BLINDSPOT HELPERS ---------------- */
  function dominantLean(s) {
    const d = s.dist || {};
    let best = "independent", bestN = 0;
    ORDER.forEach((k) => { if ((d[k] || 0) > bestN) { best = k; bestN = d[k]; } });
    return best;
  }
  function skew(s) {
    const t = tagged(s.dist);
    if (!t) return 0;
    return Math.max(...ORDER.map((k) => s.dist[k] || 0)) / t;
  }
  function blindspotStories() { return STORIES.filter((s) => s.blindspot); }

  // Homepage left-rail card — rich granular blindspot view
  function renderBlindspotCard() {
    const el = document.getElementById("blindspotCard");
    if (!el) return;
    const bs = blindspotStories();
    const political = STORIES.filter((s) => s.political);
    const pct = political.length ? Math.round((bs.length / political.length) * 100) : 0;
    const dom = { pro_government: 0, anti_government: 0, mixed: 0, independent: 0 };
    bs.forEach((s) => { dom[dominantLean(s)]++; });
    const chipCounts = ORDER.filter((k) => dom[k] > 0).map((k) =>
      `<span class="lean-chip ${LEAN_META[k].cls}">${LEAN_META[k].label} <b>${dom[k]}</b></span>`).join("");
    const list = bs.slice().sort((a, b) => skew(b) - skew(a)).slice(0, 4).map((s) =>
      `<button class="bs-mini-row" data-id="${s.id}">
         <span class="dot" style="background:${LEAN_META[dominantLean(s)].color}"></span>
         <span class="bs-mini-title">${s.title}</span>
       </button>`).join("");
    el.innerHTML = `
      <h4>Blindspots today <span class="mini-note">one-sided coverage</span></h4>
      <div class="bs-overview">
        <div class="bs-overview-num">${bs.length}</div>
        <div class="bs-overview-sub">${pct}% of ${political.length} political stories</div>
      </div>
      <div class="bs-mini-chips">${chipCounts || '<span class="bs-empty">No blindspots flagged yet</span>'}</div>
      ${list ? `<div class="bs-mini-list">${list}</div>` : ""}`;
    el.querySelectorAll(".bs-mini-row").forEach((b) =>
      b.addEventListener("click", () => openStory(b.dataset.id)));
  }

  // Blindspot PAGE — explainer + both rails + feed (3-column context)
  function renderBlindspotPage() {
    renderBlindspotExplainer();
    renderBlindspotRails();
    renderBlindspotFeed();
    renderEcho();   // echo-chamber detector lives on the Blindspots view
  }

  function renderBlindspotExplainer() {
    const el = document.getElementById("blindspotExplainer");
    if (!el) return;
    const bs = blindspotStories();
    const example = bs.slice().sort((a, b) => skew(b) - skew(a))[0];
    const exDist = example ? example.dist
      : { pro_government: 14, anti_government: 0, mixed: 1, independent: 0 };
    el.innerHTML = `
      <h4>What is a blindspot? <span class="mini-note">the detection rule</span></h4>
      <div class="bs-explain">
        <div class="bs-explain-donut">${donutSVG(exDist)}</div>
        <div class="bs-explain-text">
          <p>A blindspot is a <b>political</b> story whose coverage is almost entirely from a single lean. We require at least <b>3 tagged</b> canonical articles so the reading is statistically meaningful, then flag the story when one side dominates the pro/anti-government axis.</p>
          <p class="bs-explain-ex">${example ? "Most lopsided now: <b>" + example.title + "</b>" : "No blindspots in this snapshot."}</p>
        </div>
      </div>`;
  }

  function renderBlindspotRails() {
    const left = document.querySelector("#leftRail .rail-inner");
    const right = document.querySelector("#rightRail .rail-inner");
    if (!left || !right) return;
    const bs = blindspotStories();
    const political = STORIES.filter((s) => s.political);
    const pct = political.length ? Math.round((bs.length / political.length) * 100) : 0;
    const agg = { pro_government: 0, anti_government: 0, mixed: 0, independent: 0 };
    bs.forEach((s) => { agg[dominantLean(s)]++; });
    const donutDist = { pro_government: agg.pro_government, anti_government: agg.anti_government,
      mixed: agg.mixed, independent: agg.independent };
    const leanLegend = ORDER.map((k) => `
      <div class="lr ${agg[k] === 0 ? "muted" : ""}"><span class="dot" style="background:${LEAN_META[k].color}"></span>
        <span class="lbl">${LEAN_META[k].label}</span><span class="val">${agg[k]}</span></div>`).join("");

    left.innerHTML = `
      <div class="rail-card-panel">
        <h4>Blindspot overview <span class="mini-note">this snapshot</span></h4>
        <div class="bs-overview">
          <div class="bs-overview-num">${bs.length}</div>
          <div class="bs-overview-sub">${pct}% of ${political.length} political stories</div>
        </div>
        <div class="spectrum-donut">
          ${donutSVG(donutDist)}
          <div class="spectrum-total"><span class="big">${bs.length}</span><span class="cap">blind-<br>spots</span></div>
        </div>
        <div class="spectrum-legend" style="margin-top:14px;">${leanLegend}</div>
        <p class="spectrum-note">Each blindspot is owned by one lean. This shows which side dominates the stories we flag as one‑sided.</p>
      </div>`;

    const top = bs.slice().sort((a, b) => skew(b) - skew(a)).slice(0, 5);
    const list = top.map((s) => {
      const rows = ORDER.filter((k) => s.dist[k] > 0).map((k) => {
        const p = Math.round(s.dist[k] / Math.max(1, tagged(s.dist)) * 100);
        return `<div class="bs-row"><span>${LEAN_META[k].label}</span>
          <span class="track"><i style="width:${p}%;background:${LEAN_META[k].color}"></i></span>
          <span>${s.dist[k]}</span></div>`;
      }).join("");
      return `<div class="bs-card" data-id="${s.id}">
        <span class="badge badge-blindspot">Lopsided</span>
        <h4>${s.title}</h4>
        <div class="bs-breakdown">${rows}</div>
      </div>`;
    }).join("");
    right.innerHTML = `
      <div class="rail-card-panel methodology-panel">
        <h4>How we detect a blindspot</h4>
        <p>A story is flagged when it is <b>political</b>, has at least <b>3 tagged</b> canonical articles, and its coverage leans overwhelmingly to one side of the pro/anti-government axis. The aim is not to judge a story wrong, but to surface where the public may be missing a perspective.</p>
      </div>
      <div class="rail-card-panel">
        <h4>Most lopsided now <span class="mini-note">by skew</span></h4>
        ${list || '<div class="loc-empty">No blindspots flagged in this snapshot.</div>'}
      </div>`;
    right.querySelectorAll(".bs-card").forEach((c) =>
      c.addEventListener("click", () => openStory(c.dataset.id)));
  }

  function blindspotCardHTML(s, i) {
    const biasChips = ORDER.filter((k) => s.dist[k] > 0).map((k) =>
      `<span class="bb-chip ${LEAN_META[k].cls}"><span class="dot" style="background:${LEAN_META[k].color}"></span>${SHORT_LEAN[k]} ${s.dist[k]}</span>`).join("");
    const dom = dominantLean(s);
    return `
    <article class="story-card" data-id="${s.id}" style="animation-delay:${Math.min(i, 8) * 0.05}s">
      <div class="thumb">${thumbHTML(s, "📰")}</div>
      ${saveBtnHTML(s)}
      <div class="content">
        <div class="badge-row">
          <span class="badge badge-blindspot">Blindspot · ${LEAN_META[dom].label} owned</span>
          <span class="badge-conv">${s.updated}</span>
        </div>
        <h3>${s.title}</h3>
        <div class="bias-breakdown">${biasChips}</div>
        <p class="story-sum" data-id="${s.id}"></p>
        <div class="meta-row"><span class="mono">${s.count} outlets reported this</span>${confidenceChip(s.coverage)}</div>
      </div>
    </article>`;
  }

  function renderBlindspotFeed() {
    const track = document.getElementById("blindspotFeed");
    if (!track) return;
    const bs = blindspotStories().sort((a, b) => skew(b) - skew(a));
    if (!bs.length) {
      track.innerHTML = `<div class="empty-state" style="margin-top:20px;">
        <div class="empty-icon"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg></div>
        <h3>No blindspots in this snapshot</h3>
        <p>When a political story is covered overwhelmingly from one side, it will appear here.</p></div>`;
      const pager = document.getElementById("blindspotPager");
      if (pager) pager.innerHTML = "";
      return;
    }
    track.innerHTML = bs.map((s, i) => blindspotCardHTML(s, i)).join("");
    track.querySelectorAll(".story-card").forEach((el) =>
      el.addEventListener("click", () => openStory(el.dataset.id)));
    wireSaveButtons(track);
    bs.forEach((s) => {
      const card = track.querySelector(`.story-card[data-id="${s.id}"]`);
      if (!card) return;
      fetchStoryExtras(s.id).then(({ img, summary }) => {
        if (!card.isConnected) return;
        const thumb = card.querySelector(".thumb");
        if (img) thumb.innerHTML = `<div class="photo-gradient"><img src="${img}" alt="" loading="lazy"></div>`;
        const sum = card.querySelector(".story-sum");
        if (sum && summary) sum.textContent = summary;
      });
    });
  }

  function backToFeed() {
    navigate("#/" + (currentView === "blindspot" ? "blindspot" : "stories"));
  }

  /* ---------------- SOURCES ---------------- */
  function renderSources(sortMode) {
    let list = [...SOURCES];
    list.sort((a, b) => sortMode === "alpha" ? a.name.localeCompare(b.name) : b.vol - a.vol);
    document.getElementById("sourcesGrid").innerHTML = list.map((src, i) => {
      const h = hue(src.name);
      return `
      <div class="source-card" data-id="${src.id}" data-name="${src.name}" style="animation-delay:${i * 0.04}s">
        <div class="top">
          <div class="source-avatar" style="background:hsl(${h} 30% 16%); color:hsl(${h} 50% 70%)">${src.name.slice(0, 1)}</div>
          <div><h4>${src.name}</h4><div class="url">${src.url || ""}</div></div>
        </div>
        ${leanChip(src.lean)}
        ${src.notes ? `<div class="source-note">"${src.notes}"</div>` : (src.lean ? "" : `<div class="source-note">No ownership signal on file yet — shown honestly rather than guessed.</div>`)}
        <div class="source-stats">
          <span class="vol mono">${src.vol} articles</span>
          <span class="confidence-label">${src.confidence ? src.confidence + " confidence" : "unrated"}</span>
        </div>
      </div>`;
    }).join("");
    document.querySelectorAll(".source-card").forEach((el) =>
      el.addEventListener("click", () => selectSource(SOURCES.find((s) => s.id === el.dataset.id))));
    renderLeaderboard();   // source leaderboard lives on the Sources view
  }

  function selectSource(src) {
    if (!src) return;
    const g = MAP.focusSource(src);
    updateLocPanel({ ...g, sources: SOURCES.filter((s) => s.name === src.name) });
    const panel = document.getElementById("sourcesMapPanel");
    if (panel) panel.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  /* ---------------- PIPELINE HEALTH ---------------- */
  function renderHealth() {
    if (!HEALTH) return;
    const h = HEALTH;
    document.getElementById("healthStats").innerHTML = `
      <div class="stat-card"><div class="big mono">${h.total_articles}</div><div class="lbl">Total articles ingested</div></div>
      <div class="stat-card"><div class="big mono">${h.total_canonical_articles}</div><div class="lbl">Canonical (deduplicated)</div></div>
      <div class="stat-card"><div class="big mono">${h.total_stories}</div><div class="lbl">Stories clustered</div></div>
      <div class="stat-card"><div class="big mono" style="color:var(--lean-anti)">${h.blindspot.flagged}</div><div class="lbl">Blindspots flagged today</div></div>`;
  }

  /* ---------------- MAP + location panel ---------------- */
  function initMap() {
    const ok = MAP.init("leafletMap");
    if (!ok) {
      document.getElementById("leafletMap").style.display = "none";
      document.getElementById("mapFallback").style.display = "flex";
      document.getElementById("mapFallback").innerHTML = `
        <div class="loc-empty">The interactive map needs an internet connection (Leaflet CDN).
        Here are the tracked regions instead:</div>
        <div class="region-list">${Object.keys(GEO.REGION_META).map((k) => {
          const n = SOURCES.filter((s) => (s.regional_base || "national") === k).length;
          return `<div class="region-row"><span class="rname"><span class="rdot" style="background:${GEO.REGION_META[k].color}"></span>${GEO.REGION_META[k].label}</span>
          <span class="rtrack"><i style="width:${(n / Math.max(1, SOURCES.length)) * 100}%"></i></span><span class="rval">${n}</span></div>`;
        }).join("")}</div>`;
    }
  }
  function updateLocPanel(g) {
    const el = document.getElementById("locPanel");
    if (!g) { el.innerHTML = `<div class="loc-empty">Click a source or a region to locate it on the map.</div>`; return; }
    if (g.state) {
      const m = GEO.STATE_META[g.state];
      const region = GEO.REGION_META[g.region] || GEO.REGION_META.national;
      const lgas = (g.lgas && g.lgas.length) ? g.lgas.map((l) => `<span>${l}</span>`).join("")
                                              : `<span class="loc-empty">No LGA data</span>`;
      const srcs = (g.sources && g.sources.length)
        ? g.sources.map((s) => `<span class="source-chip"><span class="dot" style="background:${s.lean ? LEAN_META[s.lean].color : "var(--text-faint)"}"></span>${s.name}</span>`).join("")
        : "";
      el.innerHTML = `
        <div class="loc-head"><div>
          <div class="loc-region">${region.label}</div>
          <div class="loc-state">${g.state} State</div>
        </div></div>
        <p class="loc-sub">${g.city ? g.city + " · " : ""}Coverage from this state contributes to the NaijaPulse feed.</p>
        <h5>Major LGAs / cities</h5>
        <div class="lga-list">${lgas}</div>
        ${srcs ? `<h5 style="margin-top:16px;">Tracked outlets here</h5><div style="margin-top:8px;">${srcs}</div>` : ""}`;
    } else {
      const region = GEO.REGION_META[g.region] || GEO.REGION_META.national;
      const srcs = (g.sources && g.sources.length)
        ? g.sources.map((s) => `<span class="source-chip"><span class="dot" style="background:${s.lean ? LEAN_META[s.lean].color : "var(--text-faint)"}"></span>${s.name}</span>`).join("")
        : "";
      el.innerHTML = `
        <div class="loc-head"><div>
          <div class="loc-region">region</div>
          <div class="loc-state">${region.label}</div>
        </div></div>
        <p class="loc-sub">A regional view — several states contribute from this part of the country.</p>
        ${srcs ? `<h5 style="margin-top:8px;">Tracked outlets</h5><div style="margin-top:8px;">${srcs}</div>` : ""}`;
    }
  }

  /* ---------------- controls ---------------- */
  function wireControls() {
    document.getElementById("sortToggle").addEventListener("click", (e) => {
      const btn = e.target.closest("button"); if (!btn) return;
      document.querySelectorAll("#sortToggle button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active"); state.sort = btn.dataset.sort; state.page = 0; renderFeed();
    });
    const mi = document.getElementById("minOutlets");
    if (mi) mi.addEventListener("input", (e) => {
      state.minOutlets = +e.target.value;
      document.getElementById("minOutletsVal").textContent = e.target.value;
      state.page = 0; renderFeed();
    });
    const po = document.getElementById("politicalOnly");
    if (po) po.addEventListener("change", (e) => { state.politicalOnly = e.target.checked; state.page = 0; renderFeed(); });
    const bo = document.getElementById("blindspotOnly");
    if (bo) bo.addEventListener("change", (e) => { state.blindspotOnly = e.target.checked; state.page = 0; renderFeed(); });
    const so = document.getElementById("savedOnly");
    if (so) so.addEventListener("change", (e) => { state.savedOnly = e.target.checked; state.page = 0; renderFeed(); });
    const hc = document.getElementById("hideLowConf");
    if (hc) hc.addEventListener("change", (e) => { state.hideLowConf = e.target.checked; state.page = 0; renderFeed(); });
    document.getElementById("topicBar").addEventListener("click", (e) => {
      const btn = e.target.closest(".pill"); if (!btn) return;
      setTopic(btn.dataset.topic);
    });
    document.getElementById("sourceSortToggle").addEventListener("click", (e) => {
      const btn = e.target.closest("button"); if (!btn) return;
      document.querySelectorAll("#sourceSortToggle button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active"); renderSources(btn.dataset.ssort);
    });
    document.querySelectorAll(".menu-btn").forEach((n) =>
      n.addEventListener("click", () => navigate("#/" + n.dataset.view)));
    document.getElementById("themeToggle").addEventListener("click", () => {
      const html = document.documentElement;
      html.setAttribute("data-theme", html.getAttribute("data-theme") === "dark" ? "light" : "dark");
    });
    const si = document.getElementById("searchInput");
    if (si) si.addEventListener("input", (e) => {
      state.query = e.target.value.trim(); state.page = 0; renderFeed();
    });
    document.getElementById("brandHome").addEventListener("click", () => navigate("#/stories"));
    document.getElementById("backToFeed").addEventListener("click", backToFeed);
    document.getElementById("hamburger").addEventListener("click", () => {
      document.getElementById("leftRail").classList.toggle("mobile-open");
      document.getElementById("railBackdrop").classList.toggle("show");
    });
    document.getElementById("railBackdrop").addEventListener("click", closeDrawer);
  }
  function closeDrawer() {
    document.getElementById("leftRail").classList.remove("mobile-open");
    document.getElementById("railBackdrop").classList.remove("show");
  }

  /* ---------------- misc ---------------- */
  let toastTimer;
  function toast(msg, warn) {
    let t = document.getElementById("toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
    t.textContent = msg;
    t.className = "toast show" + (warn ? " warn" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.className = "toast"), 3200);
  }
  function updateNavCounts() {
    document.querySelectorAll(".menu-btn").forEach((n) => {
      if (n.dataset.view === "stories") { const c = n.querySelector(".count"); if (c) c.textContent = STORIES.length; }
      if (n.dataset.view === "sources") { const c = n.querySelector(".count"); if (c) c.textContent = SOURCES.length; }
      if (n.dataset.view === "blindspot") { const c = n.querySelector(".count"); if (c) c.textContent = blindspotStories().length; }
    });
  }
  function renderFooter() {
    const el = document.getElementById("siteFooter");
    if (!el) return;
    el.innerHTML = `
      <div class="foot-left">
        <span class="brand-name">Naija<b>Pulse</b></span>
        <span class="foot-tag">See every side of every Nigerian story.</span>
      </div>
      <div class="foot-right">
        <span>Read-only snapshot · link-outs only · no in-app reader</span>
        <span>${USING_MOCK ? "Sample data" : "Live API"} · ${new Date().getFullYear()}</span>
      </div>`;
  }

  /* ---------------- embedded fallback (sample) ---------------- */
  const MOCK = {
    stories: [
      {id:"s1",representative_title:"After Oyo Schoolchildren, Teachers Regain Freedom, ADC Demands Rescue of Borno, Kwara Kidnap Victims",article_count:27,bias_distribution:{mixed:19,independent:6,pro_government:2,anti_government:0},is_blindspot:false,bias_coverage_pct:100,last_updated_at:new Date(Date.now()-2*3600e3).toISOString()},
      {id:"s2",representative_title:"CBN Holds Benchmark Rate at 27.5% as Naira Steadies Against Dollar",article_count:34,bias_distribution:{mixed:14,independent:9,pro_government:8,anti_government:3},is_blindspot:false,bias_coverage_pct:100,last_updated_at:new Date(Date.now()-40*60e3).toISOString()},
      {id:"s3",representative_title:"FG Commissions Second Niger Bridge Rehabilitation, Opposition Questions Contract Cost",article_count:21,bias_distribution:{mixed:5,independent:2,pro_government:13,anti_government:1},is_blindspot:false,bias_coverage_pct:95.2,last_updated_at:new Date(Date.now()-3*3600e3).toISOString()},
      {id:"s4",representative_title:"Lagos Flood Displaces 3,000 Residents in Ikorodu as Rains Intensify",article_count:16,bias_distribution:{mixed:9,independent:5,pro_government:1,anti_government:1},is_blindspot:false,bias_coverage_pct:100,last_updated_at:new Date(Date.now()-5*3600e3).toISOString()},
      {id:"s5",representative_title:"Super Eagles Name Provisional Squad for 2027 AFCON Qualifiers",article_count:12,bias_distribution:{mixed:7,independent:3,pro_government:1,anti_government:1},is_blindspot:false,bias_coverage_pct:100,last_updated_at:new Date(Date.now()-1*3600e3).toISOString()},
      {id:"s6",representative_title:"INEC Unveils New Voter Register Ahead of 2027 Elections, Civil Society Flags Gaps",article_count:9,bias_distribution:{mixed:2,independent:1,pro_government:6,anti_government:0},is_blindspot:false,bias_coverage_pct:100,last_updated_at:new Date(Date.now()-6*3600e3).toISOString()},
      {id:"s7",representative_title:"Senate Passes Tax Reform Bill Amid Walkout by Minority Caucus",article_count:24,bias_distribution:{mixed:8,independent:2,pro_government:13,anti_government:1},is_blindspot:false,bias_coverage_pct:100,last_updated_at:new Date(Date.now()-2*3600e3).toISOString()},
      {id:"s8",representative_title:"Fuel Scarcity Bites in Port Harcourt as Marketers Blame Depot Logistics",article_count:18,bias_distribution:{mixed:11,independent:4,pro_government:2,anti_government:1},is_blindspot:false,bias_coverage_pct:100,last_updated_at:new Date(Date.now()-8*3600e3).toISOString()},
      {id:"s9",representative_title:"Governor Commissions Potato Value Chain Project, Eyes Agro-Industrial Transformation",article_count:3,bias_distribution:{mixed:0,independent:0,pro_government:3,anti_government:0},is_blindspot:false,bias_coverage_pct:33.3,last_updated_at:new Date(Date.now()-11*3600e3).toISOString()},
      {id:"s10",representative_title:"Afrobeats Star Announces Continental Tour, Ticket Sales Crash Vendor Site",article_count:14,bias_distribution:{mixed:9,independent:4,pro_government:0,anti_government:1},is_blindspot:false,bias_coverage_pct:100,last_updated_at:new Date(Date.now()-20*60e3).toISOString()},
      {id:"s11",representative_title:"EFCC Arraigns Former Commissioner Over Alleged N2.3bn Contract Fraud",article_count:8,bias_distribution:{mixed:1,independent:0,pro_government:1,anti_government:6},is_blindspot:false,bias_coverage_pct:100,last_updated_at:new Date(Date.now()-4*3600e3).toISOString()},
      {id:"s12",representative_title:"Customs Reports Record N1.7tn Revenue in H1, Cites Digitised Clearance",article_count:11,bias_distribution:{mixed:2,independent:1,pro_government:8,anti_government:0},is_blindspot:false,bias_coverage_pct:100,last_updated_at:new Date(Date.now()-9*3600e3).toISOString()},
      {id:"s13",representative_title:"FG Launches Renewed Hope Infrastructure Drive, Commissions 12 Rural Roads",article_count:19,bias_distribution:{mixed:2,independent:1,pro_government:15,anti_government:0},is_blindspot:true,is_political_topic:true,bias_coverage_pct:94.7,last_updated_at:new Date(Date.now()-3*3600e3).toISOString()},
      {id:"s14",representative_title:"Groups Allege Suppression of Dissent as Protest Leaders Remain in Detention",article_count:13,bias_distribution:{mixed:1,independent:1,pro_government:0,anti_government:11},is_blindspot:true,is_political_topic:true,bias_coverage_pct:92.3,last_updated_at:new Date(Date.now()-5*3600e3).toISOString()},
    ],
    sources: [
      {id:"punch",name:"Punch",homepage_url:"punchng.com",ownership_lean:"mixed",confidence:"high",canonical_article_count:44,notes:"Broad ownership base; coverage swings by desk.",regional_base:"south_west"},
      {id:"vanguard",name:"Vanguard",homepage_url:"vanguardngr.com",ownership_lean:"mixed",confidence:"high",canonical_article_count:38,notes:null,regional_base:"south_west"},
      {id:"thecable",name:"TheCable",homepage_url:"thecable.ng",ownership_lean:"independent",confidence:"high",canonical_article_count:31,notes:"Digital-native, no political ownership ties on record.",regional_base:"national"},
      {id:"premiumtimes",name:"Premium Times",homepage_url:"premiumtimesng.com",ownership_lean:"independent",confidence:"high",canonical_article_count:29,notes:"Investigative desk; funded partly by grants.",regional_base:"national"},
      {id:"dailytrust",name:"Daily Trust",homepage_url:"dailytrust.com",ownership_lean:"mixed",confidence:"medium",canonical_article_count:26,notes:null,regional_base:"north"},
      {id:"thisday",name:"ThisDay",homepage_url:"thisdaylive.com",ownership_lean:"pro_government",confidence:"medium",canonical_article_count:24,notes:"Ownership has documented ties to political office holders.",regional_base:"south_west"},
      {id:"channels",name:"Channels TV",homepage_url:"channelstv.com",ownership_lean:"mixed",confidence:"high",canonical_article_count:22,notes:null,regional_base:"south_west"},
      {id:"leadership",name:"Leadership",homepage_url:"leadership.ng",ownership_lean:"pro_government",confidence:"low",canonical_article_count:19,notes:"Lean inferred from editorial pattern, not confirmed ownership.",regional_base:"north"},
      {id:"nation",name:"The Nation",homepage_url:"thenationonlineng.net",ownership_lean:"pro_government",confidence:"medium",canonical_article_count:18,notes:null,regional_base:"south_west"},
      {id:"sahara",name:"Sahara Reporters",homepage_url:"saharareporters.com",ownership_lean:"anti_government",confidence:"high",canonical_article_count:16,notes:"Diaspora-run; consistently oppositional editorial stance.",regional_base:"south_west"},
      {id:"businessday",name:"BusinessDay",homepage_url:"businessday.ng",ownership_lean:"independent",confidence:"medium",canonical_article_count:14,notes:null,regional_base:"south_west"},
      {id:"tribune",name:"Nigerian Tribune",homepage_url:"tribuneonlineng.com",ownership_lean:null,confidence:null,canonical_article_count:9,notes:null,regional_base:"south_west"},
    ],
    health: { total_articles: 500, total_canonical_articles: 498, total_stories: 163,
      blindspot: { flagged: 2 }, per_source_article_counts: [],
      min_sample_gate: { threshold: 3, stories_below: 116 },
      bias_coverage_buckets: {}, topic_gate: { stories_excluded_by_topic_gate: 88 },
      rss_feed_status: {} },
  };

  /* ---------------- init ---------------- */
  async function init() {
    wireControls();
    initMap();
    updateLocPanel(null);
    document.getElementById("clockLine").textContent =
      new Date().toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
    await loadData();
    updateNavCounts();
    renderTopicPills();
    renderRegions();
    renderBalance();
    renderSnapshot();
    renderBlindspotCard();
    renderTopicCards();
    renderTimeline();
    renderBiasSpectrum();
    renderDataCards();
    renderMeasureCards();
    renderFeed();
    renderSources("volume");
    renderHealth();
    renderFooter();
    homeLeftHTML = document.querySelector("#leftRail .rail-inner").innerHTML;
    homeRightHTML = document.querySelector("#rightRail .rail-inner").innerHTML;
    window.addEventListener("hashchange", onRoute);
    onRoute(); // honor any incoming deep link; defaults to #/stories
  }
  document.addEventListener("DOMContentLoaded", init);
})();
