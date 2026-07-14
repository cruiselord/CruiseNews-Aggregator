/* ===================================================================
   staticmap.js — lightweight inline-SVG mini map of Nigeria
   -------------------------------------------------------------------
   Renders a static SVG silhouette of Nigeria with a dot per tracked
   state (from geo.js STATE_META), highlighting a given set of states.
   No Leaflet, no tiles, no network — cheap enough to drop into every
   story page. The heavy interactive Leaflet map lives only in Sources.
   =================================================================== */
window.NaijaStaticMap = (function () {
  "use strict";
  const Geo = window.NaijaGeo;

  // viewBox + geographic bounds (approx. Nigeria bounding box).
  const W = 320, H = 300, PAD = 14;
  const B = { minLng: 2.5, maxLng: 15.0, minLat: 3.9, maxLat: 14.2 };

  function projX(lng) {
    return PAD + ((lng - B.minLng) / (B.maxLng - B.minLng)) * (W - 2 * PAD);
  }
  function projY(lat) {
    // latitude increases upward, SVG y increases downward -> invert
    return PAD + ((B.maxLat - lat) / (B.maxLat - B.minLat)) * (H - 2 * PAD);
  }

  // Rough Nigeria border outline (lng, lat), traced clockwise. Not survey-
  // grade — just enough to read as "Nigeria".
  const OUTLINE = [
    [3.6, 11.7], [4.1, 13.5], [6.0, 13.7], [8.5, 13.9], [10.8, 13.4],
    [13.1, 13.6], [14.7, 12.9], [14.2, 11.5], [13.3, 10.2], [13.2, 9.0],
    [12.5, 8.0], [11.9, 7.0], [10.5, 6.9], [9.0, 6.4], [8.7, 5.6],
    [8.3, 4.5], [7.3, 4.4], [6.4, 4.3], [5.4, 5.0], [4.8, 6.0],
    [3.4, 6.4], [2.7, 6.6], [2.75, 9.0], [3.6, 11.7],
  ];

  function outlinePath() {
    return OUTLINE.map((p, i) =>
      (i === 0 ? "M" : "L") + projX(p[0]).toFixed(1) + " " + projY(p[1]).toFixed(1)
    ).join(" ") + " Z";
  }

  // Render into `el` (or return the SVG string if el is falsy).
  // highlightStates: array of STATE_META keys to emphasise.
  function render(el, highlightStates) {
    const hi = new Set(highlightStates || []);
    const dots = [];
    for (const [name, m] of Object.entries(Geo.STATE_META)) {
      const rm = Geo.REGION_META[m.region] || Geo.REGION_META.national;
      const on = hi.has(name);
      const x = projX(m.lng).toFixed(1), y = projY(m.lat).toFixed(1);
      dots.push(
        `<circle class="ms-dot${on ? " on" : ""}" cx="${x}" cy="${y}" r="${on ? 6 : 2.6}"` +
        ` fill="${on ? rm.color : "rgba(237,231,216,.22)"}"` +
        (on ? ` stroke="${rm.color}" stroke-width="2" stroke-opacity=".35"` : "") +
        `><title>${name}</title></circle>` +
        (on ? `<text class="ms-label" x="${x}" y="${(+y - 9).toFixed(1)}" text-anchor="middle">${name}</text>` : "")
      );
    }

    const svg =
      `<svg class="mini-map-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Map of Nigeria showing coverage states">` +
        `<path class="ms-outline" d="${outlinePath()}"/>` +
        dots.join("") +
      `</svg>`;

    if (el) el.innerHTML = svg;
    return svg;
  }

  return { render };
})();
