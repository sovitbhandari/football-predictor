const $ = (id) => document.getElementById(id);
const pct = (value) => `${(Number(value) * 100).toFixed(1)}%`;
const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
const cache = new Map();
const LIVE_POLL_MS = 45000;

const statusEl = $("status");
const results = $("results");
const homeSelect = $("home");
const awaySelect = $("away");
const leagueSelect = $("league");
const labLeagueSelect = $("lab-league");

let navData = { main: [], other: [], all: [] };
let view = "home"; // home | league | lab
let activeLeagueId = "";
let browseDate = todayISO();
let livePayload = { live: [], retrieval: "ok", count: 0 };
let browsePayload = null;
let selectedKey = "";
let selectedMatch = null;
let predictToken = 0;
let browseToken = 0;
let predictAbort = null;
let liveTimer = 0;
let otherOpen = false;
let scrollMemory = { home: 0, league: 0 };
let labToken = 0;
let labCache = new Map();

function todayISO(offsetDays = 0) {
  const d = new Date();
  d.setHours(12, 0, 0, 0);
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function initials(name) {
  return String(name || "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function crest(url, name, size) {
  const fallbackClass = size === "lg" ? "crest-lg-fallback" : "crest-fallback";
  const imgClass = size === "lg" ? "crest-lg" : "crest";
  const label = escapeHtml(initials(name));
  if (!url) return `<div class="${fallbackClass}">${label}</div>`;
  return `<span class="crest-wrap">
    <img class="${imgClass}" src="${escapeHtml(url)}" alt="" loading="lazy" decoding="async" onerror="this.style.display='none';this.nextElementSibling.style.display='grid'">
    <div class="${fallbackClass}" style="display:none">${label}</div>
  </span>`;
}

function when(iso, mode = "full") {
  if (!iso) return "";
  const date = new Date(iso);
  if (mode === "time") {
    return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  if (mode === "card") {
    const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    return time;
  }
  return date.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

function fixtureKey(match) {
  return `${match.league_id || ""}|${match.fixture_id || ""}|${match.home}|${match.away}|${match.kickoff || ""}`;
}

function params() {
  return new URLSearchParams(window.location.search);
}

function writeParams(next, { push = false } = {}) {
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(next)) {
    if (value == null || value === "") url.searchParams.delete(key);
    else url.searchParams.set(key, value);
  }
  if (push) history.pushState({}, "", url);
  else history.replaceState({}, "", url);
}

function setOptions(select, values, selected) {
  select.innerHTML = values.map((value) => {
    const mark = value === selected ? " selected" : "";
    return `<option value="${escapeHtml(value)}"${mark}>${escapeHtml(value)}</option>`;
  }).join("");
}

function setLeagueOptions(select, selectedId) {
  select.innerHTML = navData.all.map((item) => {
    const mark = item.id === selectedId ? " selected" : "";
    return `<option value="${escapeHtml(item.id)}"${mark}>${escapeHtml(item.name)} · ${escapeHtml(item.country)}</option>`;
  }).join("");
}

function formatApiError(detail) {
  if (detail == null) return "Request failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  return String(detail);
}

function foldTeam(name) {
  return String(name || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/'/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function teamsEqual(a, b) {
  const fa = foldTeam(a);
  const fb = foldTeam(b);
  if (!fa || !fb) return false;
  if (fa === fb) return true;
  if (fa.replaceAll("y", "") === fb.replaceAll("y", "")) return true;
  const ta = fa.split(" ").filter(Boolean);
  const tb = fb.split(" ").filter(Boolean);
  const subset = (small, big) => small.every((token) => big.includes(token));
  if (subset(ta, tb) || subset(tb, ta)) {
    const smaller = ta.join(" ").length <= tb.join(" ").length ? ta : tb;
    return smaller.join("").length >= 4;
  }
  return false;
}

function catalogNameFrom(values, ...names) {
  for (const name of names) {
    if (!name) continue;
    const hits = values.filter((value) => teamsEqual(value, name));
    if (hits.length === 1) return hits[0];
  }
  return null;
}

/* ——— Compact fixture row ——— */
function FixtureRow(match, { showCompetition = false } = {}) {
  const active = fixtureKey(match) === selectedKey ? " is-active" : "";
  const live = match.status === "live" ? " is-live" : "";
  const center = match.status === "live"
    ? `<div class="fx-score">${escapeHtml(match.score || "–")}</div>
       <div class="fx-live-meta">${escapeHtml(match.minute || "Live")}</div>`
    : `<div class="fx-kick">${escapeHtml(when(match.kickoff, "time"))}</div>`;
  const tag = showCompetition && match.league_name
    ? `<div class="fx-comp-tag">${escapeHtml(match.league_name)}</div>`
    : "";
  return `<button type="button" class="fx-row${active}${live}" data-key="${escapeHtml(fixtureKey(match))}" data-league="${escapeHtml(match.league_id || "")}">
    ${tag}
    <div class="fx-side home">
      ${crest(match.home_logo, match.home)}
      <span class="fx-name">${escapeHtml(match.home_short || match.home)}</span>
    </div>
    <div class="fx-center">${center}</div>
    <div class="fx-side away">
      ${crest(match.away_logo, match.away)}
      <span class="fx-name">${escapeHtml(match.away_short || match.away)}</span>
    </div>
  </button>`;
}

function paintNav() {
  const mainIds = new Set(navData.main.map((item) => item.id));
  const otherActive = view === "league" && activeLeagueId && !mainIds.has(activeLeagueId);
  // Main links live in the horizontal scroller; Other sits outside so its menu
  // is not clipped by overflow-x:auto.
  $("top-nav").innerHTML = [
    `<button type="button" class="nav-link${view === "home" ? " is-active" : ""}" data-nav="home">Home</button>`,
    `<button type="button" class="nav-link${view === "lab" ? " is-active" : ""}" data-nav="lab">Model Lab</button>`,
    ...navData.main.map((item) =>
      `<button type="button" class="nav-link${view === "league" && activeLeagueId === item.id ? " is-active" : ""}" data-nav="league" data-id="${escapeHtml(item.id)}">${escapeHtml(item.name)}</button>`
    ),
  ].join("");
  const otherItems = navData.other.map((item) =>
    `<button type="button" data-nav="league" data-id="${escapeHtml(item.id)}" class="${activeLeagueId === item.id ? "is-active" : ""}">${escapeHtml(item.name)} · ${escapeHtml(item.country)}</button>`
  ).join("");
  $("nav-other").innerHTML = `
    <button type="button" class="nav-other-btn${otherActive ? " is-active" : ""}" id="other-toggle" aria-expanded="${otherOpen}">Other ▾</button>
  `;
  // Portal the menu to <body> so overflow/stacking from .wrap cannot hide it.
  let menu = $("other-menu");
  if (!menu) {
    menu = document.createElement("div");
    menu.id = "other-menu";
    document.body.appendChild(menu);
  } else if (menu.parentElement !== document.body) {
    document.body.appendChild(menu);
  }
  menu.className = "nav-other-menu";
  menu.innerHTML = otherItems;
  menu.classList.toggle("hidden", !otherOpen);
  if (otherOpen) {
    const toggle = $("other-toggle");
    const rect = toggle.getBoundingClientRect();
    const width = Math.max(240, rect.width);
    let left = rect.right - width;
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    menu.style.cssText = [
      "position:fixed",
      "z-index:10000",
      `top:${Math.round(rect.bottom + 6)}px`,
      `left:${Math.round(left)}px`,
      "right:auto",
      `min-width:${width}px`,
      "max-height:min(320px,70vh)",
      "overflow:auto",
    ].join(";");
  } else {
    menu.style.cssText = "";
  }

  setLeagueOptions(leagueSelect, activeLeagueId || navData.all[0]?.id);
  setLeagueOptions(labLeagueSelect, activeLeagueId || labLeagueSelect.value || navData.all[0]?.id);
}

function paintLive() {
  const live = livePayload.live || [];
  const retrieval = livePayload.retrieval || "ok";
  const asOf = livePayload.as_of;
  const scrollBox = $("live-now");
  const prevScroll = scrollBox.scrollTop;

  if (retrieval === "error") {
    $("live-as-of").innerHTML = asOf ? `As of ${escapeHtml(when(asOf))}` : "";
    scrollBox.innerHTML = `<p class="live-empty live-error">${escapeHtml(livePayload.message || "Live scores could not be retrieved.")}</p>`;
    return;
  }
  const countLabel = live.length
    ? `<span class="live-count"><span class="live-dot"></span>${live.length} live · ${escapeHtml(asOf ? when(asOf) : "now")}</span>`
    : (asOf ? `As of ${when(asOf)}` : "");
  const warn = retrieval === "partial"
    ? `<p class="live-empty live-warn">${escapeHtml(livePayload.message || "Partial live coverage.")}</p>`
    : "";
  $("live-as-of").innerHTML = countLabel;
  if (!live.length) {
    scrollBox.innerHTML = `${warn}<p class="live-empty">${escapeHtml(livePayload.message || "No live games across supported competitions right now.")}</p>`;
  } else {
    scrollBox.innerHTML = `${warn}<div class="live-rows">${live.map((match) => FixtureRow(match, { showCompetition: true })).join("")}</div>`;
  }
  scrollBox.scrollTop = prevScroll;
}

function competitionPanel(panel) {
  const logo = panel.logo
    ? `<img class="comp-logo" src="${escapeHtml(panel.logo)}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='grid'"><div class="comp-logo-fallback" style="display:none">${escapeHtml(initials(panel.name))}</div>`
    : `<div class="comp-logo-fallback">${escapeHtml(initials(panel.name))}</div>`;
  let body;
  if (panel.status === "error") {
    body = `<p class="panel-empty live-error">Could not load fixtures${panel.error ? `: ${escapeHtml(panel.error)}` : "."}</p>`;
  } else if (!(panel.fixtures || []).length) {
    body = `<p class="panel-empty">No upcoming fixtures on this date.</p>`;
  } else {
    body = `<div class="fx-list">${panel.fixtures.map((match) => FixtureRow(match)).join("")}</div>`;
  }
  return `<article class="comp-panel" data-league="${escapeHtml(panel.id)}">
    <div class="comp-head">
      <div class="comp-head-left">
        ${logo}
        <div style="min-width:0">
          <h3 class="comp-title">${escapeHtml(panel.name)}</h3>
          <p class="comp-sub">${escapeHtml(panel.date)} · ${escapeHtml(panel.season || "")}</p>
        </div>
      </div>
      <a class="view-league" href="?view=league&league=${encodeURIComponent(panel.id)}&date=${encodeURIComponent(browseDate)}" data-view-league="${escapeHtml(panel.id)}">View league</a>
    </div>
    ${body}
  </article>`;
}

function paintBrowse() {
  const title = view === "home" ? "Upcoming fixtures" : (browsePayload?.competitions?.[0]?.name || "Fixtures");
  $("browse-title").textContent = title;
  $("date-picker").value = browseDate;
  $("date-today").classList.toggle("is-active", browseDate === todayISO(0));
  $("date-tomorrow").classList.toggle("is-active", browseDate === todayISO(1));

  if (!browsePayload) {
    $("competition-panels").innerHTML = `<div class="skel"></div><div class="skel"></div>`;
    return;
  }
  let panels = browsePayload.competitions || [];
  if (view === "home") {
    panels = panels.filter((panel) => (panel.fixtures || []).length || panel.status === "error");
  }
  if (!panels.length) {
    const next = browsePayload.next_date_with_fixtures;
    $("competition-panels").innerHTML = `<div class="panel-empty">
      No upcoming fixtures on ${escapeHtml(browseDate)}.
      ${next ? `<button type="button" class="chip-btn" id="jump-next-date" data-date="${escapeHtml(next)}" style="margin-left:8px">Jump to ${escapeHtml(next)}</button>` : ""}
    </div>`;
    return;
  }
  $("competition-panels").innerHTML = panels.map(competitionPanel).join("");
}

function showActiveView() {
  const browsing = view !== "lab";
  $("live-card").classList.toggle("hidden", !browsing);
  $("browse-card").classList.toggle("hidden", !browsing);
  $("custom-card").classList.toggle("hidden", !browsing);
  $("model-lab-card").classList.toggle("hidden", view !== "lab");
  if (view === "lab") results.classList.add("hidden");
}

function fmtMetric(value, digits = 3) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function fmtPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function metricCard(label, value, sub = "") {
  return `<div class="stat-box">
    <div class="k">${escapeHtml(label)}</div>
    <div class="v">${escapeHtml(value)}</div>
    ${sub ? `<div class="s">${escapeHtml(sub)}</div>` : ""}
  </div>`;
}

function EvaluationMetrics(title, item) {
  return `<article class="lab-metric-card">
    <div class="combo-top">
      <div>
        <div class="combo-title">${escapeHtml(title)}</div>
        <div class="explain">ξ ${escapeHtml(String(item.xi))} · ${escapeHtml(String(item.n || 0))} holdout matches</div>
      </div>
    </div>
    <div class="lab-metric-grid">
      ${metricCard("Log loss", fmtMetric(item.log_loss), "lower is better")}
      ${metricCard("Brier", fmtMetric(item.brier), "lower is better")}
      ${metricCard("Accuracy", fmtPct(item.result_accuracy), "secondary metric")}
      ${metricCard("Avg confidence", fmtPct(item.avg_confidence), "chosen result")}
    </div>
  </article>`;
}

function CalibrationTable(rows) {
  const body = (rows || []).map((row) => `<div class="cal-row">
    <span>${escapeHtml(row.range)}</span>
    <span>${escapeHtml(String(row.n))}</span>
    <span>${escapeHtml(fmtPct(row.avg_confidence))}</span>
    <span>${escapeHtml(fmtPct(row.hit_rate))}</span>
  </div>`).join("");
  return `<div class="cal-table">
    <div class="cal-row cal-head"><span>Confidence</span><span>N</span><span>Avg</span><span>Hit rate</span></div>
    ${body}
  </div>`;
}

function SamplePredictionRows(rows) {
  if (!(rows || []).length) return `<p class="explain">No sample predictions available.</p>`;
  return `<div class="sample-table">
    ${(rows || []).map((row) => `<div class="sample-row">
      <div>
        <b>${escapeHtml(row.home)} vs ${escapeHtml(row.away)}</b>
        <span>${escapeHtml(new Date(row.date).toLocaleDateString())} · actual ${escapeHtml(row.score)}</span>
      </div>
      <div class="sample-probs">
        <span>H ${pct(row.home_win)}</span>
        <span>D ${pct(row.draw)}</span>
        <span>A ${pct(row.away_win)}</span>
      </div>
      <div class="${row.predicted_label === row.outcome ? "sample-hit" : "sample-miss"}">
        ${escapeHtml(row.predicted_label)} · ${pct(row.predicted_prob)}
      </div>
    </div>`).join("")}
  </div>`;
}

function renderEvaluation(data) {
  const [baseline, tuned, ensemble] = data.holdout || [];
  const diff = baseline?.log_loss != null && tuned?.log_loss != null
    ? baseline.log_loss - tuned.log_loss
    : null;
  const ensembleDiff = baseline?.log_loss != null && ensemble?.log_loss != null
    ? baseline.log_loss - ensemble.log_loss
    : null;
  const improved = data.recommendation === "candidate_improved" || data.recommendation === "ensemble_candidate_improved";
  $("lab-results").innerHTML = `
    <div class="lab-summary">
      <div>
        <h3>${escapeHtml(data.league?.name || "Model evaluation")}</h3>
        <p>${escapeHtml(data.date_range?.start || "—")} → ${escapeHtml(data.date_range?.end || "—")} · ${escapeHtml(String(data.match_count || 0))} completed matches</p>
      </div>
      <span class="badge ${improved ? "HIGH" : "MEDIUM"}">${escapeHtml(data.recommendation || "checked")}</span>
    </div>
    <div class="model-bar">
      <div><b>Model</b>${escapeHtml(data.model_class || data.model || "Dixon-Coles")}</div>
      <div><b>penaltyblog</b>${escapeHtml(data.penaltyblog_version || "—")}</div>
      <div><b>Tune / holdout</b>${escapeHtml(String(data.config?.tune_matches || "—"))} / ${escapeHtml(String(data.config?.holdout_matches || "—"))}</div>
      <div><b>Selected ξ</b>${escapeHtml(String(data.selected_xi ?? "—"))}</div>
      <div><b>Latest result included</b>${escapeHtml(data.latest_completed_match_included || data.date_range?.end || "—")}</div>
    </div>
    <div class="lab-callout">
      ${escapeHtml(data.recommendation_note || "Use this as evidence before changing production forecasts.")}
      ${diff != null ? ` Log-loss delta: ${escapeHtml(diff.toFixed(4))}.` : ""}
      ${ensembleDiff != null ? ` Ensemble delta: ${escapeHtml(ensembleDiff.toFixed(4))}.` : ""}
      ${data.freshness_note ? ` ${escapeHtml(data.freshness_note)}` : ""}
    </div>
    <div class="lab-three">
      ${EvaluationMetrics("Current baseline", baseline || {})}
      ${EvaluationMetrics("Tuned decay candidate", tuned || {})}
      ${EvaluationMetrics("DC + Elo ensemble", ensemble || data.ensemble || {})}
    </div>
    <div class="lab-three">
      <article class="lab-metric-card">
        <div class="combo-title">Baseline calibration</div>
        ${CalibrationTable(data.calibration?.baseline || [])}
      </article>
      <article class="lab-metric-card">
        <div class="combo-title">Tuned calibration</div>
        ${CalibrationTable(data.calibration?.tuned || [])}
      </article>
      <article class="lab-metric-card">
        <div class="combo-title">Ensemble calibration</div>
        ${CalibrationTable(data.calibration?.ensemble || [])}
      </article>
    </div>
    <article class="lab-metric-card">
      <div class="combo-title">Latest evaluated holdout examples</div>
      <p class="explain">These are the most recent matches inside the completed-result data used for this backtest, not a live final-score feed.</p>
      ${SamplePredictionRows(data.sample_predictions || [])}
    </article>
    <article class="lab-metric-card">
      <div class="combo-title">Model extension status</div>
      <p class="explain">${escapeHtml(data.extension_status?.recent_form || "")}</p>
      <p class="explain">${escapeHtml(data.extension_status?.lineups || "")}</p>
      <p class="explain">${escapeHtml(data.extension_status?.odds_value || "")}</p>
    </article>
  `;
}

async function loadModelLab({ refresh = false } = {}) {
  const token = ++labToken;
  const leagueId = labLeagueSelect.value || activeLeagueId || navData.all[0]?.id || "laliga";
  activeLeagueId = leagueId;
  labLeagueSelect.value = leagueId;
  const cacheKey = `${leagueId}`;
  $("lab-status").textContent = "Running walk-forward evaluation…";
  $("lab-results").innerHTML = `<div class="skel"></div><div class="skel"></div>`;
  try {
    let data = !refresh ? labCache.get(cacheKey) : null;
    if (!data) {
      const query = new URLSearchParams();
      if (refresh) query.set("refresh", "true");
      const suffix = query.toString() ? `?${query}` : "";
      const response = await fetch(`/api/evaluation/${encodeURIComponent(leagueId)}${suffix}`);
      const body = await response.json();
      if (!response.ok) throw new Error(formatApiError(body.detail) || "Could not evaluate model");
      data = body;
      labCache.set(cacheKey, data);
    }
    if (token !== labToken) return;
    renderEvaluation(data);
    $("lab-status").textContent = `${data.league.name} · generated ${when(data.generated_at)}`;
    writeParams({ view: "lab", league: leagueId, date: "", fixture_id: "", home: "", away: "", kickoff: "" });
    paintNav();
  } catch (error) {
    if (token !== labToken) return;
    $("lab-status").innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
    $("lab-results").innerHTML = `<p class="panel-empty live-error">${escapeHtml(error.message)}</p>`;
  }
}

function headerName(side, data, match) {
  if (
    match
    && (teamsEqual(match[side], data[side]) || teamsEqual(match[`${side}_source`], data[side]))
  ) {
    return match[`${side}_short`] || data[side];
  }
  return data[side];
}

function MatchHeader(data, match) {
  const kick = match?.kickoff || data.kickoff;
  const homeName = headerName("home", data, match);
  const awayName = headerName("away", data, match);
  const liveLine = data.fixture_status === "live"
    ? `<p class="kick-line" style="color:var(--danger)">LIVE ${escapeHtml(data.fixture_minute || "")} · ${escapeHtml(data.fixture_score || "")}</p>`
    : `<p class="kick-line">${escapeHtml(kick ? when(kick) : "")}</p>`;
  return `<div class="match-header">
    <div class="team-block home">
      ${crest(data.home_logo || match?.home_logo, homeName, "lg")}
      <h2 class="team-name">${escapeHtml(homeName)}</h2>
    </div>
    <div class="vs-col">
      <div class="vs">VS</div>
      ${liveLine}
      <p class="league-line">${escapeHtml(data.league)} · ${escapeHtml(data.season)}</p>
      <div class="ai-chip">AI model prediction</div>
    </div>
    <div class="team-block away">
      ${crest(data.away_logo || match?.away_logo, awayName, "lg")}
      <h2 class="team-name">${escapeHtml(awayName)}</h2>
    </div>
  </div>`;
}

function ProbabilityBar(data) {
  const top = data.predicted_result;
  return `<div class="prob-bar">
      <span style="width:${data.home_win * 100}%; background:var(--home)"></span>
      <span style="width:${data.draw * 100}%; background:var(--draw)"></span>
      <span style="width:${data.away_win * 100}%; background:var(--away)"></span>
    </div>
    <div class="prob-grid">
      <div class="prob-card${top === "home" ? " is-top" : ""}"><small>Home</small><b style="color:var(--home)">${pct(data.home_win)}</b></div>
      <div class="prob-card${top === "draw" ? " is-top" : ""}"><small>Draw</small><b style="color:var(--draw)">${pct(data.draw)}</b></div>
      <div class="prob-card${top === "away" ? " is-top" : ""}"><small>Away</small><b style="color:var(--away)">${pct(data.away_win)}</b></div>
    </div>`;
}

function ModelBlend(data) {
  const signals = data.ensemble?.signals || [];
  const dc = data.dixon_coles_1x2 || {};
  const baseDc = data.base_dixon_coles_1x2 || {};
  const elo = data.elo_1x2 || {};
  const lineup = data.lineup_1x2 || {};
  const gridAdj = data.lineup_grid_adjustment || {};
  return `<div class="model-blend">
    <div class="blend-head">
      <b>${escapeHtml(data.headline_model || "Dixon-Coles")}</b>
      <span>${escapeHtml(data.ensemble?.status || "baseline")}</span>
    </div>
    <div class="blend-signals">
      ${signals.map((signal) =>
        `<span>${escapeHtml(signal.label)} ${Math.round((signal.weight || 0) * 100)}%</span>`
      ).join("")}
    </div>
    <div class="blend-compare">
      <span>${escapeHtml(data.score_grid_model || "DC grid")} ${pct(dc.home_win || 0)} / ${pct(dc.draw || 0)} / ${pct(dc.away_win || 0)}</span>
      ${gridAdj.applied ? `<span>Base DC ${pct(baseDc.home_win || 0)} / ${pct(baseDc.draw || 0)} / ${pct(baseDc.away_win || 0)}</span>` : ""}
      <span>Elo ${pct(elo.home_win || 0)} / ${pct(elo.draw || 0)} / ${pct(elo.away_win || 0)}</span>
      ${data.lineup_1x2 ? `<span>Lineup signal ${pct(lineup.home_win || 0)} / ${pct(lineup.draw || 0)} / ${pct(lineup.away_win || 0)}</span>` : ""}
      ${gridAdj.applied ? `<span>Grid tilt H ${Number(gridAdj.home_goal_multiplier || 1).toFixed(3)} / A ${Number(gridAdj.away_goal_multiplier || 1).toFixed(3)}</span>` : ""}
    </div>
    <p>${escapeHtml(data.ensemble?.note || "Score-grid markets remain Dixon-Coles.")}</p>
    ${gridAdj.applied ? `<p>${escapeHtml(gridAdj.note || "Lineup adjustment applied to the score grid.")}</p>` : ""}
  </div>`;
}

function PredictionHero(data, match) {
  const bestScore = (data.exact_scores || [])[0];
  const color = data.predicted_result === "home" ? "var(--home)" : data.predicted_result === "away" ? "var(--away)" : "var(--draw)";
  const kind = data.forecast_label
    || (data.forecast_kind === "live_fixture" ? "Live match" : "Upcoming fixture forecast");
  const noForecast = data.forecast_available === false;
  const kick = data.kickoff || match?.kickoff;
  let body = "";
  if (noForecast) {
    body = `<p class="explain">${escapeHtml(data.forecast_kind_note || "No saved pre-match forecast for this live fixture.")}</p>`;
  } else {
    body = `<div class="hero-result">
      <p class="hero-kicker">Most likely match result (regulation)</p>
      <p class="hero-label" style="color:${color}">${escapeHtml(data.predicted_result_label || "").toUpperCase()}</p>
      <p class="hero-prob" style="color:${color}">${pct(data.predicted_result_prob || 0)}</p>
      <p class="hero-note">Headline 1X2 blend — not the same as the most likely exact score.</p>
    </div>
    ${ProbabilityBar(data)}
    ${ModelBlend(data)}
    <div class="split-2">
      <div class="stat-box">
        <div class="k">Expected goals (Dixon–Coles λ)</div>
        <div class="v">${Number(data.xg_home).toFixed(2)} — ${Number(data.xg_away).toFixed(2)}</div>
        <div class="s">${escapeHtml(data.home)} / ${escapeHtml(data.away)} · not shot-based xG</div>
      </div>
      <div class="stat-box">
        <div class="k">Most likely exact score</div>
        <div class="v">${bestScore ? `${bestScore.home} — ${bestScore.away}` : "—"}</div>
        <div class="s">${bestScore ? `${pct(bestScore.prob)} of scoreline mass · top 3 below` : ""}</div>
      </div>
    </div>`;
  }
  return `${MatchHeader(data, match)}
    <p class="explain" style="margin-top:8px">${escapeHtml(kind)}${kick ? ` · kickoff ${escapeHtml(when(kick))}` : ""}${data.fixture_id ? ` · ${escapeHtml(data.fixture_id)}` : ""}</p>
    ${data.forecast_kind_note && !noForecast ? `<p class="explain">${escapeHtml(data.forecast_kind_note)}</p>` : ""}
    ${body}`;
}

function SinglePickCard(pick) {
  const fair = pick.fair_odds != null ? `Fair ${Number(pick.fair_odds).toFixed(2)}` : "";
  const value = pick.value_status === "unavailable" ? "Value unavailable" : "";
  return `<article class="pick-card">
    <div class="pick-top">
      <div class="rank">#${pick.rank}</div>
      <span class="badge ${escapeHtml(pick.confidence)}">${escapeHtml(pick.confidence)}</span>
    </div>
    <div class="sel">${escapeHtml(pick.selection)}</div>
    <div class="market">${escapeHtml(pick.market)}</div>
    <div class="prob">${pct(pick.prob)}</div>
    <div class="explain">${escapeHtml(pick.explain || "")}${fair ? ` · ${fair}` : ""}${value ? ` · ${value}` : ""}</div>
  </article>`;
}

function ComboCard(title, combo) {
  if (!combo) return "";
  const risk = combo.risk || (combo.n === 4 ? "HIGH" : combo.n === 3 ? "MEDIUM" : "LOWER");
  const legs = (combo.legs || String(combo.label || "").split(" + ")).filter(Boolean);
  const fair = combo.fair_odds != null ? `Fair ${Number(combo.fair_odds).toFixed(2)}` : "";
  return `<article class="combo-card">
    <div class="combo-top">
      <div class="combo-title">${escapeHtml(title)}</div>
      <span class="badge ${risk === "HIGH" ? "HIGH-RISK" : risk}">${escapeHtml(risk)}</span>
    </div>
    <div class="combo-legs">${legs.map((leg) => `<div>${escapeHtml(leg)}</div>`).join("")}</div>
    <div class="prob">${pct(combo.prob)}</div>
    <div class="explain">${escapeHtml(combo.explain || "Joint probability from intersecting score-grid cells.")}${fair ? ` · ${fair}` : ""}</div>
  </article>`;
}

function fairOdds(prob) {
  const value = Number(prob);
  if (!value || value <= 0) return "—";
  return (1 / value).toFixed(2);
}

function RecPickCard({ label, title, probability, fair, type, note, badge = "MODEL" }) {
  return `<article class="rec-pick-card">
    <div class="rec-pick-top">
      <span class="rec-label">${escapeHtml(label)}</span>
      <span class="badge MEDIUM">${escapeHtml(badge)}</span>
    </div>
    <div class="sel">${escapeHtml(title || "—")}</div>
    <div class="rec-stat-row">
      <div><small>Model probability</small><b>${pct(probability || 0)}</b></div>
      <div><small>Fair odds</small><b>${escapeHtml(fair || fairOdds(probability))}</b></div>
      <div><small>Type</small><b>${escapeHtml(type || "Model pick")}</b></div>
    </div>
    <p class="explain">${escapeHtml(note || "")}</p>
  </article>`;
}

function BettingDecision(data) {
  const decision = data.betting_decision || {};
  const candidates = decision.candidates || [];
  const tone = decision.status === "value_candidate" ? "value" : "no-bet";
  return `<div class="betting-gate ${tone}">
    <div>
      <span class="rec-label">Betting discipline</span>
      <h3>${escapeHtml(decision.grade || "NO BET")}</h3>
      <p>${escapeHtml(decision.summary || "Profit recommendations need real odds and positive expected value.")}</p>
    </div>
    ${candidates.length ? `<div class="bet-candidates">
      ${candidates.map((item) => `<div class="bet-row">
        <b>${escapeHtml(item.selection || "")}</b>
        <span>${pct(item.probability || 0)} · odds ${Number(item.market_odds || 0).toFixed(2)} · EV ${(Number(item.expected_value || 0) * 100).toFixed(1)}%</span>
        <small>Suggested cap: ${(Number(item.suggested_bankroll_percent || 0)).toFixed(2)}% bankroll</small>
      </div>`).join("")}
    </div>` : `<div class="bet-candidates">
      <div class="bet-row"><b>No bet</b><span>${escapeHtml(decision.headline || "No positive edge")}</span><small>Use the probabilities as research, not a profit signal.</small></div>
    </div>`}
  </div>`;
}

function RecommendationTabs(data) {
  const primary = {
    label: "Win prediction",
    title: data.predicted_result_label || "No result pick",
    probability: data.predicted_result_prob || 0,
    type: "1X2 result",
    note: "Most likely regulation result from aggregated home/draw/away score-grid probabilities.",
    badge: "RESULT",
  };
  const singles = (data.single_picks || []).slice(0, 4);
  const valuePicks = (data.views?.best_value?.single_picks || singles).slice(0, 4);
  const combos = [data.combo_2leg, data.combo_3leg].filter(Boolean);
  const hasMarketOdds = valuePicks.some((pick) => pick.market_odds != null);
  const valueCandidates = data.views?.best_value?.value_candidates || [];

  const tabData = [
    {
      id: "likely",
      label: "Most Likely",
      html: `<div class="rec-grid">
        ${RecPickCard(primary)}
        ${singles.map((pick) => RecPickCard({
          label: `#${pick.rank || ""} single`,
          title: pick.selection,
          probability: pick.prob,
          type: pick.market || "Single",
          fair: pick.fair_odds != null ? Number(pick.fair_odds).toFixed(2) : null,
          note: pick.explain || "Derived from the score grid.",
          badge: pick.confidence || "MODEL",
        })).join("")}
      </div>`,
    },
    {
      id: "value",
      label: "Best Value",
      html: hasMarketOdds && valueCandidates.length
        ? `<div class="rec-grid">${valuePicks.map((pick) => RecPickCard({
            label: "Value candidate",
            title: pick.selection,
            probability: pick.prob,
            type: pick.expected_value != null ? `EV ${(pick.expected_value * 100).toFixed(1)}%` : "Value",
            fair: pick.fair_odds != null ? Number(pick.fair_odds).toFixed(2) : null,
            note: `${pick.bookmaker ? `${pick.bookmaker}: ` : ""}market odds ${pick.market_odds}. Best value ranks by expected return, not by safest probability.`,
            badge: "VALUE",
          })).join("")}</div>`
        : hasMarketOdds
          ? `<div class="rec-empty">
              <b>No positive value passed the gate.</b>
              <p>Matched odds exist, but none cleared the minimum +3% expected-value threshold.</p>
            </div>`
        : `<div class="rec-empty">
            <b>Value picks need real odds.</b>
            <p>Fair odds are available from the model, but profit cannot be estimated until bookmaker/provider odds are connected.</p>
            <div class="rec-grid">${valuePicks.map((pick) => RecPickCard({
              label: "Fair price only",
              title: pick.selection,
              probability: pick.prob,
              type: "Value unavailable",
              fair: pick.fair_odds != null ? Number(pick.fair_odds).toFixed(2) : null,
              note: "Only treat this as value if offered market odds are higher than the fair odds shown here.",
              badge: "NO ODDS",
            })).join("")}</div>
          </div>`,
    },
    {
      id: "combos",
      label: "Combos",
      html: combos.length
        ? `<div class="rec-grid">${combos.map((combo) => RecPickCard({
            label: `${combo.n || combo.legs?.length || ""}-leg combo`,
            title: combo.legs?.join(" + ") || combo.label,
            probability: combo.prob,
            type: combo.risk || "Combo",
            fair: combo.fair_odds != null ? Number(combo.fair_odds).toFixed(2) : null,
            note: combo.explain || "Joint probability from score-grid intersection.",
            badge: combo.risk || "COMBO",
          })).join("")}</div>`
        : `<p class="rec-empty">No valid non-redundant combo was available for this fixture.</p>`,
    },
    {
      id: "avoid",
      label: "Avoid",
      html: `<div class="avoid-list">
        <div><b>Do not call fair odds “profit”.</b><p>Profit/value needs offered market odds with a timestamp.</p></div>
        <div><b>Avoid same-game combo multiplication.</b><p>This app uses score-grid intersections because combo legs are correlated.</p></div>
        <div><b>Avoid forcing longshots.</b><p>If a 3-leg or 4-leg does not have a meaningful joint probability, it should not be manufactured.</p></div>
        <div><b>Avoid stale live interpretation.</b><p>Pre-match forecasts should not be presented as in-play predictions.</p></div>
      </div>`,
    },
  ];

  return {
    tabs: tabData.map((tab, index) =>
      `<button type="button" class="rec-tab${index === 0 ? " is-active" : ""}" data-rec-tab="${escapeHtml(tab.id)}" role="tab">${escapeHtml(tab.label)}</button>`
    ).join(""),
    panels: tabData.map((tab, index) =>
      `<div class="rec-panel${index === 0 ? "" : " hidden"}" data-rec-panel="${escapeHtml(tab.id)}">${tab.html}</div>`
    ).join(""),
  };
}

function FreshnessCard(data) {
  const f = data.freshness || {};
  const external = data.external_data || {};
  const externalText = external.configured
    ? `${external.provider || "External"}: ${external.status || "configured"}`
    : `${external.provider || "API-Football"}: not configured`;
  return `<div class="model-bar">
    <div><b>Generated</b>${escapeHtml(f.forecast_generated_at ? when(f.forecast_generated_at) : "—")}</div>
    <div><b>Training cutoff</b>${escapeHtml(f.training_cutoff ? when(f.training_cutoff) : "—")}</div>
    <div><b>Results through</b>${escapeHtml(f.results_current_through || "—")}</div>
    <div><b>Data version</b><span class="s">${escapeHtml(f.data_version || "—")}</span></div>
    <div><b>External data</b><span class="s">${escapeHtml(externalText)}</span></div>
  </div>`;
}

function PlayerPredictionCard(row, label) {
  return `<article class="player-card">
    <div>
      <b>${escapeHtml(row.player || "—")}</b>
      <span>${escapeHtml(row.team || "")}</span>
    </div>
    <strong>${pct(row.chance || 0)}</strong>
    <p>${escapeHtml(label)} · ${escapeHtml(String(row.goals ?? 0))} G · ${escapeHtml(String(row.assists ?? 0))} A · ${Number(row.xg || 0).toFixed(1)} xG · ${Number(row.xa || 0).toFixed(1)} xA</p>
  </article>`;
}

function PlayerContributionCard(row) {
  return `<article class="player-card">
    <div>
      <b>${escapeHtml(row.player || "—")}</b>
      <span>${escapeHtml(row.team || "")}</span>
    </div>
    <strong>${Number(row.attack_signal || 0).toFixed(1)}</strong>
    <p>Context only · ${escapeHtml(String(row.minutes ?? 0))} min · ${escapeHtml(String(row.goals ?? 0))} G · ${escapeHtml(String(row.assists ?? 0))} A · ${Number(row.xg || 0).toFixed(1)} xG · ${Number(row.xa || 0).toFixed(1)} xA</p>
  </article>`;
}

function PlayerPredictionSection(data, kind) {
  const players = data.players || {};
  const rows = kind === "score"
    ? [...(players.home_scorers || []), ...(players.away_scorers || [])]
    : [...(players.home_assists || []), ...(players.away_assists || [])];
  if (!rows.length) {
    return `<p class="explain">${escapeHtml(data.player_note || "Player data is unavailable for this fixture.")}</p>`;
  }
  return rows.map((row) => PlayerPredictionCard(row, kind === "score" ? "Anytime scorer" : "Assist")).join("");
}

function ImpactLayer(data) {
  const impact = data.impact_layer || {};
  const playerForm = impact.player_form || {};
  const injuries = impact.injuries || {};
  const lineups = impact.lineups || {};
  const contributors = [
    ...((playerForm.home || {}).contributors || []),
    ...((playerForm.away || {}).contributors || []),
  ];
  const injuryRows = (injuries.players || []).slice(0, 8);
  const lineupRows = (lineups.teams || []);
  return `<div class="impact-stack">
    <div class="stat-box">
      <div class="k">Impact data status</div>
      <div class="v">${impact.included_in_model ? "Included in model" : "Context only"}</div>
      <div class="s">${escapeHtml(impact.summary || "No player-impact layer available.")}</div>
    </div>
    <div class="impact-grid">
      <div class="stat-box">
        <div class="k">Lineups</div>
        <div class="v">${lineups.available ? "Available" : "Unavailable"}</div>
        <div class="s">${escapeHtml(lineups.available ? `${lineups.source || "Provider"} lineup context${lineups.included_in_model ? " included in the score grid." : ". Not numerically included yet."}` : "No confirmed/expected lineup feed is available for this fixture.")}</div>
        ${lineupRows.map((team) => `<p class="mini-list"><b>${escapeHtml(team.team || "")}</b> ${escapeHtml(team.formation || "")} · ${(team.players || []).slice(0, 4).map(escapeHtml).join(", ")}${(team.players || []).length > 4 ? "…" : ""}</p>`).join("")}
      </div>
      <div class="stat-box">
        <div class="k">Injuries / suspensions</div>
        <div class="v">${injuries.available ? `${injuryRows.length} listed` : "Unavailable"}</div>
        <div class="s">${escapeHtml(injuries.available ? `${injuries.source || "Provider"} availability context${injuries.included_in_model ? " included in the score grid." : " only; no numeric penalty applied."}` : "No structured injury/suspension feed is available for this fixture.")}</div>
        ${injuryRows.map((row) => `<p class="mini-list"><b>${escapeHtml(row.player || "")}</b> ${escapeHtml(row.team || "")} · ${escapeHtml(row.reason || row.type || "listed")}${row.expected_return ? ` · ${escapeHtml(row.expected_return)}` : ""}</p>`).join("")}
      </div>
    </div>
    <div class="stat-box">
      <div class="k">Player attacking contribution</div>
      <div class="v">${playerForm.available ? "Available" : "Unavailable"}</div>
      <div class="s">${escapeHtml(playerForm.note || "Player club/form data could not be mapped for this fixture.")}</div>
      ${contributors.length ? `<div class="players compact">${contributors.slice(0, 8).map(PlayerContributionCard).join("")}</div>` : ""}
    </div>
  </div>`;
}

function render(data, match) {
  const hidePicks = data.forecast_available === false;
  results.classList.remove("hidden");
  $("freshness").innerHTML = FreshnessCard(data);
  $("hero").innerHTML = PredictionHero(data, match);
  $("recommendations-card").classList.toggle("hidden", hidePicks);
  $("picks-card").classList.toggle("hidden", hidePicks);
  $("combos-card").classList.toggle("hidden", hidePicks);
  $("scores-card").classList.toggle("hidden", hidePicks);
  if (!hidePicks) {
    const rec = RecommendationTabs(data);
    $("rec-tabs").innerHTML = rec.tabs;
    $("betting-decision").innerHTML = BettingDecision(data);
    $("recommendations").innerHTML = rec.panels;
    const valueView = data.views?.best_value || {};
    $("rec-note").textContent = valueView.status === "available"
      ? `Best Value uses ${valueView.source || "matched market"} odds. It is separate from most-likely probability ranking.`
      : `Most likely ranks by model probability. Best value is unavailable: ${valueView.reason || "no matched market odds."}`;
    $("singles").innerHTML = (data.single_picks || []).map(SinglePickCard).join("") || `<p class="explain">No singles.</p>`;
    $("combo-note").textContent = data.combo_note || "";
    $("combos").innerHTML = [
      ComboCard("2-leg combo", data.combo_2leg),
      ComboCard("3-leg combo", data.combo_3leg),
    ].join("");
    $("scores").innerHTML = `<div class="score-list">${(data.exact_scores || []).map((row) =>
      `<div class="score-row"><b>${row.home} — ${row.away}</b><span>${pct(row.prob)}</span></div>`
    ).join("")}</div>`;
  }
  const form = data.recent_form || {};
  $("form").innerHTML = ["home", "away"].map((side) => {
    const block = form[side] || {};
    return `<div class="stat-box"><div class="k">${escapeHtml(data[side])} recent</div>
      <div class="v">${escapeHtml(block.summary || block.record || "—")}</div>
      <div class="s">${escapeHtml(block.note || "Form cards are context only; not separate model weights.")}</div></div>`;
  }).join("");
  $("scorers").innerHTML = PlayerPredictionSection(data, "score");
  $("assists").innerHTML = PlayerPredictionSection(data, "assist");
  $("why").innerHTML = `${ImpactLayer(data)}
    <div class="stat-box"><div class="k">Model</div><div class="v">${escapeHtml((data.inputs || []).join(" · "))}</div>
    <div class="s">Not in model: ${(data.inputs_not_in_model || []).map(escapeHtml).join("; ")}</div></div>`;
  const settlement = data.settlement;
  if (settlement && data.forecast_kind === "completed_fixture") {
    $("settlement-card").classList.remove("hidden");
    $("settlement").innerHTML = `<div class="stat-box"><div class="k">Actual</div>
      <div class="v">${escapeHtml(String(settlement.actual_home ?? settlement.goals_home ?? ""))} — ${escapeHtml(String(settlement.actual_away ?? settlement.goals_away ?? ""))}</div>
      <div class="s">${escapeHtml(settlement.summary || settlement.note || "Tracked fixture settlement.")}</div></div>`;
  } else {
    $("settlement-card").classList.add("hidden");
    $("settlement").innerHTML = "";
  }
  $("model-info").innerHTML = `<div class="model-bar">
    <div><b>penaltyblog</b>${escapeHtml(data.penaltyblog_version || data.model_name || "DixonColesGoalModel")}</div>
    <div><b>ξ</b>${escapeHtml(String(data.xi ?? "0.0018"))}</div>
    <div><b>Trained on</b>${escapeHtml(String(data.trained_on ?? "—"))} matches</div>
  </div>`;
  paintLive();
  paintBrowse();
}

function skeleton() {
  results.classList.remove("hidden");
  $("hero").innerHTML = `<div class="skel"></div>`;
  $("singles").innerHTML = `<div class="skel"></div>`;
}

async function ensureTeams(leagueId) {
  const query = new URLSearchParams({ tz: localTz });
  const data = await fetch(`/api/leagues/${leagueId}/teams?${query}`).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(formatApiError(body.detail) || "Could not load teams");
    return body;
  });
  setOptions(homeSelect, data.teams, data.teams[0]);
  setOptions(awaySelect, data.teams, data.teams[1] || data.teams[0]);
  leagueSelect.value = leagueId;
  return data;
}

async function runPredict(match) {
  const token = ++predictToken;
  const leagueId = match?.league_id || leagueSelect.value;
  const homeName = match?.home || homeSelect.value;
  const awayName = match?.away || awaySelect.value;
  if (!homeName || !awayName) {
    statusEl.innerHTML = `<span class="error">Pick two teams to predict.</span>`;
    return;
  }
  // Update custom selects lazily — do not block prediction on /teams catalog load.
  homeSelect.value = homeName;
  awaySelect.value = awayName;
  if ([...leagueSelect.options].some((opt) => opt.value === leagueId)) {
    leagueSelect.value = leagueId;
  }
  const key = `${leagueId}|${match?.fixture_id || ""}|${homeName}|${awayName}|${match?.kickoff || ""}`;
  writeParams({
    view,
    league: view === "league" ? activeLeagueId : (params().get("league") || ""),
    date: browseDate,
    fixture_id: match?.fixture_id || "",
    home: homeName,
    away: awayName,
    kickoff: match?.kickoff || "",
  });
  if (cache.has(key)) {
    if (token !== predictToken) return;
    render(cache.get(key), match);
    statusEl.textContent = `${cache.get(key).league} ${cache.get(key).season}`;
    return;
  }
  skeleton();
  statusEl.textContent = "Scoring match…";
  $("predict").disabled = true;
  predictAbort?.abort();
  predictAbort = new AbortController();
  try {
    const response = await fetch("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: predictAbort.signal,
      body: JSON.stringify({
        league_id: leagueId,
        home: homeName,
        away: awayName,
        fixture_id: match?.fixture_id || null,
        kickoff: match?.kickoff || null,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(formatApiError(data.detail) || "Prediction failed");
    cache.set(key, data);
    if (token !== predictToken) return;
    render(data, match);
    statusEl.textContent = `${data.league} ${data.season}`;
    // Fill team dropdowns in background for custom match UI.
    ensureTeams(leagueId).catch(() => {});
  } catch (error) {
    if (error.name === "AbortError") return;
    if (token !== predictToken) return;
    statusEl.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
  } finally {
    if (token === predictToken) $("predict").disabled = false;
  }
}

async function selectFixture(match, { predict = true, fromUser = false } = {}) {
  if (!match) return;
  selectedMatch = match;
  selectedKey = fixtureKey(match);
  paintLive();
  paintBrowse();
  if (!predict) return;
  if (fromUser) results.scrollIntoView({ behavior: "smooth", block: "start" });
  await runPredict(match);
}

async function loadLiveBoard({ silent = false } = {}) {
  if (document.hidden) return;
  const liveBox = $("live-now");
  const prevScroll = liveBox.scrollTop;
  try {
    if (!silent && !(livePayload.live || []).length) {
      liveBox.innerHTML = `<p class="live-empty">Loading live scores…</p>`;
    }
    const query = new URLSearchParams({ tz: localTz });
    const data = await fetch(`/api/live?${query}`).then(async (r) => {
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || "Could not load live games");
      return body;
    });
    livePayload = data;
    paintLive();
    liveBox.scrollTop = prevScroll;
  } catch (error) {
    livePayload = {
      live: [],
      retrieval: "error",
      message: error.message || "Live scores could not be retrieved.",
      as_of: null,
    };
    paintLive();
  }
}

async function loadBrowse() {
  const token = ++browseToken;
  statusEl.textContent = "Loading fixtures…";
  // Keep prior panels visible while loading so clicks don't blank the screen.
  const panels = $("competition-panels");
  if (!panels.querySelector(".comp-panel, .comp-empty, .date-empty")) {
    panels.innerHTML = `<div class="skel"></div><div class="skel"></div>`;
  } else {
    panels.classList.add("is-loading");
  }
  const query = new URLSearchParams({ tz: localTz, date: browseDate });
  try {
    let data;
    if (view === "league" && activeLeagueId) {
      data = await fetch(`/api/browse/${activeLeagueId}?${query}`).then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(formatApiError(body.detail) || "Could not load fixtures");
        return body;
      });
    } else {
      // Progressive Home: paint main leagues first, then fill Other competitions.
      const mainQuery = new URLSearchParams(query);
      mainQuery.set("scope", "main");
      data = await fetch(`/api/home?${mainQuery}`).then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(formatApiError(body.detail) || "Could not load fixtures");
        return body;
      });
      if (token !== browseToken) return;
      browsePayload = data;
      if (data.live) livePayload = data.live;
      paintLive();
      paintBrowse();
      statusEl.textContent = `Browsing ${browseDate} · loading more competitions…`;
      const otherQuery = new URLSearchParams(query);
      otherQuery.set("scope", "other");
      const other = await fetch(`/api/home?${otherQuery}`).then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(formatApiError(body.detail) || "Could not load fixtures");
        return body;
      });
      if (token !== browseToken) return;
      data = {
        ...other,
        competitions: [...(data.competitions || []), ...(other.competitions || [])],
        empty: !([...(data.competitions || []), ...(other.competitions || [])].some((p) => (p.fixtures || []).length)),
        live: data.live || other.live,
      };
    }
    if (token !== browseToken) return;
    browsePayload = data;
    if (data.live) livePayload = data.live;
    paintLive();
    paintBrowse();
    $("competition-panels").classList.remove("is-loading");
    statusEl.textContent = view === "home"
      ? `Browsing ${browseDate} · models fit only when you open a fixture.`
      : `${data.competitions?.[0]?.name || "League"} · ${browseDate}`;
    const wantedId = params().get("fixture_id");
    const wantedHome = params().get("home");
    const wantedAway = params().get("away");
    if (wantedId || (wantedHome && wantedAway)) {
      const pool = [
        ...(livePayload.live || []),
        ...((data.competitions || []).flatMap((panel) => panel.fixtures || [])),
      ];
      const hit = pool.find((item) =>
        (wantedId && item.fixture_id === wantedId)
        || (wantedHome && wantedAway && teamsEqual(item.home, wantedHome) && teamsEqual(item.away, wantedAway))
      );
      if (hit) await selectFixture(hit, { predict: true, fromUser: false });
    } else {
      results.classList.add("hidden");
      selectedKey = "";
      selectedMatch = null;
    }
  } catch (error) {
    if (token !== browseToken) return;
    statusEl.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
    $("competition-panels").innerHTML = `<p class="panel-empty live-error">${escapeHtml(error.message)}</p>`;
  } finally {
    if (token === browseToken) $("competition-panels").classList.remove("is-loading");
  }
}

function rememberScroll() {
  scrollMemory[view === "home" ? "home" : view === "lab" ? "lab" : "league"] = window.scrollY;
}

function restoreScroll() {
  const y = scrollMemory[view === "home" ? "home" : view === "lab" ? "lab" : "league"] || 0;
  requestAnimationFrame(() => window.scrollTo(0, y));
}

async function navigate({ nextView, leagueId, date, push = true } = {}) {
  rememberScroll();
  otherOpen = false;
  view = nextView || view;
  if (leagueId != null) activeLeagueId = leagueId;
  if (date) browseDate = date;
  writeParams({
    view,
    league: view === "league" || view === "lab" ? activeLeagueId : "",
    date: browseDate,
    fixture_id: "",
    home: "",
    away: "",
    kickoff: "",
  }, { push });
  paintNav();
  showActiveView();
  results.classList.add("hidden");
  selectedKey = "";
  selectedMatch = null;
  if (view === "lab") {
    await loadModelLab();
    restoreScroll();
    return;
  }
  // Keep prediction cache across browse navigation; only clear on boot.
  statusEl.textContent = "Loading fixtures…";
  $("competition-panels").innerHTML = `<div class="skel"></div><div class="skel"></div>`;
  await loadBrowse();
  restoreScroll();
}

function startLivePolling() {
  if (liveTimer) return;
  liveTimer = window.setInterval(() => {
    if (document.hidden) return;
    loadLiveBoard({ silent: true });
  }, LIVE_POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadLiveBoard({ silent: true });
  });
}

function bindEvents() {
  const onNavClick = (event) => {
    const otherBtn = event.target.closest("#other-toggle");
    if (otherBtn) {
      event.preventDefault();
      event.stopPropagation();
      otherOpen = !otherOpen;
      paintNav();
      return;
    }
    const btn = event.target.closest("[data-nav]");
    if (!btn) return;
    const kind = btn.dataset.nav;
    if (kind === "home") navigate({ nextView: "home", leagueId: "", push: true });
    if (kind === "lab") navigate({ nextView: "lab", leagueId: activeLeagueId || labLeagueSelect.value || navData.all[0]?.id, push: true });
    if (kind === "league") navigate({ nextView: "league", leagueId: btn.dataset.id, push: true });
  };
  $("top-nav").addEventListener("click", onNavClick);
  $("nav-other").addEventListener("click", onNavClick);
  document.body.addEventListener("click", (event) => {
    if (event.target.closest("#other-menu [data-nav]")) {
      onNavClick(event);
    }
  });

  document.addEventListener("click", (event) => {
    if (
      otherOpen
      && !event.target.closest(".nav-other")
      && !event.target.closest("#other-menu")
    ) {
      otherOpen = false;
      paintNav();
    }
  });
  window.addEventListener("resize", () => {
    if (otherOpen) paintNav();
  });
  window.addEventListener("scroll", () => {
    if (otherOpen) {
      otherOpen = false;
      paintNav();
    }
  }, true);

  $("competition-panels").addEventListener("click", (event) => {
    const jump = event.target.closest("#jump-next-date");
    if (jump) {
      navigate({ date: jump.dataset.date, push: true });
      return;
    }
    const viewLink = event.target.closest("[data-view-league]");
    if (viewLink) {
      event.preventDefault();
      navigate({ nextView: "league", leagueId: viewLink.dataset.viewLeague, push: true });
      return;
    }
    const row = event.target.closest(".fx-row");
    if (!row) return;
    const pool = [
      ...(livePayload.live || []),
      ...((browsePayload?.competitions || []).flatMap((panel) => panel.fixtures || [])),
    ];
    const match = pool.find((item) => fixtureKey(item) === row.dataset.key);
    if (match) selectFixture(match, { predict: true, fromUser: true });
  });

  $("live-now").addEventListener("click", (event) => {
    const row = event.target.closest(".fx-row");
    if (!row) return;
    const match = (livePayload.live || []).find((item) => fixtureKey(item) === row.dataset.key);
    if (match) selectFixture(match, { predict: true, fromUser: true });
  });

  results.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-rec-tab]");
    if (!tab) return;
    const id = tab.dataset.recTab;
    results.querySelectorAll("[data-rec-tab]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.recTab === id);
    });
    results.querySelectorAll("[data-rec-panel]").forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.recPanel !== id);
    });
  });

  $("date-prev").addEventListener("click", () => {
    const d = new Date(browseDate + "T12:00:00");
    d.setDate(d.getDate() - 1);
    navigate({ date: d.toISOString().slice(0, 10), push: true });
  });
  $("date-next").addEventListener("click", () => {
    const d = new Date(browseDate + "T12:00:00");
    d.setDate(d.getDate() + 1);
    navigate({ date: d.toISOString().slice(0, 10), push: true });
  });
  $("date-today").addEventListener("click", () => navigate({ date: todayISO(0), push: true }));
  $("date-tomorrow").addEventListener("click", () => navigate({ date: todayISO(1), push: true }));
  $("date-picker").addEventListener("change", (event) => {
    if (event.target.value) navigate({ date: event.target.value, push: true });
  });

  $("predict").addEventListener("click", () => {
    selectFixture({
      league_id: leagueSelect.value,
      home: homeSelect.value,
      away: awaySelect.value,
    }, { predict: true, fromUser: true });
  });

  labLeagueSelect.addEventListener("change", () => {
    navigate({ nextView: "lab", leagueId: labLeagueSelect.value, push: true });
  });
  $("run-evaluation").addEventListener("click", () => {
    labCache.delete(labLeagueSelect.value);
    loadModelLab({ refresh: true });
  });

  window.addEventListener("popstate", () => {
    bootFromUrl({ push: false });
  });
}

async function bootFromUrl({ push = false } = {}) {
  const q = params();
  view = q.get("view") === "league" && q.get("league") ? "league" : q.get("view") === "lab" ? "lab" : "home";
  activeLeagueId = view === "league" || view === "lab" ? (q.get("league") || activeLeagueId || navData.all[0]?.id || "") : "";
  browseDate = q.get("date") || todayISO(0);
  paintNav();
  showActiveView();
  writeParams({
    view,
    league: activeLeagueId,
    date: view === "lab" ? "" : browseDate,
    fixture_id: q.get("fixture_id") || "",
    home: q.get("home") || "",
    away: q.get("away") || "",
    kickoff: q.get("kickoff") || "",
  }, { push });
  if (view === "lab") {
    await loadModelLab();
    return;
  }
  // Paint fixtures first; live board refresh afterward so it does not starve FotMob.
  await loadBrowse();
  void loadLiveBoard();
}

async function boot() {
  bindEvents();
  navData = await fetch("/api/nav").then((r) => r.json());
  paintNav();
  startLivePolling();
  await bootFromUrl({ push: false });
}

boot().catch((error) => {
  statusEl.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
});
