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

  const API_BASE = (location.hostname === "localhost" || location.hostname === "127.0.0.1")
    ? "" : "http://localhost:8000";

  const LEAN_META = {
    pro_government: { label: "Pro-government", cls: "pro", color: "var(--lean-pro)" },
    anti_government:{ label: "Anti-government", cls: "anti", color: "var(--lean-anti)" },
    mixed:          { label: "Mixed", cls: "mixed", color: "var(--lean-mixed)" },
    independent:    { label: "Independent", cls: "indep", color: "var(--lean-indep)" },
  };
  const ORDER = ["pro_government", "anti_government", "mixed", "independent"];

  const PAGE_SIZE = 9;

  let STORIES = [], SOURCES = [], HEALTH = null, USING_MOCK = false;
  const MAP = window.NaijaMap, GEO = window.NaijaGeo,
        TOPICS = window.NaijaTopics, STATICMAP = window.NaijaStaticMap;

  let state = {
    sort: "recent", minOutlets: 1, politicalOnly: false,
    hideLowConf: false, topic: "all", side: "all", query: "", page: 0,
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

  /* ---------------- view switching ---------------- */
  const VIEWS = ["stories", "sources", "health"];
  function showView(name) {
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
    closeDrawer();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

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
    el.innerHTML = `<h4>Regional Coverage <span class="mini-note">where outlets sit</span></h4>
      <div class="region-list">${Object.keys(buckets).map((k) => {
        const rm = GEO.REGION_META[k];
        const n = buckets[k];
        return `<div class="region-row" data-region="${k}">
          <span class="rname"><span class="rdot" style="background:${rm.color}"></span>${rm.label}</span>
          <span class="rtrack"><i style="width:${(n / max) * 100}%"></i></span>
          <span class="rval">${n}</span></div>`;
      }).join("")}</div>`;
    el.querySelectorAll(".region-row").forEach((row) => {
      row.addEventListener("click", () => {
        showView("sources");
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
    const bar = ORDER.map((k) => {
      const pct = (totals[k] / total) * 100;
      return pct > 0 ? `<span class="${LEAN_META[k].cls}" style="width:${pct}%;background:${LEAN_META[k].color}"></span>` : "";
    }).join("");
    const legend = ORDER.map((k) => `
      <div class="lr"><span class="dot" style="background:${LEAN_META[k].color}"></span>
        <span class="lbl">${LEAN_META[k].label}</span><span class="val">${totals[k]}</span></div>`).join("");
    el.innerHTML = `<h4>Bias Spectrum <span class="mini-note">all coverage today</span></h4>
      <div class="spectrum-bar">${bar}</div>
      <div class="spectrum-legend">${legend}</div>`;
  }

  /* ---------------- TRENDING CAROUSEL ---------------- */
  function renderCarousel() {
    const track = document.getElementById("carouselTrack");
    const top = [...STORIES].sort((a, b) => b.count - a.count).slice(0, 8);
    if (!top.length) { track.innerHTML = ""; return; }
    track.className = "rail";
    track.innerHTML = top.map((s, i) => `
      <div class="rail-card" data-id="${s.id}" style="animation-delay:${i * 0.07}s">
        <div class="thumb">${thumbHTML(s, "📰")}</div>
        <div class="body">
          <div class="badge-row">
            ${s.political ? '<span class="badge badge-political">Political</span>' : ""}
            <span class="badge-conv">${s.count} outlets</span>
          </div>
          <h3>${s.title}</h3>
          ${ribbonHTML(s.dist)}
          ${confidenceChip(s.coverage)}
        </div>
      </div>`).join("");
    track.querySelectorAll(".rail-card").forEach((el) =>
      el.addEventListener("click", () => openStory(el.dataset.id)));
    // pull real images for the top cards (lightweight)
    top.forEach((s) => fetchStoryImage(s.id).then((url) => {
      if (!url) return;
      s.img = url;
      const card = track.querySelector(`.rail-card[data-id="${s.id}"] .thumb`);
      if (card) card.innerHTML = `<div class="photo-gradient"><img src="${url}" alt="" loading="lazy"></div>`;
    }));
  }
  async function fetchStoryImage(id) {
    try {
      const d = await fetchJSON("/stories/" + id);
      const m = (d.members || []).find((x) => x.image_url);
      return m ? m.image_url : null;
    } catch (e) { return null; }
  }

  /* ---------------- FEED (paginated) ---------------- */
  function getFilteredStories() {
    let list = STORIES.filter((s) => s.count >= state.minOutlets);
    if (state.politicalOnly) list = list.filter((s) => s.political);
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
      html += `
      <article class="story-card" data-id="${s.id}" style="animation-delay:${Math.min(i, 8) * 0.05}s">
        <div class="thumb">${thumbHTML(s, "📰")}</div>
        <div class="content">
          <div class="badge-row">
            ${s.blindspot ? `<span class="badge badge-blindspot"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01M10.3 3.9L2.5 17a2 2 0 001.7 3h15.6a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/></svg>Blindspot</span>` : ""}
            ${s.political ? '<span class="badge badge-political">Political</span>' : `<span class="badge-conv">${TOPICS.label(s.topic)}</span>`}
            <span class="badge-conv">${s.updated}</span>
          </div>
          <h3>${s.title}</h3>
          ${ribbonHTML(s.dist)}
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
    state = { ...state, minOutlets: 1, politicalOnly: false, hideLowConf: false, topic: "all", query: "", page: 0 };
    const mi = document.getElementById("minOutlets");
    if (mi) { mi.value = 1; document.getElementById("minOutletsVal").textContent = 1; }
    const po = document.getElementById("politicalOnly"); if (po) po.checked = false;
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

  async function openStory(id) {
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
      </div>
      ${blindBanner}
      ${bestSummary ? `<p class="story-lede">${bestSummary}</p>` : ""}
      <p class="story-lede-sub">Reported by <b>${s.count}</b> outlets across the lean spectrum. Read each side below — every article links out to its source.</p>`;

    // LEFT: donut + coverage + mini map
    const total = tagged(s.dist);
    const legend = ORDER.map((k) => `
      <div class="legend-row"><span class="dot" style="background:${LEAN_META[k].color}"></span>
        <span class="lbl">${LEAN_META[k].label}</span><span class="val">${s.dist[k]}</span></div>`).join("");
    const states = new Set();
    members.forEach((m) => { const g = GEO.SOURCE_GEO[m.source_name]; if (g && g.state) states.add(g.state); });
    document.getElementById("storyLeft").innerHTML = `
      <div class="panel">
        <h4>Bias distribution <span class="mono" style="color:var(--text-faint);font-weight:400;">${total} tagged</span></h4>
        <div class="donut-wrap">${donutSVG(s.dist)}<div class="donut-legend">${legend}</div></div>
        <div class="coverage-meter">
          <div class="note">Coverage confidence — based on ${total} of ${s.count} articles</div>
          <div class="track"><div class="fill" style="width:${s.coverage}%"></div></div>
          <div class="note">${s.coverage < 50 ? "Thin sample — treat this reading as directional, not definitive." : Math.round(s.coverage) + "% of members have a known source lean."}</div>
        </div>
      </div>
      <div class="panel">
        <h4>Where it's covered <span class="mini-note">source states</span></h4>
        <div class="story-map" id="storyMap"></div>
      </div>`;
    if (STATICMAP && states.size) STATICMAP.render(document.getElementById("storyMap"), [...states]);

    // RIGHT: convergence + topics + ad + facts
    const topSources = [...members].sort((a, b) => b.also_reported_by - a.also_reported_by).slice(0, 4);
    const convChips = topSources.length
      ? topSources.map((m) => `<span class="conv-chip">${m.source_name || "Unknown"}<b>+${m.also_reported_by}</b></span>`).join("")
      : `<span class="loc-empty">Single-outlet story — no convergence yet.</span>`;
    document.getElementById("storyRight").innerHTML = `
      <div class="panel">
        <h4>Convergence</h4>
        <div class="conv-row">${convChips}</div>
        <p class="conv-note">${s.count} outlet${s.count === 1 ? "" : "s"} carried this story. Higher "also reported by" means broad agreement.</p>
      </div>
      <div class="panel">
        <h4>Similar topics</h4>
        <div class="similar-tags">
          <a>${TOPICS.label(s.topic)}</a><a>Nigeria</a><a>Governance</a><a>${s.political ? "Political" : "Human interest"}</a>
        </div>
      </div>
      <div class="panel key-facts">
        <h4>Key facts</h4>
        <div class="kf-row"><span>First seen</span><b>${relTime(data.first_seen_at)}</b></div>
        <div class="kf-row"><span>Last updated</span><b>${s.updated}</b></div>
        <div class="kf-row"><span>Tagged sample</span><b>${total}/${s.count}</b></div>
        <div class="kf-row"><span>Blindspot</span><b>${s.blindspot ? "Yes" : "No"}</b></div>
      </div>
      <div class="ad-slot rect" data-ad="detail-rect"><span class="tag">Advertisement</span><span>Google AdSense · 300×250</span></div>`;

    // CENTER: side tabs + member list
    state.side = "all";
    const sides = ["all", "pro_government", "anti_government", "mixed", "independent"];
    const sideCounts = {};
    sides.forEach((sd) => (sideCounts[sd] = sd === "all" ? members.length
      : members.filter((m) => m.side === sd).length));
    const center = document.getElementById("storyCenter");
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

    // show the detail view
    VIEWS.forEach((v) => { document.getElementById("view-" + v).hidden = true; });
    document.getElementById("view-story-detail").hidden = false;
    document.getElementById("topicBar").style.display = "none";
    document.querySelectorAll(".menu-btn").forEach((b) => b.classList.remove("active"));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function sideOfLean(lean) {
    if (!lean) return "independent";
    if (lean === "pro_government") return "pro_government";
    if (lean === "anti_government") return "anti_government";
    if (lean === "mixed") return "mixed";
    return "independent";
  }

  function backToFeed() {
    document.getElementById("view-story-detail").hidden = true;
    document.getElementById("view-stories").hidden = false;
    document.getElementById("topicBar").style.display = "flex";
    state.side = "all";
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
      n.addEventListener("click", () => showView(n.dataset.view)));
    document.getElementById("themeToggle").addEventListener("click", () => {
      const html = document.documentElement;
      html.setAttribute("data-theme", html.getAttribute("data-theme") === "dark" ? "light" : "dark");
    });
    const si = document.getElementById("searchInput");
    if (si) si.addEventListener("input", (e) => {
      state.query = e.target.value.trim(); state.page = 0; renderFeed();
    });
    document.getElementById("brandHome").addEventListener("click", () => { showView("stories"); });
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
      blindspot: { flagged: 0 }, per_source_article_counts: [],
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
    renderTopicCards();
    renderTimeline();
    renderBiasSpectrum();
    renderCarousel();
    renderFeed();
    renderSources("volume");
    renderHealth();
    renderFooter();
    showView("stories");
  }
  document.addEventListener("DOMContentLoaded", init);
})();
