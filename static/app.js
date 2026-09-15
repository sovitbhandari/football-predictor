const $ = (id) => document.getElementById(id);
const pct = (value) => `${(value * 100).toFixed(1)}%`;
const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
const cache = new Map();

const league = $("league");
const home = $("home");
const away = $("away");
const status = $("status");
const results = $("results");
const custom = $("custom");

let leagueMeta = {};
let carousel = [];
let liveMatches = [];
let selectedKey = "";
let paintedLeague = "";
let predictToken = 0;
let teamsToken = 0;
let predictAbort = null;
let liveTimer = 0;

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
  if (mode === "card") {
    const today = new Date();
    const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    const startThat = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const diff = Math.round((startThat - startToday) / 86400000);
    const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    if (diff === 0) return `Today · ${time}`;
    if (diff === 1) return `Tomorrow · ${time}`;
    return `${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })} · ${time}`;
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
  return `${match.league_id || ""}|${match.home}|${match.away}|${match.kickoff || ""}`;
}

function sameTeams(match, homeName, awayName) {
  if (!match || !homeName || !awayName) return false;
  return (
    (teamsEqual(match.home, homeName) && teamsEqual(match.away, awayName))
    || (teamsEqual(match.home_source, homeName) && teamsEqual(match.away_source, awayName))
  );
}

function params() {
  return new URLSearchParams(window.location.search);
}

function writeParams(next) {
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(next)) {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  }
  history.replaceState({}, "", url);
}

function setOptions(select, values, selected) {
  select.innerHTML = values.map((value) => {
    const mark = value === selected ? " selected" : "";
    return `<option value="${escapeHtml(value)}"${mark}>${escapeHtml(value)}</option>`;
  }).join("");
}

function cardState(match) {
  if (match.status === "live") return "Live";
  const date = match.kickoff ? new Date(match.kickoff) : null;
  if (!date) return "Upcoming";
  const today = new Date();
  const diff = Math.round(
    (new Date(date.getFullYear(), date.getMonth(), date.getDate()) -
      new Date(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000
  );
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  return "Upcoming";
}

function FixtureCard(match, { showLeague = false } = {}) {
  const active = fixtureKey(match) === selectedKey ? " is-active" : "";
  const live = match.status === "live" ? " is-live" : "";
  const leagueLine = showLeague && match.league_name
    ? `<div class="fx-league">${escapeHtml(match.league_name)}</div>`
    : "";
  const meta = match.status === "live"
    ? `Live ${match.minute || ""} ${match.score || ""}`.trim()
    : when(match.kickoff, "card");
  return `<button type="button" class="fx-card${active}${live}" data-key="${escapeHtml(fixtureKey(match))}" data-league="${escapeHtml(match.league_id || "")}">
    ${leagueLine}
    <div class="fx-crest-row">
      ${crest(match.home_logo, match.home)}
      ${crest(match.away_logo, match.away)}
    </div>
    <div class="fx-teams">
      ${escapeHtml(match.home_short || match.home)}
      <small>vs</small>
      ${escapeHtml(match.away_short || match.away)}
    </div>
    <div class="fx-meta">${escapeHtml(meta)}</div>
  </button>`;
}

function FixtureCarousel(matches) {
  if (!matches.length) return `<p class="status">No upcoming fixtures found.</p>`;
  return `<div class="carousel">${matches.map((match) => FixtureCard(match)).join("")}</div>`;
}

function LiveBoard(matches) {
  if (!matches.length) {
    return `<p class="live-empty">No live games in these leagues right now.</p>`;
  }
  return `<div class="live-board">${matches.map((match) => FixtureCard(match, { showLeague: true })).join("")}</div>`;
}

function MatchHeader(data, match) {
  const kick = match?.kickoff ? when(match.kickoff) : "";
  const homeName = headerName("home", data, match);
  const awayName = headerName("away", data, match);
  return `<div class="match-header">
    <div class="team-block home">
      ${crest(data.home_logo || match?.home_logo, homeName, "lg")}
      <h2 class="team-name">${escapeHtml(homeName)}</h2>
    </div>
    <div class="vs-col">
      <div class="vs">VS</div>
      <p class="kick-line">${escapeHtml(kick)}</p>
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

function PredictionHero(data, match) {
  const bestScore = data.exact_scores[0];
  const color = data.predicted_result === "home" ? "var(--home)" : data.predicted_result === "away" ? "var(--away)" : "var(--draw)";
  const kind = data.forecast_kind === "hypothetical_matchup"
    ? "Hypothetical matchup"
    : data.forecast_kind === "completed_fixture"
      ? "Completed fixture"
      : "Upcoming fixture forecast";
  const kick = data.kickoff || match?.kickoff;
  return `${MatchHeader(data, match)}
    <p class="explain" style="margin-top:8px">${escapeHtml(kind)}${kick ? ` · kickoff ${escapeHtml(when(kick))}` : ""}${data.fixture_id ? ` · ${escapeHtml(data.fixture_id)}` : ""}</p>
    <div class="hero-result">
      <p class="hero-kicker">Most likely match result (regulation)</p>
      <p class="hero-label" style="color:${color}">${escapeHtml(data.predicted_result_label).toUpperCase()}</p>
      <p class="hero-prob" style="color:${color}">${pct(data.predicted_result_prob)}</p>
      <p class="hero-note">Aggregate 1X2 from the score grid — not the same as the most likely exact score.</p>
    </div>
    ${ProbabilityBar(data)}
    <div class="split-2">
      <div class="stat-box">
        <div class="k">Expected goals (Dixon–Coles λ)</div>
        <div class="v">${data.xg_home.toFixed(2)} — ${data.xg_away.toFixed(2)}</div>
        <div class="s">${escapeHtml(data.home)} / ${escapeHtml(data.away)} · not shot-based xG</div>
      </div>
      <div class="stat-box">
        <div class="k">Most likely exact score</div>
        <div class="v">${bestScore.home} — ${bestScore.away}</div>
        <div class="s">${pct(bestScore.prob)} of scoreline mass · top 3 below</div>
      </div>
    </div>`;
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
    ${legs.map((leg) => `<div class="leg"><span>${escapeHtml(leg)}</span><span class="check">✓</span></div>`).join("")}
    <div class="market" style="margin-top:10px">Joint model probability</div>
    <div class="prob">${pct(combo.prob)}</div>
    <div class="explain">${escapeHtml(combo.explain || "Joint probability from scoreline states, not multiplied singles.")}${fair ? ` · ${fair}` : ""} · Value unavailable</div>
  </article>`;
}

function FreshnessBar(data) {
  const f = data.freshness || {};
  const lineup = data.lineup || {};
  const cacheNote = f.from_cache ? "Cached model fit (same data version)" : "Fresh fit for this cutoff";
  const stale = f.stale ? `<div class="s" style="color:#c45c26">Stale-data notice: showing last valid forecast.</div>` : "";
  return `<div class="model-bar">
    <div><b>Forecast generated</b>${escapeHtml(when(f.forecast_generated_at || data.as_of))}</div>
    <div><b>Results through</b>${escapeHtml(f.results_current_through || "—")}</div>
    <div><b>Lineup</b>${escapeHtml(lineup.status || "Unavailable")}<div class="s">${escapeHtml(lineup.note || "")}</div></div>
    <div><b>Cache</b>${escapeHtml(cacheNote)}${stale}</div>
  </div>`;
}

function FormWindow(teamBlock) {
  if (!teamBlock || !teamBlock.windows) {
    return `<div class="stat-box"><div class="k">${escapeHtml(teamBlock?.team || "Team")}</div><div class="s">${escapeHtml(teamBlock?.note || "No form")}</div></div>`;
  }
  const w5 = teamBlock.windows["5"] || {};
  const w10 = teamBlock.windows["10"] || {};
  return `<div class="stat-box">
    <div class="k">${escapeHtml(teamBlock.team)} · recent form</div>
    <div class="v" style="font-size:16px">L5 ${escapeHtml(w5.record || "—")} · L10 ${escapeHtml(w10.record || "—")}</div>
    <div class="s">L5 ${w5.points_per_match != null ? w5.points_per_match.toFixed(2) : "—"} PPG · GF/GA ${w5.goals_for != null ? w5.goals_for.toFixed(2) : "—"}/${w5.goals_against != null ? w5.goals_against.toFixed(2) : "—"} · rest ${teamBlock.rest_days ?? "—"}d</div>
    <div class="s">${escapeHtml(teamBlock.note || "")}</div>
  </div>`;
}

function SettlementBlock(data) {
  const s = data.settlement;
  if (!s) return "";
  const actual = s.actual_score || {};
  const pred = s.predicted_score || {};
  const singles = (s.singles || []).map((row) =>
    `<div class="leg"><span>${escapeHtml(row.selection || "")}</span><span>${row.won === true ? "Won" : row.won === false ? "Lost" : "—"}</span></div>`
  ).join("");
  const combos = (s.combos || []).map((row) =>
    `<div class="leg"><span>${escapeHtml(row.label || "")}</span><span>${row.won === true ? "Won" : row.won === false ? "Lost" : "—"}</span></div>`
  ).join("");
  return `<div class="stat-box">
    <div class="k">Frozen pre-kickoff forecast</div>
    <div class="s">${escapeHtml(when(s.frozen_at))}</div>
    <div class="metric-row" style="margin-top:10px">
      <div><b>${pred.home ?? "—"}–${pred.away ?? "—"}</b><small>Predicted exact (${pct(pred.prob || 0)})</small></div>
      <div><b>${actual.home}–${actual.away}</b><small>Actual regulation</small></div>
      <div><b>${s.exact_score_hit ? "Hit" : "Miss"}</b><small>Exact score</small></div>
      <div><b>${s.result_hit ? "Hit" : "Miss"}</b><small>1X2 (${escapeHtml(s.predicted_result_label || "")})</small></div>
    </div>
    <div style="margin-top:12px">${singles}${combos}</div>
    <div class="s" style="margin-top:8px">${escapeHtml(s.note || "")}</div>
  </div>`;
}

function ModelInfoBar(data) {
  const model = data.model_library
    ? `${data.model || "Dixon–Coles"} · ${data.model_library} ${data.penaltyblog_version || ""}`
    : (data.model || "Dixon–Coles");
  const extras = [
    data.rho != null ? `ρ ${Number(data.rho).toFixed(3)}` : null,
    data.home_advantage != null ? `home adv ${Number(data.home_advantage).toFixed(2)}` : null,
    data.xi != null ? `ξ ${data.xi}` : null,
    data.loglikelihood != null ? `ll ${Number(data.loglikelihood).toFixed(1)}` : null,
  ].filter(Boolean).join(" · ");
  const notIn = (data.inputs_not_in_model || []).join(" · ");
  return `<div class="model-bar">
    <div><b>Model</b>${escapeHtml(model)}${extras ? `<div class="s">${escapeHtml(extras)}</div>` : ""}</div>
    <div><b>In the fit</b>${escapeHtml((data.inputs || []).join(" · "))}</div>
    <div><b>Display only</b>${escapeHtml(notIn || "—")}</div>
    <div><b>Sample</b>${data.trained_on} matches before cutoff · ${data.current_matches} this season</div>
  </div>`;
}

function ExactScoreList(rows) {
  const max = rows[0]?.prob || 1;
  return rows.map((row, index) => `
    <div class="score-row${index === 0 ? " is-top" : ""}">
      <div>${row.home} — ${row.away}</div>
      <div class="score-track"><span style="width:${(row.prob / max) * 100}%"></span></div>
      <div>${pct(row.prob)}</div>
    </div>`).join("");
}

function PlayerPredictionCard(row, kind) {
  const photo = row.photo
    ? `<img class="avatar" src="${escapeHtml(row.photo)}" alt="" onerror="this.outerHTML='<div class=avatar-fallback>${escapeHtml(initials(row.player))}</div>'">`
    : `<div class="avatar-fallback">${escapeHtml(initials(row.player))}</div>`;
  const season = kind === "score"
    ? `${row.goals} G · ${row.xg.toFixed(1)} xG · ${row.minutes} min`
    : `${row.assists} A · ${row.xa.toFixed(1)} xA · ${row.minutes} min`;
  return `<article class="player-card">
    ${photo}
    <div>
      <div class="sel">${escapeHtml(row.player)}</div>
      <div class="market">${escapeHtml(row.team || "")} · ${kind === "score" ? "Anytime scorer" : "Assist"}</div>
      <div class="prob" style="font-size:22px">${pct(row.chance)}</div>
      <div class="explain">${escapeHtml(season)}</div>
    </div>
  </article>`;
}

function TeamStatComparison(form) {
  return `<div class="stat-box">
    <div class="k">${escapeHtml(form.team)} — ${escapeHtml(form.venue)}</div>
    <div class="metric-row">
      <div><b>${form.games}</b><small>Matches</small></div>
      <div><b>${(form.scored || 0).toFixed(2)}</b><small>Goals / match</small></div>
      <div><b>${(form.conceded || 0).toFixed(2)}</b><small>Conceded / match</small></div>
    </div>
  </div>`;
}

function HeadToHeadList(rows) {
  if (!rows.length) {
    return `<div class="h2h"><p class="explain">No head-to-head games in the training sample. H2H is shown for context and is not a separate model weight.</p></div>`;
  }
  return `<div class="h2h">
    <p class="section-title" style="margin-top:8px">Recent H2H</p>
    ${rows.slice(-6).reverse().map((row) => `
      <div class="h2h-row">
        <span>${escapeHtml(row.date)}</span>
        <div>${escapeHtml(row.home)} ${row.goals_home}–${row.goals_away} ${escapeHtml(row.away)}</div>
      </div>`).join("")}
    <p class="explain">Shown for context. The Dixon–Coles fit uses the full result sample, not these rows alone.</p>
  </div>`;
}

function skeleton() {
  results.classList.remove("hidden");
  if ($("freshness")) $("freshness").innerHTML = `<div class="skel" style="min-height:48px"></div>`;
  $("hero").innerHTML = `<div class="skel" style="min-height:240px"></div>`;
  $("singles").innerHTML = `<div class="skel"></div><div class="skel"></div><div class="skel"></div>`;
  $("combos").innerHTML = `<div class="skel"></div><div class="skel"></div>`;
  $("scores").innerHTML = `<div class="skel"></div>`;
  if ($("form")) $("form").innerHTML = `<div class="skel"></div>`;
  $("scorers").innerHTML = `<div class="skel"></div>`;
  $("assists").innerHTML = `<div class="skel"></div>`;
  $("why").innerHTML = `<div class="skel"></div>`;
  if ($("settlement-card")) $("settlement-card").classList.add("hidden");
  $("model-info").innerHTML = `<div class="skel" style="min-height:48px"></div>`;
}

function render(data, match) {
  if ($("freshness")) $("freshness").innerHTML = FreshnessBar(data);
  $("hero").innerHTML = PredictionHero(data, match);
  $("singles").innerHTML = (data.single_picks || []).map(SinglePickCard).join("") || `<p class="explain">No single picks available.</p>`;
  if ($("combo-note")) $("combo-note").textContent = data.combo_note || "Joint probabilities from the score grid. Redundant legs are rejected.";
  $("combos").innerHTML = [
    ComboCard("Best 2-leg", data.combo_2leg),
    ComboCard("Best 3-leg", data.combo_3leg || data.medium_risk),
  ].filter(Boolean).join("") || `<p class="explain">${escapeHtml(data.combo_note || "No valid combos.")}</p>`;
  $("scores").innerHTML = ExactScoreList((data.exact_scores || []).slice(0, 3));
  if ($("form")) {
    const rf = data.recent_form || {};
    $("form").innerHTML = `${FormWindow(rf.home)}${FormWindow(rf.away)}`;
  }
  if (data.player_note) {
    $("scorers").innerHTML = `<p class="explain">${escapeHtml(data.player_note)}</p>`;
    $("assists").innerHTML = "";
  } else if (data.players) {
    const scorers = [...(data.players.home_scorers || []), ...(data.players.away_scorers || [])]
      .sort((a, b) => b.chance - a.chance);
    const assists = [...(data.players.home_assists || []), ...(data.players.away_assists || [])]
      .sort((a, b) => b.chance - a.chance);
    $("scorers").innerHTML = scorers.map((row) => PlayerPredictionCard(row, "score")).join("") || `<p class="explain">Not enough minutes yet.</p>`;
    $("assists").innerHTML = assists.map((row) => PlayerPredictionCard(row, "assist")).join("") || `<p class="explain">Not enough minutes yet.</p>`;
  }
  $("why").innerHTML = `${TeamStatComparison(data.home_form)}${TeamStatComparison(data.away_form)}${HeadToHeadList(data.h2h || [])}`;
  const settleHtml = SettlementBlock(data);
  if ($("settlement-card")) {
    if (settleHtml) {
      $("settlement-card").classList.remove("hidden");
      $("settlement").innerHTML = settleHtml;
    } else {
      $("settlement-card").classList.add("hidden");
      $("settlement").innerHTML = "";
    }
  }
  $("model-info").innerHTML = ModelInfoBar(data);
  results.classList.remove("hidden");
}

function highlightCarousel() {
  if (paintedLeague !== league.value) {
    paintCarousel();
    return;
  }
  document.querySelectorAll(".fx-card").forEach((card) => {
    card.classList.toggle("is-active", card.dataset.key === selectedKey);
  });
}

function paintCarousel() {
  paintedLeague = league.value;
  $("as-of").textContent = leagueMeta.as_of ? `As of ${when(leagueMeta.as_of)}` : "";
  $("fixtures").innerHTML = FixtureCarousel(carousel);
  highlightCarousel();
}

function paintLiveBoard(asOf) {
  const count = liveMatches.length;
  $("live-as-of").innerHTML = count
    ? `<span class="live-count"><span class="live-dot"></span>${count} live · ${escapeHtml(asOf ? when(asOf) : "now")}</span>`
    : (asOf ? `As of ${when(asOf)}` : "");
  $("live-now").innerHTML = LiveBoard(liveMatches);
  highlightCarousel();
}

function findInCarousel(homeName, awayName) {
  return carousel.find((item) => sameTeams(item, homeName, awayName));
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

function catalogName(...names) {
  return catalogNameFrom([...home.options].map((opt) => opt.value), ...names);
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

async function selectFixture(match, predictNow, fromUser) {
  if (!match) return;
  selectedKey = fixtureKey(match);
  const homeName = catalogName(match.home, match.home_source);
  const awayName = catalogName(match.away, match.away_source);
  highlightCarousel();
  if (!homeName || !awayName || homeName === awayName) {
    status.innerHTML = `<span class="error">Could not map this fixture onto the model teams.</span>`;
    return;
  }
  home.value = homeName;
  away.value = awayName;
  writeParams({ league: league.value, home: homeName, away: awayName });
  if (predictNow) {
    if (fromUser) results.scrollIntoView({ behavior: "smooth", block: "start" });
    await runPredict(match);
  }
}

function predictBody(match) {
  const body = {
    league_id: league.value,
    home: home.value,
    away: away.value,
  };
  if (match?.fixture_id) body.fixture_id = match.fixture_id;
  if (match?.kickoff) body.kickoff = match.kickoff;
  return body;
}

function prefetchNearby() {
  const index = carousel.findIndex((item) => fixtureKey(item) === selectedKey);
  const nearby = carousel.slice(Math.max(0, index), index + 4);
  nearby.forEach((match) => {
    if (!match || match.predictable === false) return;
    const key = `${league.value}|${match.fixture_id || ""}|${match.home}|${match.away}|${match.kickoff || ""}`;
    if (cache.has(key)) return;
    fetch("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(predictBody(match)),
    }).then(async (response) => {
      const data = await response.json();
      if (response.ok) cache.set(key, data);
    }).catch(() => {});
  });
}

async function runPredict(match) {
  const token = ++predictToken;
  const selected = match || carousel.find((item) => item.home === home.value && item.away === away.value);
  const key = `${league.value}|${selected?.fixture_id || ""}|${home.value}|${away.value}|${selected?.kickoff || ""}`;
  if (cache.has(key)) {
    render(cache.get(key), selected);
    status.textContent = `${leagueMeta.name || ""} ${leagueMeta.season || ""}`;
    prefetchNearby();
    return;
  }
  skeleton();
  status.textContent = "Scoring match…";
  $("predict").disabled = true;
  predictAbort?.abort();
  predictAbort = new AbortController();
  try {
    const response = await fetch("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: predictAbort.signal,
      body: JSON.stringify(predictBody(selected)),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(formatApiError(data.detail) || "Prediction failed");
    cache.set(key, data);
    if (token !== predictToken) return;
    render(data, selected);
    status.textContent = `${data.league} ${data.season}`;
    prefetchNearby();
  } catch (error) {
    if (error.name === "AbortError") return;
    if (token !== predictToken) return;
    status.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
  } finally {
    if (token === predictToken) $("predict").disabled = false;
  }
}

function formatApiError(detail) {
  if (detail == null) return "Request failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  return String(detail);
}
  const token = ++teamsToken;
  status.textContent = "Loading fixtures…";
  home.disabled = away.disabled = true;
  results.classList.add("hidden");
  cache.clear();
  paintedLeague = "";
  predictToken += 1;
  predictAbort?.abort();
  const query = new URLSearchParams({ tz: localTz });
  const data = await fetch(`/api/leagues/${league.value}/teams?${query}`).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(formatApiError(body.detail) || "Could not load teams");
    return body;
  });
  if (token !== teamsToken) return;
  leagueMeta = data;
  carousel = data.carousel && data.carousel.length
    ? data.carousel
    : [...(data.live || []), ...(data.today || []), ...(data.next_matches || []), ...(data.upcoming || [])];
  const wantedHome = params().get("home");
  const wantedAway = params().get("away");
  const focus = findInCarousel(wantedHome, wantedAway)
    || data.focus
    || carousel[0];
  const homePick = catalogNameFrom(data.teams, focus?.home, focus?.home_source, wantedHome) || data.teams[0];
  const awayPick = catalogNameFrom(data.teams, focus?.away, focus?.away_source, wantedAway) || data.teams[1] || data.teams[0];
  setOptions(home, data.teams, homePick);
  setOptions(away, data.teams, awayPick);
  home.disabled = away.disabled = false;
  status.textContent = `${data.name} ${data.season}: trained on ${data.trained_on} matches (${data.current_matches} this season).`;
  paintCarousel();
  if (focus) await selectFixture(focus, true, false);
}

$("fixtures").addEventListener("click", (event) => {
  const card = event.target.closest(".fx-card");
  if (!card) return;
  const match = carousel.find((item) => fixtureKey(item) === card.dataset.key);
  if (match) selectFixture(match, true, true);
});

$("live-now").addEventListener("click", (event) => {
  const card = event.target.closest(".fx-card");
  if (!card) return;
  const match = liveMatches.find((item) => fixtureKey(item) === card.dataset.key);
  if (match) selectLiveMatch(match).catch((error) => {
    status.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
  });
});

async function selectLiveMatch(match) {
  writeParams({ league: match.league_id, home: match.home, away: match.away });
  selectedKey = fixtureKey(match);
  highlightCarousel();
  if (league.value !== match.league_id) {
    league.value = match.league_id;
    await loadTeams();
    return;
  }
  const mapped = findInCarousel(match.home, match.away) || findInCarousel(match.home_source, match.away_source);
  await selectFixture(mapped || match, true, true);
}

async function loadLiveBoard() {
  const query = new URLSearchParams({ tz: localTz });
  const data = await fetch(`/api/live?${query}`).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || "Could not load live games");
    return body;
  });
  liveMatches = data.live || [];
  paintLiveBoard(data.as_of);
}

async function loadLeagues() {
  const data = await fetch("/api/leagues").then((r) => r.json());
  const selected = params().get("league") || data[0]?.id;
  league.innerHTML = data.map((item) =>
    `<option value="${escapeHtml(item.id)}"${item.id === selected ? " selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml(item.country)} · ${escapeHtml(item.season)}</option>`
  ).join("");
  loadLiveBoard().catch(() => {
    $("live-now").innerHTML = `<p class="live-empty">Live scores are unavailable right now.</p>`;
  });
  if (!liveTimer) liveTimer = window.setInterval(() => {
    loadLiveBoard().catch(() => {});
  }, 45000);
  await loadTeams();
}

league.addEventListener("change", () => {
  writeParams({ league: league.value, home: "", away: "" });
  loadTeams().catch((error) => {
    status.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
  });
});

$("predict").addEventListener("click", () => {
  selectedKey = "";
  paintCarousel();
  writeParams({ league: league.value, home: home.value, away: away.value });
  runPredict().catch((error) => {
    status.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
  });
});

loadLeagues().catch((error) => {
  status.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
});
