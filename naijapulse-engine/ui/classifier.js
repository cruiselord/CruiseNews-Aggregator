/* ===================================================================
   classifier.js — client-side topic classifier
   -------------------------------------------------------------------
   The pipeline stores only a political / non-political flag, not a
   topic. We derive a richer 8-topic taxonomy (matching the prototype's
   nav) from titles via keyword scoring. Pure, dependency-free, runs in
   the browser. No backend change required.
   =================================================================== */
window.NaijaTopics = (function () {
  "use strict";

  // Display order + human labels for the topic pills.
  const TOPIC_META = {
    politics:     "Politics",
    security:     "Security",
    economy:      "Economy",
    elections:    "Elections 2027",
    naira:        "Naira & Markets",
    floods:       "Floods & Climate",
    sports:       "Sports",
    entertainment:"Entertainment",
    general:      "General",
  };
  const TOPIC_ORDER = ["politics","security","economy","elections","naira","floods","sports","entertainment","general"];

  // Keyword maps. Order within the map does not matter; PRIORITY breaks ties.
  const TOPIC_KEYWORDS = {
    politics: [
      "politic","politician","government","govt","president","presidency","vice president",
      "minister","cabinet","governor","deputy governor","senate","senator","house of reps",
      "assembly","legislature","lawmaker","lawmakers","parliament","apc","pdp","labour party",
      "nnpp","lp ","political party","policy","constitution","tribunal","judiciary","supreme court",
      "court","subsidy","democracy","protest","strike","union"," corruption","corrupt","efcc",
      "icpc","civil society","cabinet","speaker","ondo","offa","appointment","resign","impeach",
    ],
    security: [
      "security","insurgency","terror","terrorist","terrorism","boko haram","iswap","bandit",
      "bandits","banditry","kidnap","kidnapped","kidnapping","abduction","abducted","abduct",
      "herdsmen","farmers","police","police ","npf","army","military","dhq","nscdc","civil defence",
      "troops","soldier","soldiers","war","coup","ipob","ekit","violence","attack","attacked",
      "explosion","bomb","militant","militants","killings","clash","crisis","curfew","siege",
    ],
    economy: [
      "economy","economic","inflation","recession","gdp","debt","budget","tax","taxation",
      "revenue","fgn","bond","subsidy","petrol","fuel","diesel","kerosene","refinery","nnpc",
      "dangote refinery","trade","export","import","manufactur","industry","industrial","smse",
      "employment","job","jobs","wage","minimum wage","growth","investment","investor","fdi",
      "forex","prices","cost of living","poverty","subsidy removal","palliative",
    ],
    elections: [
      "election","elections","inec","voter","voters","voting","ballot","polling","polls",
      "registered voter","permanent voter","pvc","candidate","candidacy","campaign","campaigns",
      "2027","primaries","primary election","runoff","collation","result","results","declared",
      "won","defeated","constituency","electoral","register","registration","wards","delimitation",
    ],
    naira: [
      "naira","cbn","central bank","godwin emefiele","olanipekun","cardoso","dollar","usd",
      "exchange rate","forex","fx ","currency","devalue","devaluation","parallel market",
      "black market","pound","euro","crypto","bitcoin","remittance","imf","world bank","loan",
      "imf loan","bailout","mint","minting","coins","cashless","epayment","transfer",
    ],
    floods: [
      "flood","floods","flooding","rain","rainfall","climate","climate change","drought",
      "erosion","gully","storm","thunder","lightning","disaster","natural disaster","weather",
      "environment","environmental","heatwave","dry season","wet season","lagdo","dam","relief",
      "idp","displaced","evacuat","emergency","niemet","meteorological",
    ],
    sports: [
      "football","soccer","super eagles","afcon","nff","eaglets","falconets","premier league",
      "npfl","match","matches","goal","goals","player","players","coach","coaches","league",
      "world cup","olympic","olympics","medal","medals","sport","sports","athlete","athletes",
      "tennis","basketball","afrobasket","caa","championship","victory","trophy","tournament",
      "freestyle","wrestling","boxing","super cup",
    ],
    entertainment: [
      "nollywood","afrobeats","music","song","album","artist","artiste","singer","actor",
      "actress","movie","film","films","cinema","celebrity","bbnaija","big brother","concert",
      "tour","tours","award","awards","grammy","amvca","skit","comedian","comedy","producer",
      "director","reality show","streaming","netflix","track","video","entertainment",
    ],
  };

  // Fixed priority to break ties (more "structural" topics win over generic ones).
  const PRIORITY = {
    elections: 0, sports: 1, entertainment: 2, floods: 3, naira: 4,
    security: 5, economy: 6, politics: 7, general: 99,
  };

  function score(text, topic) {
    if (!text) return 0;
    const low = " " + text.toLowerCase() + " ";
    let n = 0;
    for (const kw of TOPIC_KEYWORDS[topic]) {
      // Pad keywords so we match word-ish boundaries without a regex per word.
      if (low.includes(kw)) n++;
    }
    return n;
  }

  // Returns the best topic key for a single title (or combined text).
  function classify(text) {
    let best = "general", bestScore = 0, bestPri = PRIORITY.general;
    for (const topic of TOPIC_ORDER) {
      if (topic === "general") continue;
      const s = score(text, topic);
      if (s === 0) continue;
      const pri = PRIORITY[topic];
      if (s > bestScore || (s === bestScore && pri < bestPri && bestScore > 0)) {
        best = topic; bestScore = s; bestPri = pri;
      }
    }
    return best;
  }

  // Count stories per topic for the pill badges.
  function countTopics(stories) {
    const counts = {};
    TOPIC_ORDER.forEach((t) => (counts[t] = 0));
    for (const s of stories) {
      const t = s.topic || classify(s.title || "");
      counts[t] = (counts[t] || 0) + 1;
    }
    return counts;
  }

  function label(topic) { return TOPIC_META[topic] || topic; }

  return { TOPIC_META, TOPIC_ORDER, classify, countTopics, label };
})();
