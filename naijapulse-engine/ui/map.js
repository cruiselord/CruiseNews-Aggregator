/* ===================================================================
   map.js — world map with fly-to-state for Nigerian sources
   -------------------------------------------------------------------
   Uses Leaflet (loaded via CDN in index.html). Starts at world view,
   flies to a source's Nigerian state on click, highlights the state
   polygon, and returns geo info for the location panel. Degrades to a
   static region list if Leaflet or tiles fail to load (app.js handles
   that branch).
   =================================================================== */
window.NaijaMap = (function () {
  "use strict";
  const Geo = window.NaijaGeo;
  let map = null, ready = false;
  let outlineLayer = null, markerLayer = null, highlighted = null;

  const TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
  const TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

  function init(containerId) {
    if (typeof window.L === "undefined") return false; // Leaflet CDN blocked
    const el = document.getElementById(containerId);
    if (!el) return false;

    map = window.L.map(el, {
      center: [9.08, 8.68], zoom: 3, minZoom: 2, maxZoom: 10,
      worldCopyJump: true, zoomControl: true, attributionControl: true,
      scrollWheelZoom: true,
    });

    try {
      window.L.tileLayer(TILE_URL, {
        attribution: TILE_ATTR, subdomains: "abcd", maxZoom: 10,
      }).addTo(map);
    } catch (e) { /* tiles may fail offline; outline + markers still work */ }

    markerLayer = window.L.layerGroup().addTo(map);
    paintStates();
    ready = true;
    // Fit to Nigeria shortly after init so the world view "arrives".
    setTimeout(() => { if (map) map.flyTo([9.08, 8.68], 6, { duration: 1.6 }); }, 400);
    return true;
  }

  // Draw state outlines (real GeoJSON if reachable, else box fallback) and
  // a centroid marker per state coloured by region.
  async function paintStates() {
    const gj = await Geo.loadStatesGeoJSON();
    if (!map) return;
    outlineLayer = window.L.geoJSON(gj, {
      style: () => ({
        color: "rgba(217,164,65,.35)", weight: 1,
        fillColor: "rgba(46,139,105,.06)", fillOpacity: 1,
      }),
      onEachFeature: (feature, layer) => {
        const name = Geo.matchStateName(feature.properties);
        layer._stateName = name;
        layer.on("click", () => { if (name) focusState(name); });
      },
    }).addTo(map);

    for (const [name, m] of Object.entries(Geo.STATE_META)) {
      const rm = Geo.REGION_META[m.region] || Geo.REGION_META.national;
      const mk = window.L.circleMarker([m.lat, m.lng], {
        radius: 4, color: rm.color, weight: 1, fillColor: rm.color,
        fillOpacity: 0.85,
      });
      mk._stateName = name;
      mk.bindTooltip(name, { direction: "top", opacity: 0.9 });
      mk.on("click", () => focusState(name));
      markerLayer.addLayer(mk);
    }
  }

  function clearHighlight() {
    if (highlighted && outlineLayer) {
      highlighted.setStyle({
        color: "rgba(217,164,65,.35)", weight: 1,
        fillColor: "rgba(46,139,105,.06)", fillOpacity: 1,
      });
      highlighted = null;
    }
  }

  function highlightState(name) {
    if (!outlineLayer) return;
    outlineLayer.eachLayer((layer) => {
      if (layer._stateName === name) {
        clearHighlight();
        layer.setStyle({
          color: "#4FBF95", weight: 2.5,
          fillColor: "rgba(79,191,149,.28)", fillOpacity: 1,
        });
        highlighted = layer;
        try { layer.bringToFront(); } catch (e) {}
      }
    });
  }

  // Public: focus a state by name. Returns geo info for the panel.
  function focusState(name) {
    const m = Geo.STATE_META[name];
    if (!map || !m) return null;
    highlightState(name);
    map.flyTo([m.lat, m.lng], 7, { duration: 1.4 });
    return {
      state: name, city: null, region: m.region,
      lat: m.lat, lng: m.lng, lgas: m.lgas,
    };
  }

  // Public: focus a source (object with name + regional_base).
  function focusSource(src) {
    const g = Geo.resolveSource(src || {});
    if (!map) return g;
    if (g.state) {
      highlightState(g.state);
      map.flyTo([g.lat, g.lng], 7, { duration: 1.4 });
    } else {
      map.flyTo([g.lat, g.lng], 6, { duration: 1.4 });
    }
    return g;
  }

  // Public: focus a whole region (fly to its centroid, no single-state highlight).
  function focusRegion(regionKey) {
    const rm = Geo.REGION_META[regionKey] || Geo.REGION_META.national;
    if (!map) return { region: regionKey, lat: rm.centroid[1], lng: rm.centroid[0], lgas: [] };
    clearHighlight();
    map.flyTo([rm.centroid[1], rm.centroid[0]], 6, { duration: 1.4 });
    return { region: regionKey, lat: rm.centroid[1], lng: rm.centroid[0],
             city: null, state: null, lgas: [] };
  }

  // Recompute map size after the container becomes visible (view switch).
  function invalidate() { if (map) try { map.invalidateSize(); } catch (e) {} }

  return { init, focusState, focusSource, focusRegion, invalidate, isReady: () => ready };
})();
