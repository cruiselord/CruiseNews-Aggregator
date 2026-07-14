/* ===================================================================
   geo.js — client-side geographic enrichment for Nigerian sources
   -------------------------------------------------------------------
   The DB stores only `regional_base` (south_west / national / north) and
   no coordinates. This file authors the missing geography so the map can
   fly to a state and show nearby LGAs. No backend change required.
   Coordinates are approximate state centroids; LGA lists are the major
   local governments / cities per state (curated, not exhaustive).
   =================================================================== */
window.NaijaGeo = (function () {
  "use strict";

  // Region metadata (matches source_bias.regional_base values).
  const REGION_META = {
    south_west: { label: "South West", color: "#4FBF95", centroid: [7.05, 4.0] },
    national:   { label: "National / FCT", color: "#D9A441", centroid: [7.45, 9.05] },
    north:      { label: "North", color: "#6E7B8B", centroid: [8.2, 11.0] },
  };

  // 36 states + FCT -> centroid, region bucket, major LGAs/cities.
  // region bucket: "south_west" | "north" | "national" (south-south/east folded
  // into the "national" coverage bucket, same as the prototype's regions).
  const STATE_META = {
    "Lagos":      { lat: 6.55, lng: 3.35, region: "south_west", lgas: ["Ikeja","Lagos Island","Alimosho","Surulere","Ikorodu","Epe"] },
    "Ogun":       { lat: 7.15, lng: 3.35, region: "south_west", lgas: ["Abeokuta","Sagamu","Ijebu-Ode","Ifo","Ipokia"] },
    "Oyo":        { lat: 7.40, lng: 3.90, region: "south_west", lgas: ["Ibadan","Ogbomoso","Oyo","Iseyin","Kajola"] },
    "Osun":       { lat: 7.55, lng: 4.55, region: "south_west", lgas: ["Osogbo","Ife","Ilesa","Ikirun","Ede"] },
    "Ondo":       { lat: 7.25, lng: 5.10, region: "south_west", lgas: ["Akure","Ondo","Owo","Ikare","Okitipupa"] },
    "Ekiti":      { lat: 7.65, lng: 5.30, region: "south_west", lgas: ["Ado-Ekiti","Ikere","Ijero","Irepodun-Ifelodun"] },

    "FCT":        { lat: 9.05, lng: 7.45, region: "national", lgas: ["Abuja","Gwagwalada","Kuje","Bwari","Kwali"] },
    "Abia":       { lat: 5.45, lng: 7.55, region: "national", lgas: ["Umuahia","Aba","Ohafia","Arochukwu"] },
    "Akwa Ibom":  { lat: 5.05, lng: 7.90, region: "national", lgas: ["Uyo","Ikot Ekpene","Eket","Oron"] },
    "Anambra":    { lat: 6.20, lng: 7.00, region: "national", lgas: ["Awka","Onitsha","Nnewi","Ekwusigo"] },
    "Bayelsa":    { lat: 4.75, lng: 6.10, region: "national", lgas: ["Yenagoa","Brass","Kaiama","Ogbia"] },
    "Cross River":{ lat: 5.85, lng: 8.30, region: "national", lgas: ["Calabar","Ikom","Ogoja","Odukpani"] },
    "Delta":      { lat: 5.55, lng: 6.00, region: "national", lgas: ["Asaba","Warri","Ughelli","Sapele"] },
    "Ebonyi":     { lat: 6.25, lng: 8.10, region: "national", lgas: ["Abakaliki","Afikpo","Onueke","Ikwo"] },
    "Edo":        { lat: 6.55, lng: 5.80, region: "national", lgas: ["Benin City","Auchi","Ekpoma","Oredo"] },
    "Enugu":      { lat: 6.45, lng: 7.50, region: "national", lgas: ["Enugu","Nsukka","Awgu","Udi"] },
    "Imo":        { lat: 5.55, lng: 7.05, region: "national", lgas: ["Owerri","Okigwe","Orlu","Mbaitoli"] },
    "Rivers":     { lat: 4.85, lng: 6.95, region: "national", lgas: ["Port Harcourt","Eleme","Bonny","Obio-Akpor"] },

    "Adamawa":    { lat: 9.30, lng: 12.45, region: "north", lgas: ["Yola","Mubi","Jimeta","Numan"] },
    "Bauchi":     { lat: 10.30, lng: 9.85, region: "north", lgas: ["Bauchi","Azare","Misau","Katagum"] },
    "Borno":      { lat: 11.85, lng: 13.15, region: "north", lgas: ["Maiduguri","Bama","Biu","Dikwa"] },
    "Gombe":      { lat: 10.30, lng: 11.15, region: "north", lgas: ["Gombe","Dukku","Kaltungo","Funakaye"] },
    "Jigawa":     { lat: 12.10, lng: 9.55, region: "north", lgas: ["Dutse","Hadejia","Kazaure","Birnin Kudu"] },
    "Kaduna":     { lat: 10.55, lng: 7.45, region: "north", lgas: ["Kaduna","Zaria","Kafanchan","Giwa"] },
    "Kano":       { lat: 11.95, lng: 8.50, region: "north", lgas: ["Kano","Gwale","Wudil","Ungogo"] },
    "Katsina":    { lat: 12.95, lng: 7.60, region: "north", lgas: ["Katsina","Daura","Funtua","Dutsin-Ma"] },
    "Kebbi":      { lat: 12.45, lng: 4.20, region: "north", lgas: ["Birnin Kebbi","Argungu","Yauri","Sakaba"] },
    "Kogi":       { lat: 7.80, lng: 6.75, region: "north", lgas: ["Lokoja","Okene","Kabba","Ibaji"] },
    "Kwara":      { lat: 8.50, lng: 4.55, region: "north", lgas: ["Ilorin","Offa","Jebba","Edu"] },
    "Nasarawa":   { lat: 8.50, lng: 7.65, region: "north", lgas: ["Lafia","Keffi","Akwanga","Nasarawa Eggon"] },
    "Niger":      { lat: 9.95, lng: 5.10, region: "north", lgas: ["Minna","Bida","Suleja","Kontagora"] },
    "Plateau":    { lat: 9.20, lng: 9.55, region: "north", lgas: ["Jos","Barkin Ladi","Pankshin","Langtang"] },
    "Sokoto":     { lat: 13.05, lng: 5.25, region: "north", lgas: ["Sokoto","Wamako","Tambuwal","Gwadabawa"] },
    "Taraba":     { lat: 8.05, lng: 10.95, region: "north", lgas: ["Jalingo","Wukari","Gembu","Bali"] },
    "Yobe":       { lat: 11.75, lng: 11.45, region: "north", lgas: ["Damaturu","Potiskum","Nguru","Gashua"] },
    "Zamfara":    { lat: 12.10, lng: 6.25, region: "north", lgas: ["Gusau","Anka","Talata Mafara","Bungudu"] },
  };

  // Source -> city/state. Keyed by the `name` returned by /sources.
  // International desks are placed at their Nigerian bureau city.
  const SOURCE_GEO = {
    "TheCable":          { city: "Abuja", state: "FCT" },
    "BusinessDay":       { city: "Lagos", state: "Lagos" },
    "Daily Post":        { city: "Lagos", state: "Lagos" },
    "Daily Trust":       { city: "Abuja", state: "FCT" },
    "Guardian NG":       { city: "Lagos", state: "Lagos" },
    "Premium Times":     { city: "Abuja", state: "FCT" },
    "Punch":             { city: "Lagos", state: "Lagos" },
    "The Nation":        { city: "Lagos", state: "Lagos" },
    "ThisDay":           { city: "Lagos", state: "Lagos" },
    "Tribune":           { city: "Ibadan", state: "Oyo" },
    "Vanguard":          { city: "Lagos", state: "Lagos" },
    "Channels TV":       { city: "Lagos", state: "Lagos" },
    "Sahara Reporters":  { city: "Lagos", state: "Lagos" },
    "Peoples Gazette":   { city: "Abuja", state: "FCT" },
    "Leadership":        { city: "Abuja", state: "FCT" },
    "Nairametrics":      { city: "Lagos", state: "Lagos" },
    "The Whistler":      { city: "Abuja", state: "FCT" },
    "Financial Watch":   { city: "Lagos", state: "Lagos" },
    "Blueprint":         { city: "Abuja", state: "FCT" },
    "BBC Sport":         { city: "Lagos", state: "Lagos" },
    "Premium Times Sports": { city: "Abuja", state: "FCT" },
    "Sporting Life":     { city: "Lagos", state: "Lagos" },
    "BBC News":          { city: "Abuja", state: "FCT" },
    "Al Jazeera":        { city: "Abuja", state: "FCT" },
    "MyJoyOnline":       { city: "Lagos", state: "Lagos" },
    "Standard Digital":  { city: "Lagos", state: "Lagos" },
  };

  function regionOfState(state) {
    const m = STATE_META[state];
    return m ? m.region : "national";
  }

  // Resolve a source (id/name + regional_base) to a geo point + region.
  function resolveSource(src) {
    const g = SOURCE_GEO[src.name];
    if (g && STATE_META[g.state]) {
      const sm = STATE_META[g.state];
      return { city: g.city, state: g.state, lat: sm.lat, lng: sm.lng,
               region: sm.region, lgas: sm.lgas };
    }
    // Fallback: fly to the region centroid.
    const rm = REGION_META[src.regional_base] || REGION_META.national;
    return { city: null, state: null, lat: rm.centroid[1], lng: rm.centroid[0],
             region: src.regional_base || "national", lgas: [] };
  }

  // Build a tiny box-polygon GeoJSON per state so the map can highlight a
  // state even when offline (no external GeoJSON fetch available).
  function fallbackGeoJSON() {
    const features = [];
    for (const [name, m] of Object.entries(STATE_META)) {
      const d = 0.45;
      features.push({
        type: "Feature",
        properties: { name: name },
        geometry: {
          type: "Polygon",
          coordinates: [[
            [m.lng - d, m.lat - d], [m.lng + d, m.lat - d],
            [m.lng + d, m.lat + d], [m.lng - d, m.lat + d],
            [m.lng - d, m.lat - d],
          ]],
        },
      });
    }
    return { type: "FeatureCollection", features: features };
  }

  // Try a real Nigeria states GeoJSON from a CDN; fall back to boxes.
  async function loadStatesGeoJSON() {
    const CDN = "https://raw.githubusercontent.com/richardimaoka/nigeria-geojson/master/ng.json";
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 6000);
      const res = await fetch(CDN, { signal: ctrl.signal });
      clearTimeout(t);
      if (!res.ok) throw new Error("bad status");
      const gj = await res.json();
      if (gj && gj.features && gj.features.length) return gj;
      throw new Error("empty");
    } catch (e) {
      return fallbackGeoJSON();
    }
  }

  // Normalise a GeoJSON feature's state name to our STATE_META key.
  function matchStateName(props) {
    const raw = props && (props.name || props.NAME_1 || props.NAME ||
                          props.state || props.STATE);
    if (!raw) return null;
    const r = String(raw).trim().toLowerCase();
    for (const k of Object.keys(STATE_META)) {
      if (k.toLowerCase() === r) return k;
    }
    // loose contains match (e.g. "Federal Capital Territory" -> FCT)
    if (r.includes("federal capital") || r === "fct") return "FCT";
    for (const k of Object.keys(STATE_META)) {
      if (r.includes(k.toLowerCase())) return k;
    }
    return null;
  }

  return {
    REGION_META, STATE_META, SOURCE_GEO,
    regionOfState, resolveSource, fallbackGeoJSON,
    loadStatesGeoJSON, matchStateName,
  };
})();
