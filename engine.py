import hashlib
import io
import json
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from penaltyblog.models import DixonColesGoalModel, dixon_coles_weights

import forecasts as forecast_store
import form as form_analysis
from markets import best_combos, rank_single_selections

USER_AGENT = "Mozilla/5.0"
TRACKING_NOTE = (
    "Prediction vs Actual is only shown for fixtures this app forecast "
    "before kickoff after tracking activation. No retrospective backfill."
)
# Default Dixon–Coles time-decay (penaltyblog dixon_coles_weights).
DEFAULT_XI = 0.0018
MODEL_MAX_GOALS = 15
EURO_BASE = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
EXTRA_BASE = "https://www.football-data.co.uk/new/{code}.csv"
FOTMOB_LEAGUE = "https://www.fotmob.com/api/data/leagues?id={league_id}"
FOTMOB_HEADERS = {"User-Agent": USER_AGENT}
FOTMOB_CREST = "https://images.fotmob.com/image_resources/logo/teamlogo/{team_id}.png"
FOTMOB_PLAYER = "https://images.fotmob.com/image_resources/playerimages/{player_id}.png"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
CSV_TTL = 6 * 3600
FOTMOB_LIVE_TTL = 45
FOTMOB_SEASON_TTL = 6 * 3600
PLAYER_TTL = 3 * 3600

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT})
_HTTP = ThreadPoolExecutor(max_workers=8)

LEAGUES = [
    {
        "id": "premier-league",
        "name": "Premier League",
        "country": "England",
        "kind": "european",
        "code": "E0",
        "season": "2627",
        "prior_season": "2526",
        "season_label": "2026/27",
        "fotmob_id": 47,
    },
    {
        "id": "ucl",
        "name": "UEFA Champions League",
        "country": "Europe",
        "kind": "fotmob",
        "season": "2026/2027",
        "prior_season": "2025/2026",
        "season_label": "2026/27",
        "fotmob_id": 42,
    },
    {
        "id": "laliga",
        "name": "LaLiga",
        "country": "Spain",
        "kind": "european",
        "code": "SP1",
        "season": "2627",
        "prior_season": "2526",
        "season_label": "2026/27",
        "fotmob_id": 87,
    },
    {
        "id": "serie-a",
        "name": "Serie A",
        "country": "Italy",
        "kind": "european",
        "code": "I1",
        "season": "2627",
        "prior_season": "2526",
        "season_label": "2026/27",
        "fotmob_id": 55,
    },
    {
        "id": "bundesliga",
        "name": "Bundesliga",
        "country": "Germany",
        "kind": "european",
        "code": "D1",
        "season": "2627",
        "prior_season": "2526",
        "season_label": "2026/27",
        "fotmob_id": 54,
    },
    {
        "id": "ligue-1",
        "name": "Ligue 1",
        "country": "France",
        "kind": "european",
        "code": "F1",
        "season": "2627",
        "prior_season": "2526",
        "season_label": "2026/27",
        "fotmob_id": 53,
    },
    {
        "id": "brasileirao",
        "name": "Brasileirão Série A",
        "country": "Brazil",
        "kind": "extra",
        "code": "BRA",
        "season": "2026",
        "prior_season": "2025",
        "season_label": "2026",
        "league_name": "Serie A",
        "fotmob_id": 268,
    },
    {
        "id": "primeira-liga",
        "name": "Primeira Liga",
        "country": "Portugal",
        "kind": "european",
        "code": "P1",
        "season": "2627",
        "prior_season": "2526",
        "season_label": "2026/27",
        "fotmob_id": 61,
    },
    {
        "id": "eredivisie",
        "name": "Eredivisie",
        "country": "Netherlands",
        "kind": "european",
        "code": "N1",
        "season": "2627",
        "prior_season": "2526",
        "season_label": "2026/27",
        "fotmob_id": 57,
    },
    {
        "id": "argentina",
        "name": "Argentine Primera División",
        "country": "Argentina",
        "kind": "extra",
        "code": "ARG",
        "season": "2026",
        "prior_season": "2025",
        "season_label": "2026",
        "league_name": "Liga Profesional",
        "fotmob_id": 112,
    },
    {
        "id": "mls",
        "name": "Major League Soccer",
        "country": "USA/Canada",
        "kind": "extra",
        "code": "USA",
        "season": "2026",
        "prior_season": "2025",
        "season_label": "2026",
        "league_name": "MLS",
        "fotmob_id": 130,
    },
]

TEAM_HINTS = {
    "ath madrid": "atletico madrid",
    "ath bilbao": "athletic",
    "man united": "manchester united",
    "man city": "manchester city",
    "nott m forest": "nottingham forest",
    "sp lisbon": "sporting",
    "sp braga": "braga",
    "paris sg": "paris saint germain",
    "m gladbach": "monchengladbach",
    "ein frankfurt": "eintracht frankfurt",
    "fc koln": "koln",
    "la coruna": "coruna",
    "vallecano": "rayo vallecano",
    "espanol": "espanyol",
    "betis": "real betis",
    "celta": "celta vigo",
    "sociedad": "real sociedad",
    "santander": "racing santander",
    "alaves": "alaves",
    "for sittard": "fortuna sittard",
    "nijmegen": "nec nijmegen",
    "den haag": "ado den haag",
    "psv eindhoven": "psv",
    "az alkmaar": "az alkmaar",
    "botafogo rj": "botafogo",
    "flamengo rj": "flamengo",
    "athletico pr": "athletico",
    "chapecoense sc": "chapecoense",
    "guimaraes": "guimaraes",
    "manchester united": "man united",
    "manchester city": "man city",
    "nottingham forest": "nott m forest",
    "tottenham hotspur": "tottenham",
    "newcastle united": "newcastle",
    "leeds united": "leeds",
    "brighton hove albion": "brighton",
    "west ham united": "west ham",
    "wolverhampton wanderers": "wolves",
    "coventry city": "coventry",
    "hull city": "hull",
    "ipswich town": "ipswich",
    "leicester city": "leicester",
    "atletico madrid": "ath madrid",
    "athletic club": "ath bilbao",
    "athletic bilbao": "ath bilbao",
    "real betis": "betis",
    "celta vigo": "celta",
    "celta de vigo": "celta",
    "rayo vallecano": "vallecano",
    "real sociedad": "sociedad",
    "deportivo alaves": "alaves",
    "deportivo a coruna": "la coruna",
    "racing de santander": "santander",
    "racing santander": "santander",
    "paris saint germain": "paris sg",
    "sporting cp": "sporting lisbon",
    "sporting lisbon": "sp lisbon",
    "estrela da amadora": "estrela",
    "cf estrela": "estrela",
    "espanyol": "espanol",
    "rcd espanyol": "espanol",
}

_CACHE: OrderedDict[str, dict] = OrderedDict()
_CRESTS = {}
_HTTP_MEM = {}
_FIXTURE_MEM = {}
_LIVE_BOARD = {"expires": 0.0, "rows": []}
MAX_LEAGUE_CACHE = 6
MAX_HTTP_MEM = 24
MAX_FIXTURE_MEM = 8
_LOCK = threading.Lock()
_FIT_EVENTS = {}
_PLAYER_LOCKS = {}


def _cache_path(kind: str, key: str) -> Path:
    digest = hashlib.sha1(key.encode()).hexdigest()
    folder = CACHE_DIR / kind
    folder.mkdir(parents=True, exist_ok=True)
    return folder / digest


def _mem_get(key: str):
    item = _HTTP_MEM.get(key)
    if not item:
        return None
    expires, value = item
    if expires < time.time():
        _HTTP_MEM.pop(key, None)
        return None
    return value


def _mem_set(key: str, value, ttl: int):
    _HTTP_MEM[key] = (time.time() + ttl, value)
    while len(_HTTP_MEM) > MAX_HTTP_MEM:
        _HTTP_MEM.pop(next(iter(_HTTP_MEM)))
    return value


def fetch_bytes(url: str, ttl: int = CSV_TTL, params: dict | None = None) -> bytes:
    key = url if not params else url + "?" + json.dumps(params, sort_keys=True)
    cached = _mem_get(key)
    if cached is not None:
        return cached
    path = _cache_path("http", key)
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        data = path.read_bytes()
        return _mem_set(key, data, ttl)
    try:
        response = _SESSION.get(url, params=params, timeout=20)
        response.raise_for_status()
        data = response.content
        path.write_bytes(data)
        return _mem_set(key, data, ttl)
    except Exception:
        if path.exists():
            data = path.read_bytes()
            return _mem_set(key, data, min(ttl, 300))
        raise


def fetch_json(url: str, ttl: int = FOTMOB_LIVE_TTL, params: dict | None = None) -> dict:
    raw = fetch_bytes(url, ttl=ttl, params=params)
    return json.loads(raw)


def league_by_id(league_id: str) -> dict:
    for league in LEAGUES:
        if league["id"] == league_id:
            return league
    raise KeyError(league_id)


def read_csv(url: str) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(fetch_bytes(url, ttl=CSV_TTL)), encoding="utf-8-sig")


def normalize_matches(frame: pd.DataFrame, season: str) -> pd.DataFrame:
    rename = {
        "HomeTeam": "home",
        "AwayTeam": "away",
        "FTHG": "goals_home",
        "FTAG": "goals_away",
        "Home": "home",
        "Away": "away",
        "HG": "goals_home",
        "AG": "goals_away",
    }
    matches = frame.rename(columns=rename)[
        ["Date", "home", "away", "goals_home", "goals_away"]
    ].copy()
    matches["date"] = pd.to_datetime(matches["Date"], dayfirst=True, errors="coerce")
    matches["season"] = season
    matches = matches.dropna(subset=["date", "home", "away", "goals_home", "goals_away"])
    matches["home"] = matches["home"].astype(str).str.strip()
    matches["away"] = matches["away"].astype(str).str.strip()
    matches["goals_home"] = matches["goals_home"].astype(int)
    matches["goals_away"] = matches["goals_away"].astype(int)
    return matches[["date", "home", "away", "goals_home", "goals_away", "season"]]


def load_european(league: dict) -> pd.DataFrame:
    urls = [
        (EURO_BASE.format(season=season, code=league["code"]), season)
        for season in (league["prior_season"], league["season"])
    ]
    frames = list(_HTTP.map(lambda item: normalize_matches(read_csv(item[0]), item[1]), urls))
    return pd.concat(frames, ignore_index=True)


def load_extra(league: dict) -> pd.DataFrame:
    raw = read_csv(EXTRA_BASE.format(code=league["code"]))
    wanted = raw["League"].astype(str).str.strip() == league["league_name"]
    raw = raw.loc[wanted].copy()
    raw["Season"] = raw["Season"].astype(str)
    frames = []
    for season in (league["prior_season"], league["season"]):
        frames.append(normalize_matches(raw.loc[raw["Season"] == season], season))
    return pd.concat(frames, ignore_index=True)


def load_league(league: dict) -> pd.DataFrame:
    if league["kind"] == "european":
        return load_european(league)
    if league["kind"] == "fotmob":
        return load_fotmob_results(league)
    return load_extra(league)


def fotmob_league_payload(league_id: int, season: str | None = None) -> dict:
    ttl = FOTMOB_SEASON_TTL if season else FOTMOB_LIVE_TTL
    params = {"season": season} if season else None
    return fetch_json(
        FOTMOB_LEAGUE.format(league_id=league_id),
        ttl=ttl,
        params=params,
    )


def parse_score(score_str: str | None) -> tuple[int, int] | None:
    if not score_str or "-" not in score_str:
        return None
    home, away = score_str.replace(" ", "").split("-", 1)
    return int(home), int(away)


def fotmob_kickoff(match: dict) -> datetime | None:
    raw = (match.get("status") or {}).get("utcTime")
    if not raw:
        return None
    stamp = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(stamp):
        return None
    return stamp.to_pydatetime()


def load_fotmob_results(league: dict) -> pd.DataFrame:
    def season_frame(season: str) -> pd.DataFrame:
        payload = fotmob_league_payload(league["fotmob_id"], season)
        rows = []
        for match in (payload.get("fixtures") or {}).get("allMatches") or []:
            status = match.get("status") or {}
            if not status.get("finished") or status.get("cancelled"):
                continue
            score = parse_score(status.get("scoreStr"))
            kickoff = fotmob_kickoff(match)
            if score is None or kickoff is None:
                continue
            rows.append(
                {
                    "date": kickoff,
                    "home": match["home"]["name"],
                    "away": match["away"]["name"],
                    "goals_home": score[0],
                    "goals_away": score[1],
                    "season": season,
                }
            )
        return pd.DataFrame(rows)

    frames = list(_HTTP.map(season_frame, (league["prior_season"], league["season"])))
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame(
            columns=["date", "home", "away", "goals_home", "goals_away", "season"]
        )
    return pd.concat(frames, ignore_index=True)


def fold_keys(name: str) -> set[str]:
    base = fold_name(name)
    keys = {base, base.replace("y", "")}
    hinted = TEAM_HINTS.get(base)
    if hinted:
        keys.add(hinted)
        keys.add(hinted.replace("y", ""))
    return {key for key in keys if key}


def map_to_catalog(name: str, catalog: list[str]) -> str | None:
    keys = fold_keys(name)
    exact = [item for item in catalog if fold_keys(item) & keys]
    if len(exact) == 1:
        return exact[0]
    via_hint = match_team(TEAM_HINTS.get(fold_name(name), fold_name(name)), catalog)
    if via_hint:
        return via_hint
    via_name = match_team(name, catalog)
    if via_name:
        return via_name
    query_tokens = set(fold_name(name).split())
    nested = [
        item
        for item in catalog
        if fold_name(item) and set(fold_name(item).split()) <= query_tokens
    ]
    if len(nested) == 1:
        return nested[0]
    return None


def resolve_tz(tz_name: str | None):
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    local = datetime.now().astimezone().tzinfo
    return local or timezone.utc


def parse_iso(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    value = pd.to_datetime(stamp, utc=True, errors="coerce")
    if pd.isna(value):
        return None
    return value.to_pydatetime()


def fixture_key(item: dict) -> tuple:
    return (item.get("kickoff"), item.get("home"), item.get("away"))


def crest_url(team_id) -> str | None:
    if team_id in (None, ""):
        return None
    return FOTMOB_CREST.format(team_id=team_id)


def remember_crest(league_id: str, name: str | None, team_id) -> str | None:
    url = crest_url(team_id)
    if league_id and name and url:
        _CRESTS.setdefault(league_id, {})[name] = url
    return url


def lookup_crest(league_id: str, name: str) -> str | None:
    return (_CRESTS.get(league_id) or {}).get(name)


def fixture_dict(
    match: dict,
    catalog: list[str] | None = None,
    now: datetime | None = None,
    league_id: str | None = None,
) -> dict:
    status = match.get("status") or {}
    reason = status.get("reason") or {}
    kickoff = fotmob_kickoff(match)
    home = match["home"]["name"]
    away = match["away"]["name"]
    mapped_home = map_to_catalog(home, catalog) if catalog else home
    mapped_away = map_to_catalog(away, catalog) if catalog else away
    finished = bool(status.get("finished"))
    cancelled = bool(status.get("cancelled"))
    live_time = status.get("liveTime") or {}
    minute = None
    if isinstance(live_time, dict):
        minute = live_time.get("short") or live_time.get("long")
    live = False
    if not cancelled and not finished:
        live = bool(status.get("started") or minute)
        if not live and now is not None and kickoff is not None and kickoff <= now:
            live = True
    home_name = mapped_home or home
    away_name = mapped_away or away
    home_logo = remember_crest(league_id, home_name, match["home"].get("id"))
    away_logo = remember_crest(league_id, away_name, match["away"].get("id"))
    match_id = match.get("id") or match.get("matchId")
    fixture_id = (
        f"{league_id}:{match_id}"
        if league_id and match_id is not None
        else None
    )
    postponed = bool(status.get("awarded") is False and reason.get("short") in {"Post", "PP"})
    return {
        "fixture_id": fixture_id,
        "match_id": match_id,
        "league_id": league_id,
        "home": home_name,
        "away": away_name,
        "home_source": home,
        "away_source": away,
        "home_short": match["home"].get("shortName") or home_name,
        "away_short": match["away"].get("shortName") or away_name,
        "home_logo": home_logo,
        "away_logo": away_logo,
        "kickoff": kickoff.isoformat() if kickoff else None,
        "venue": (match.get("venue") or {}).get("name") if isinstance(match.get("venue"), dict) else match.get("venue"),
        "status": "live" if live else "finished" if finished else ("cancelled" if cancelled else "scheduled"),
        "finished": finished,
        "cancelled": cancelled,
        "postponed": postponed,
        "minute": minute or (reason.get("short") if isinstance(reason, dict) else None),
        "score": status.get("scoreStr"),
        "predictable": bool(
            (mapped_home in catalog and mapped_away in catalog)
            if catalog
            else True
        ),
    }


def league_fixtures(
    league: dict, catalog: list[str] | None = None, tz_name: str | None = None
) -> dict:
    payload = fotmob_league_payload(
        league["fotmob_id"], league.get("season") if league["kind"] == "fotmob" else None
    )
    matches = (payload.get("fixtures") or {}).get("allMatches") or []
    tz = resolve_tz(tz_name)
    now = datetime.now(timezone.utc)
    today = now.astimezone(tz).date()
    live, upcoming = [], []
    league_id = league["id"]
    for match in matches:
        status = match.get("status") or {}
        home_raw, away_raw = match.get("home") or {}, match.get("away") or {}
        mapped_home = (
            map_to_catalog(home_raw.get("name", ""), catalog)
            if catalog
            else home_raw.get("name")
        )
        mapped_away = (
            map_to_catalog(away_raw.get("name", ""), catalog)
            if catalog
            else away_raw.get("name")
        )
        remember_crest(league_id, mapped_home or home_raw.get("name"), home_raw.get("id"))
        remember_crest(league_id, mapped_away or away_raw.get("name"), away_raw.get("id"))
        if status.get("cancelled") or status.get("finished"):
            continue
        item = fixture_dict(match, catalog, now, league_id)
        item["league_id"] = league_id
        item["league_name"] = league["name"]
        kickoff = parse_iso(item["kickoff"])
        if item["status"] == "live":
            live.append(item)
        elif kickoff and kickoff >= now:
            upcoming.append(item)
    live.sort(key=lambda item: item["kickoff"] or "")
    upcoming.sort(key=lambda item: item["kickoff"] or "")

    def on_local_date(item: dict) -> bool:
        kickoff = parse_iso(item["kickoff"])
        return bool(kickoff and kickoff.astimezone(tz).date() == today)

    today_matches = [item for item in live + upcoming if on_local_date(item)]
    shown = {fixture_key(item) for item in today_matches}
    later = [item for item in upcoming if fixture_key(item) not in shown]
    next_matches = []
    if later:
        first_kickoff = later[0]["kickoff"]
        next_matches = [item for item in later if item["kickoff"] == first_kickoff]
    upcoming_rest = [item for item in later if fixture_key(item) not in {fixture_key(m) for m in next_matches}]
    carousel, seen = [], set()
    for item in live + today_matches + next_matches + upcoming_rest:
        key = fixture_key(item)
        if key in seen:
            continue
        seen.add(key)
        carousel.append(item)
    return {
        "as_of": now.astimezone(tz).isoformat(),
        "timezone": getattr(tz, "key", str(tz)),
        "live": live,
        "today": today_matches,
        "next": next_matches[0] if next_matches else None,
        "next_matches": next_matches,
        "upcoming": upcoming_rest[:12],
        "carousel": carousel[:16],
    }


def live_matches_for_league(league: dict, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    payload = fotmob_league_payload(
        league["fotmob_id"],
        league.get("season") if league["kind"] == "fotmob" else None,
    )
    live = []
    for match in (payload.get("fixtures") or {}).get("allMatches") or []:
        status = match.get("status") or {}
        if status.get("cancelled") or status.get("finished"):
            continue
        item = fixture_dict(match, None, now, league["id"])
        if item["status"] != "live":
            continue
        item["league_id"] = league["id"]
        item["league_name"] = league["name"]
        item["country"] = league["country"]
        live.append(item)
    live.sort(key=lambda item: item["kickoff"] or "")
    return live


def get_live_board(tz_name: str | None = None) -> dict:
    tz = resolve_tz(tz_name)
    now = datetime.now(timezone.utc)
    if _LIVE_BOARD["expires"] > time.time():
        rows = _LIVE_BOARD["rows"]
    else:
        def one(league: dict) -> list[dict]:
            try:
                return live_matches_for_league(league, now)
            except Exception:
                return []

        rows = []
        for group in _HTTP.map(one, LEAGUES):
            rows.extend(group)
        rows.sort(
            key=lambda item: (item.get("league_name") or "", item.get("kickoff") or "")
        )
        _LIVE_BOARD["rows"] = rows
        _LIVE_BOARD["expires"] = time.time() + FOTMOB_LIVE_TTL
    return {
        "as_of": now.astimezone(tz).isoformat(),
        "timezone": getattr(tz, "key", str(tz)),
        "count": len(rows),
        "live": rows,
    }


def combo_markets(prediction, home: str, away: str) -> list[tuple[str, float]]:
    grid = prediction.grid
    home_goals, away_goals = np.indices(grid.shape)
    home_win = home_goals > away_goals
    draw = home_goals == away_goals
    away_win = home_goals < away_goals
    btts = (home_goals > 0) & (away_goals > 0)
    over = (home_goals + away_goals) > 2.5
    under = (home_goals + away_goals) < 2.5
    markets = [
        (f"{home} win + BTTS", home_win & btts),
        (f"{home} win + Over 2.5", home_win & over),
        (f"{home} win + Under 2.5", home_win & under),
        ("BTTS + Over 2.5", btts & over),
        ("Draw + BTTS", draw & btts),
        (f"{away} win + BTTS", away_win & btts),
        (f"{away} win + Over 2.5", away_win & over),
        ("Draw + Under 2.5", draw & under),
    ]
    ranked = [(label, float(grid[mask].sum())) for label, mask in markets]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _joint_mask(masks):
    mask = masks[0]
    for extra in masks[1:]:
        mask = mask & extra
    return mask


def _combo_conflict(a: dict, b: dict) -> bool:
    if a["id"] == b["id"] or a["family"] == b["family"]:
        return True
    families = {a["family"], b["family"]}
    if families == {"1x2", "dc"}:
        return True
    nested = {
        frozenset({"over_15", "over_25"}),
        frozenset({"over_15", "over_35"}),
        frozenset({"over_25", "over_35"}),
        frozenset({"under_15", "under_25"}),
        frozenset({"over_15", "under_15"}),
        frozenset({"over_25", "under_25"}),
        frozenset({"over_35", "under_25"}),
        frozenset({"over_35", "under_15"}),
        frozenset({"btts_yes", "home_win_nil"}),
        frozenset({"btts_yes", "away_win_nil"}),
        frozenset({"home_win", "home_win_nil"}),
        frozenset({"away_win", "away_win_nil"}),
        frozenset({"home_win", "away_win_nil"}),
        frozenset({"away_win", "home_win_nil"}),
        frozenset({"draw", "home_win_nil"}),
        frozenset({"draw", "away_win_nil"}),
    }
    return frozenset({a["id"], b["id"]}) in nested


def _combo_ok(legs: list[dict]) -> bool:
    return all(not _combo_conflict(a, b) for a, b in combinations(legs, 2))


def _combo_pack(legs: list[dict], grid, n: int, risk: str, explain: str) -> dict:
    mask = _joint_mask([leg["mask"] for leg in legs])
    labels = [leg["label"] for leg in legs]
    return {
        "legs": labels,
        "label": " + ".join(labels),
        "prob": float(grid[mask].sum()),
        "n": n,
        "risk": risk,
        "explain": explain,
        "_mask": mask,
        "_legs": legs,
    }


def _profile_totals(prediction, total) -> dict:
    xg = float(prediction.home_goal_expectation + prediction.away_goal_expectation)
    over_35 = float(prediction.total_goals("over", 3.5))
    over_25 = float(prediction.total_goals("over", 2.5))
    under_25 = float(prediction.total_goals("under", 2.5))
    if over_35 >= 0.50:
        return {
            "id": "over_35",
            "family": "totals",
            "label": "Over 3.5 Goals",
            "mask": total > 3.5,
        }
    if xg >= 2.7 or over_25 >= 0.56:
        return {
            "id": "over_25",
            "family": "totals",
            "label": "Over 2.5 Goals",
            "mask": total > 2.5,
        }
    if xg <= 2.2 or under_25 >= 0.55:
        return {
            "id": "under_25",
            "family": "totals",
            "label": "Under 2.5 Goals",
            "mask": total < 2.5,
        }
    return {
        "id": "over_15",
        "family": "totals",
        "label": "Over 1.5 Goals",
        "mask": total > 1.5,
    }


def _combo_leg_pool(prediction, home: str, away: str) -> list[dict]:
    grid = prediction.grid
    home_goals, away_goals = np.indices(grid.shape)
    total = home_goals + away_goals
    btts = (home_goals > 0) & (away_goals > 0)
    home_win = home_goals > away_goals
    draw = home_goals == away_goals
    away_win = home_goals < away_goals
    pool = [
        {"id": "home_win", "family": "1x2", "label": f"{home} win", "mask": home_win},
        {"id": "draw", "family": "1x2", "label": "Draw", "mask": draw},
        {"id": "away_win", "family": "1x2", "label": f"{away} win", "mask": away_win},
        {
            "id": "dc_1x",
            "family": "dc",
            "label": f"{home} or Draw",
            "mask": home_win | draw,
        },
        {
            "id": "dc_x2",
            "family": "dc",
            "label": f"{away} or Draw",
            "mask": away_win | draw,
        },
        {"id": "over_15", "family": "totals", "label": "Over 1.5 Goals", "mask": total > 1.5},
        {"id": "over_25", "family": "totals", "label": "Over 2.5 Goals", "mask": total > 2.5},
        {"id": "over_35", "family": "totals", "label": "Over 3.5 Goals", "mask": total > 3.5},
        {"id": "under_25", "family": "totals", "label": "Under 2.5 Goals", "mask": total < 2.5},
        {"id": "under_15", "family": "totals", "label": "Under 1.5 Goals", "mask": total < 1.5},
        {"id": "btts_yes", "family": "btts", "label": "BTTS — Yes", "mask": btts},
        {"id": "btts_no", "family": "btts", "label": "BTTS — No", "mask": ~btts},
        {
            "id": "home_over_15",
            "family": "home_goals",
            "label": f"{home} over 1.5 goals",
            "mask": home_goals > 1.5,
        },
        {
            "id": "away_over_15",
            "family": "away_goals",
            "label": f"{away} over 1.5 goals",
            "mask": away_goals > 1.5,
        },
        {
            "id": "home_win_nil",
            "family": "win_nil",
            "label": f"{home} win to nil",
            "mask": home_win & (away_goals == 0),
        },
        {
            "id": "away_win_nil",
            "family": "win_nil",
            "label": f"{away} win to nil",
            "mask": away_win & (home_goals == 0),
        },
    ]
    for _prob, goals_h, goals_a in top_scores(prediction, 3):
        pool.append(
            {
                "id": f"exact_{goals_h}_{goals_a}",
                "family": "exact",
                "label": f"Exact {goals_h}-{goals_a}",
                "mask": (home_goals == goals_h) & (away_goals == goals_a),
            }
        )
    return pool


def multi_leg_picks(prediction, home: str, away: str) -> tuple[dict, dict]:
    grid = prediction.grid
    home_goals, away_goals = np.indices(grid.shape)
    total = home_goals + away_goals
    pool = _combo_leg_pool(prediction, home, away)
    by_id = {leg["id"]: leg for leg in pool}

    result_side, _result_label, _prob = predicted_1x2(prediction, home, away)
    result = by_id[{"home": "home_win", "away": "away_win", "draw": "draw"}[result_side]]
    totals = _profile_totals(prediction, total)

    third_choices = [
        leg
        for leg in pool
        if leg["id"] not in {result["id"], totals["id"]}
        and _combo_ok([result, totals, leg])
    ]
    best_third = None
    for leg in third_choices:
        mask = _joint_mask([result["mask"], totals["mask"], leg["mask"]])
        prob = float(grid[mask].sum())
        if best_third is None or prob > best_third[0]:
            best_third = (prob, leg)

    if best_third is None:
        btts = by_id["btts_yes" if float(prediction.btts_yes) >= 0.5 else "btts_no"]
        three = [result, btts, totals]
    else:
        three = [result, totals, best_third[1]]

    medium = _combo_pack(
        three,
        grid,
        3,
        "MEDIUM",
        "Anchored to the Dixon–Coles 1X2, then the totals line and extra market with the highest joint probability on this match’s score grid.",
    )

    remain = [leg for leg in pool if leg["id"] not in {item["id"] for item in three}]
    best_fourth = None
    three_prob = medium["prob"]
    for leg in remain:
        if not _combo_ok(three + [leg]):
            continue
        mask = medium["_mask"] & leg["mask"]
        prob = float(grid[mask].sum())
        if prob < 0.02 or prob >= three_prob - 1e-12:
            continue
        if best_fourth is None or prob > best_fourth[0]:
            best_fourth = (prob, leg)

    if best_fourth is None:
        exact_p, goals_h, goals_a = top_scores(prediction, 1)[0]
        fourth = {
            "id": f"exact_{goals_h}_{goals_a}",
            "family": "exact",
            "label": f"Exact {goals_h}-{goals_a}",
            "mask": (home_goals == goals_h) & (away_goals == goals_a),
        }
    else:
        fourth = best_fourth[1]

    high = _combo_pack(
        three + [fourth],
        grid,
        4,
        "HIGH",
        "Same 3-leg, tightened with the extra market that most reduces the Dixon–Coles score grid without becoming implied.",
    )
    medium.pop("_mask", None)
    medium.pop("_legs", None)
    high.pop("_mask", None)
    high.pop("_legs", None)
    return medium, high


def confidence_label(prob: float) -> str:
    if prob >= 0.70:
        return "HIGH"
    if prob >= 0.55:
        return "MEDIUM"
    return "SPECULATIVE"


def combo_risk(n: int, prob: float) -> str:
    if n <= 2 and prob >= 0.55:
        return "LOWER"
    if n <= 3:
        return "MEDIUM"
    return "HIGH"


def predicted_1x2(prediction, home: str, away: str) -> tuple[str, str, float]:
    home_win, draw, away_win = (
        float(prediction.home_win),
        float(prediction.draw),
        float(prediction.away_win),
    )
    if home_win >= draw and home_win >= away_win:
        return "home", f"{home} win", home_win
    if away_win >= home_win and away_win >= draw:
        return "away", f"{away} win", away_win
    return "draw", "Draw", draw


def single_market_pool(prediction, home: str, away: str) -> list[dict]:
    grid = prediction.grid
    home_goals, away_goals = np.indices(grid.shape)
    total = home_goals + away_goals
    xg_home = float(prediction.home_goal_expectation)
    xg_away = float(prediction.away_goal_expectation)
    expected_total = xg_home + xg_away
    home_win = float(prediction.home_win)
    draw = float(prediction.draw)
    away_win = float(prediction.away_win)
    over_15 = float(prediction.total_goals("over", 1.5))
    over_25 = float(prediction.total_goals("over", 2.5))
    over_35 = float(prediction.total_goals("over", 3.5))
    under_25 = float(prediction.total_goals("under", 2.5))
    btts_yes = float(prediction.btts_yes)
    btts_no = float(prediction.btts_no)
    home_over_15 = float(grid[home_goals > 1.5].sum())
    away_over_15 = float(grid[away_goals > 1.5].sum())

    return [
        {
            "id": "home_win",
            "family": "1x2",
            "market": "Match result",
            "selection": f"{home} win",
            "prob": home_win,
            "explain": f"Highest 1X2 outcome from the score grid ({home_win:.1%}).",
        },
        {
            "id": "draw",
            "family": "1x2",
            "market": "Match result",
            "selection": "Draw",
            "prob": draw,
            "explain": f"Draw probability from the score grid ({draw:.1%}).",
        },
        {
            "id": "away_win",
            "family": "1x2",
            "market": "Match result",
            "selection": f"{away} win",
            "prob": away_win,
            "explain": f"Highest 1X2 outcome from the score grid ({away_win:.1%}).",
        },
        {
            "id": "dc_1x",
            "family": "dc",
            "blocks": {"1x2"},
            "market": "Double chance",
            "selection": f"{home} or Draw",
            "prob": float(prediction.double_chance_1x),
            "explain": f"Home {home_win:.1%} + Draw {draw:.1%}.",
        },
        {
            "id": "dc_x2",
            "family": "dc",
            "blocks": {"1x2"},
            "market": "Double chance",
            "selection": f"{away} or Draw",
            "prob": float(prediction.double_chance_x2),
            "explain": f"Away {away_win:.1%} + Draw {draw:.1%}.",
        },
        {
            "id": "over_15",
            "family": "totals",
            "line": 1.5,
            "side": "over",
            "market": "Total goals",
            "selection": "Over 1.5 Goals",
            "prob": over_15,
            "explain": f"Expected total goals: {expected_total:.2f}.",
        },
        {
            "id": "over_25",
            "family": "totals",
            "line": 2.5,
            "side": "over",
            "market": "Total goals",
            "selection": "Over 2.5 Goals",
            "prob": over_25,
            "explain": f"Expected total goals: {expected_total:.2f}.",
        },
        {
            "id": "over_35",
            "family": "totals",
            "line": 3.5,
            "side": "over",
            "market": "Total goals",
            "selection": "Over 3.5 Goals",
            "prob": over_35,
            "explain": f"Expected total goals: {expected_total:.2f}.",
        },
        {
            "id": "under_25",
            "family": "totals",
            "line": 2.5,
            "side": "under",
            "market": "Total goals",
            "selection": "Under 2.5 Goals",
            "prob": under_25,
            "explain": f"Expected total goals: {expected_total:.2f}.",
        },
        {
            "id": "btts_yes",
            "family": "btts",
            "market": "Both teams to score",
            "selection": "BTTS — Yes",
            "prob": btts_yes,
            "explain": "Share of scorelines where both teams score at least once.",
        },
        {
            "id": "btts_no",
            "family": "btts",
            "market": "Both teams to score",
            "selection": "BTTS — No",
            "prob": btts_no,
            "explain": "Share of scorelines where at least one team is shut out.",
        },
        {
            "id": "home_over_15",
            "family": "home_goals",
            "market": f"{home} team goals",
            "selection": f"{home} over 1.5 goals",
            "prob": home_over_15,
            "explain": f"Home expected goals: {xg_home:.2f}.",
        },
        {
            "id": "away_over_15",
            "family": "away_goals",
            "market": f"{away} team goals",
            "selection": f"{away} over 1.5 goals",
            "prob": away_over_15,
            "explain": f"Away expected goals: {xg_away:.2f}.",
        },
    ]


def rank_single_picks(prediction, home: str, away: str, limit: int = 4) -> list[dict]:
    pool = single_market_pool(prediction, home, away)
    pool.sort(key=lambda item: item["prob"], reverse=True)
    picked = []
    families = set()
    totals = []
    blocked = set()

    def totals_ok(item: dict) -> bool:
        if item["family"] != "totals":
            return True
        if len(totals) >= 2:
            return False
        for existing in totals:
            if existing.get("side") != item.get("side") and existing.get("line") == item.get("line"):
                return False
            if existing.get("side") == "over" and item.get("side") == "under" and item.get("line") <= existing.get("line"):
                return False
            if existing.get("side") == "under" and item.get("side") == "over" and item.get("line") >= existing.get("line"):
                return False
        return True

    for item in pool:
        if item["id"].endswith("over_05"):
            continue
        if item["family"] != "totals" and (item["family"] in families or item["family"] in blocked):
            continue
        if item["family"] == "totals" and not totals_ok(item):
            continue
        picked.append(
            {
                "rank": len(picked) + 1,
                "id": item["id"],
                "market": item["market"],
                "selection": item["selection"],
                "prob": float(item["prob"]),
                "confidence": confidence_label(item["prob"]),
                "explain": item["explain"],
            }
        )
        families.add(item["family"])
        blocked.update(item.get("blocks") or set())
        if item["family"] == "dc":
            blocked.add("1x2")
        if item["family"] == "1x2":
            blocked.add("dc")
        if item["family"] == "totals":
            totals.append(item)
        if len(picked) == limit:
            break
    return picked


def best_two_leg(prediction, home: str, away: str) -> dict:
    grid = prediction.grid
    home_goals, away_goals = np.indices(grid.shape)
    total = home_goals + away_goals
    home_win = home_goals > away_goals
    draw = home_goals == away_goals
    away_win = home_goals < away_goals
    btts = (home_goals > 0) & (away_goals > 0)
    expected_total = float(prediction.home_goal_expectation + prediction.away_goal_expectation)
    candidates = [
        ([f"{home} or Draw", "Over 1.5 Goals"], (home_win | draw) & (total > 1.5)),
        ([f"{away} or Draw", "Over 1.5 Goals"], (away_win | draw) & (total > 1.5)),
        ([f"{home} win", "Over 1.5 Goals"], home_win & (total > 1.5)),
        ([f"{away} win", "Over 1.5 Goals"], away_win & (total > 1.5)),
        ([f"{home} or Draw", "Over 2.5 Goals"], (home_win | draw) & (total > 2.5)),
        ([f"{away} or Draw", "Over 2.5 Goals"], (away_win | draw) & (total > 2.5)),
        ([f"{home} win", "Over 2.5 Goals"], home_win & (total > 2.5)),
        ([f"{away} win", "Over 2.5 Goals"], away_win & (total > 2.5)),
        ([f"{home} win", "Under 2.5 Goals"], home_win & (total < 2.5)),
        ([f"{away} win", "Under 2.5 Goals"], away_win & (total < 2.5)),
        (["Draw", "Under 2.5 Goals"], draw & (total < 2.5)),
        ([f"{home} or Draw", "BTTS — Yes"], (home_win | draw) & btts),
        ([f"{away} or Draw", "BTTS — Yes"], (away_win | draw) & btts),
        ([f"{home} win", "BTTS — Yes"], home_win & btts),
        ([f"{away} win", "BTTS — Yes"], away_win & btts),
        (["BTTS — Yes", "Over 2.5 Goals"], btts & (total > 2.5)),
        (["BTTS — No", "Under 2.5 Goals"], (~btts) & (total < 2.5)),
        ([f"{home} win", "BTTS — No"], home_win & ~btts),
        ([f"{away} win", "BTTS — No"], away_win & ~btts),
    ]
    ranked = [
        (legs, float(grid[mask].sum()))
        for legs, mask in candidates
    ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    legs, prob = ranked[0]
    return {
        "legs": legs,
        "label": " + ".join(legs),
        "prob": prob,
        "n": 2,
        "risk": combo_risk(2, prob),
        "explain": f"Expected goals: {expected_total:.2f}",
    }


def arrays(*columns):
    return [np.array(column, copy=True) for column in columns]


def fold_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    text = text.lower().replace("'", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def match_team(fd_name: str, fotmob_names: list[str]) -> str | None:
    query = TEAM_HINTS.get(fold_name(fd_name), fold_name(fd_name))
    folded = {name: fold_name(name) for name in fotmob_names}
    exact = [name for name, key in folded.items() if key == query]
    if len(exact) == 1:
        return exact[0]

    query_tokens = set(query.split())
    contained = [
        name
        for name, key in folded.items()
        if query in key or query_tokens <= set(key.split())
    ]
    if len(contained) == 1:
        return contained[0]
    if contained:
        return max(
            contained,
            key=lambda name: len(query_tokens & set(folded[name].split()))
            / len(query_tokens | set(folded[name].split())),
        )

    scored = []
    for name, key in folded.items():
        tokens = set(key.split())
        if not query_tokens or not tokens:
            continue
        score = len(query_tokens & tokens) / len(query_tokens | tokens)
        scored.append((score, name))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= 0.5:
        return scored[0][1]
    return None


def top_scores(prediction, limit: int = 10) -> list[tuple[float, int, int]]:
    grid = prediction.grid
    ranked = [
        (float(grid[home_goals, away_goals]), home_goals, away_goals)
        for home_goals in range(grid.shape[0])
        for away_goals in range(grid.shape[1])
    ]
    ranked.sort(reverse=True)
    return ranked[:limit]


def load_fotmob_stat(url: str, column: str) -> pd.DataFrame:
    payload = fetch_json(url, ttl=PLAYER_TTL)
    rows = payload["TopLists"][0]["StatList"]
    frame = pd.DataFrame(rows).rename(
        columns={
            "ParticiantId": "player_id",
            "ParticipantName": "player",
            "TeamName": "team",
            "StatValue": column,
            "MinutesPlayed": "minutes",
            "MatchesPlayed": "matches",
        }
    )
    return frame[["player_id", "player", "team", column, "minutes", "matches"]]


def load_fotmob_players(league_id: int) -> pd.DataFrame:
    meta = fotmob_league_payload(league_id)
    season = meta["details"]["selectedSeason"]
    link = next(
        item for item in meta["stats"]["seasonStatLinks"] if item["Name"] == season
    )
    base = "https://data.fotmob.com/" + link["RelativePath"].rsplit("/", 1)[0] + "/"
    files = [
        ("mins_played.json", "minutes_stat"),
        ("goals.json", "goals"),
        ("goal_assist.json", "assists"),
        ("expected_goals.json", "xg"),
        ("expected_assists.json", "xa"),
    ]
    loaded = list(
        _HTTP.map(lambda item: load_fotmob_stat(base + item[0], item[1]), files)
    )
    minutes = loaded[0].rename(columns={"minutes_stat": "minutes_played"})[
        ["player_id", "player", "team", "minutes", "matches"]
    ]
    players = minutes
    for frame, column in zip(loaded[1:], ("goals", "assists", "xg", "xa")):
        players = players.merge(frame[["player_id", column]], on="player_id", how="left")
    for column in ("goals", "assists", "xg", "xa"):
        players[column] = players[column].fillna(0.0)
    players["season"] = season
    return players


def anytime_probability(share: float, team_xg: float) -> float:
    lam = max(share, 0.0) * max(team_xg, 0.0)
    return float(1.0 - np.exp(-lam))


def player_picks(
    players: pd.DataFrame,
    fotmob_team: str,
    team_xg: float,
    kind: str,
    limit: int = 3,
) -> pd.DataFrame:
    squad = players.loc[players["team"] == fotmob_team].copy()
    squad = squad.loc[squad["minutes"] >= 45]
    value_col = "xg" if kind == "score" else "xa"
    count_col = "goals" if kind == "score" else "assists"
    squad["signal"] = np.maximum(squad[value_col], squad[count_col] * 0.7)
    team_signal = squad["signal"].sum()
    if team_signal <= 0:
        return squad.iloc[0:0]
    squad["share"] = squad["signal"] / team_signal
    squad["chance"] = squad["share"].map(
        lambda share: anytime_probability(share, team_xg)
    )
    return squad.sort_values(["chance", "signal"], ascending=False).head(limit)


def players_as_dicts(frame: pd.DataFrame) -> list[dict]:
    rows = []
    for row in frame.itertuples():
        rows.append(
            {
                "player": row.player,
                "team": getattr(row, "team", ""),
                "chance": float(row.chance),
                "goals": int(row.goals),
                "assists": int(row.assists),
                "xg": float(row.xg),
                "xa": float(row.xa),
                "minutes": int(row.minutes),
                "matches": int(row.matches),
                "player_id": int(row.player_id) if getattr(row, "player_id", None) else None,
                "photo": FOTMOB_PLAYER.format(player_id=int(row.player_id))
                if getattr(row, "player_id", None)
                else None,
            }
        )
    return rows


def dixon_coles_meta(model) -> dict:
    meta = {
        "model": "Dixon–Coles",
        "model_library": "penaltyblog",
        "model_class": f"{type(model).__module__}.{type(model).__name__}",
        "penaltyblog_version": __import__("penaltyblog").__version__,
        "xi": DEFAULT_XI,
        "max_goals": MODEL_MAX_GOALS,
    }
    params = getattr(model, "params", None)
    if isinstance(params, dict):
        mapping = params
    else:
        names = getattr(model, "param_names", None) or getattr(model, "_param_names", None)
        mapping = None
        if params is not None and names is not None:
            try:
                mapping = dict(zip(list(names), np.asarray(params).ravel()))
            except (TypeError, ValueError):
                mapping = None
    if mapping:
        for key in ("rho", "home_advantage"):
            if key not in mapping:
                continue
            try:
                meta[key] = float(np.asarray(mapping[key]).reshape(-1)[0])
            except (TypeError, ValueError, IndexError):
                continue
    for attr in ("aic", "loglikelihood", "fitted"):
        if hasattr(model, attr):
            value = getattr(model, attr)
            if value is not None and not callable(value):
                try:
                    meta[attr] = float(value) if attr != "fitted" else bool(value)
                except (TypeError, ValueError):
                    meta[attr] = value
    return meta


def training_cutoff(kickoff: str | None, now: datetime | None = None) -> datetime:
    """Use regulation kickoff as the hard information boundary when known."""
    now = now or datetime.now(timezone.utc)
    if not kickoff:
        return now
    try:
        kick = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    except ValueError:
        return now
    if kick.tzinfo is None:
        kick = kick.replace(tzinfo=timezone.utc)
    kick = kick.astimezone(timezone.utc)
    return min(now, kick)


def fit_league(
    league: dict,
    cutoff: datetime | None = None,
    xi: float = DEFAULT_XI,
) -> dict:
    matches = load_league(league)
    if matches.empty:
        raise ValueError(f"No completed matches found for {league['name']}.")
    cut = cutoff or datetime.now(timezone.utc)
    dates = pd.to_datetime(matches["date"], utc=True)
    trainable = matches.loc[dates < cut].copy()
    if trainable.empty:
        raise ValueError(
            f"No completed matches before forecast cutoff for {league['name']}."
        )
    current = trainable[trainable["season"] == league["season"]]
    if current.empty:
        # Do not silently train only on prior season as if it were current.
        raise ValueError(
            f"No {league['season_label']} matches before cutoff for {league['name']}. "
            "Refusing to substitute older-season-only strengths."
        )
    weights = np.array(
        dixon_coles_weights(trainable["date"].tolist(), xi=xi),
        dtype=float,
        copy=True,
    )
    model = DixonColesGoalModel(
        *arrays(
            trainable["goals_home"].to_numpy(dtype=int),
            trainable["goals_away"].to_numpy(dtype=int),
            trainable["home"].to_numpy(dtype=str),
            trainable["away"].to_numpy(dtype=str),
            weights,
        )
    )
    model.fit()
    season_teams = sorted(
        set(matches.loc[matches["season"] == league["season"], "home"])
        | set(matches.loc[matches["season"] == league["season"], "away"])
    )
    latest_match = pd.to_datetime(trainable["date"], utc=True).max()
    return {
        "league": league,
        "matches": matches,
        "trainable": trainable,
        "current": current,
        "teams": season_teams,
        "model": model,
        "players": None,
        "player_error": None,
        "trained_on": len(trainable),
        "current_matches": len(current),
        "prior_matches": int((trainable["season"] != league["season"]).sum()),
        "cutoff": cut.isoformat(),
        "xi": xi,
        "results_through": latest_match.date().isoformat(),
        "data_version": f"{league['id']}|{latest_match.isoformat()}|{len(trainable)}|{xi}",
    }


def get_league_state(
    league_id: str,
    cutoff: datetime | None = None,
    xi: float = DEFAULT_XI,
) -> dict:
    league_by_id(league_id)  # raise KeyError for unknown ids before locking
    cut = cutoff or datetime.now(timezone.utc)
    # Bucket cutoffs to the hour so we do not refit on every click.
    cut_key = cut.astimezone(timezone.utc).strftime("%Y%m%d%H")
    cache_key = f"{league_id}|{cut_key}|{xi}"
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None:
            _CACHE.move_to_end(cache_key)
            cached = dict(cached)
            cached["from_cache"] = True
            return cached
        event = _FIT_EVENTS.get(cache_key)
        if event is None:
            event = threading.Event()
            _FIT_EVENTS[cache_key] = event
            owner = True
        else:
            owner = False
    if not owner:
        event.wait(timeout=180)
        with _LOCK:
            cached = _CACHE.get(cache_key)
            if cached is not None:
                cached = dict(cached)
                cached["from_cache"] = True
                return cached
            _FIT_EVENTS.pop(cache_key, None)
        raise ValueError(
            f"Could not finish loading {league_id}. The model fit failed or timed out — try again."
        )
    try:
        state = fit_league(league_by_id(league_id), cutoff=cut, xi=xi)
        state["from_cache"] = False
        with _LOCK:
            _CACHE[cache_key] = state
            _CACHE.move_to_end(cache_key)
            while len(_CACHE) > MAX_LEAGUE_CACHE:
                old_id, _ = _CACHE.popitem(last=False)
                _FIT_EVENTS.pop(old_id, None)
        threading.Thread(target=_warmup_players, args=(league_id,), daemon=True).start()
        return state
    except Exception:
        with _LOCK:
            _FIT_EVENTS.pop(cache_key, None)
        raise
    finally:
        event.set()
        with _LOCK:
            # Drop the latch once the fit attempt finished (success is in _CACHE).
            if cache_key not in _CACHE:
                _FIT_EVENTS.pop(cache_key, None)


def get_fixtures(league_id: str, tz_name: str | None = None) -> dict:
    key = f"{league_id}|{tz_name or ''}"
    packed = _FIXTURE_MEM.get(key)
    if packed and packed[0] > time.time():
        return packed[1]
    state = get_league_state(league_id)
    catalog = None if state["league"]["kind"] == "fotmob" else state["teams"]
    data = league_fixtures(state["league"], catalog, tz_name)
    _FIXTURE_MEM[key] = (time.time() + FOTMOB_LIVE_TTL, data)
    while len(_FIXTURE_MEM) > MAX_FIXTURE_MEM:
        _FIXTURE_MEM.pop(next(iter(_FIXTURE_MEM)))
    return data


def find_fixture(
    league_id: str,
    home: str,
    away: str,
    fixture_id: str | None = None,
    kickoff: str | None = None,
) -> dict | None:
    """Resolve the scheduled FotMob meeting when possible."""
    try:
        fixtures = get_fixtures(league_id)
    except Exception:
        return None
    pools = []
    for key in ("live", "today", "next_matches", "upcoming", "carousel"):
        pools.extend(fixtures.get(key) or [])
    if fixtures.get("next"):
        pools.append(fixtures["next"])

    if fixture_id:
        for item in pools:
            if item.get("fixture_id") == fixture_id:
                return item

    def fold(name: str) -> str:
        return fold_name(name)

    candidates = [
        item
        for item in pools
        if fold(item.get("home") or "") == fold(home)
        and fold(item.get("away") or "") == fold(away)
    ]
    if kickoff:
        for item in candidates:
            if item.get("kickoff") == kickoff:
                return item
    # Prefer next scheduled / live, not an arbitrary finished historical card
    ordered = sorted(
        candidates,
        key=lambda item: (
            0 if item.get("status") == "live" else 1 if item.get("status") == "scheduled" else 2,
            item.get("kickoff") or "",
        ),
    )
    return ordered[0] if ordered else None


def _warmup_players(league_id: str) -> None:
    try:
        _players_for_state(get_league_state(league_id))
    except Exception:
        return


def _players_for_state(state: dict):
    league_id = state["league"]["id"]
    with _LOCK:
        lock = _PLAYER_LOCKS.setdefault(league_id, threading.Lock())
    with lock:
        if state["players"] is not None or state["player_error"] is not None:
            return
        try:
            state["players"] = load_fotmob_players(state["league"]["fotmob_id"])
        except Exception as error:
            state["player_error"] = str(error)


def build_prediction(
    league_id: str,
    home: str,
    away: str,
    fixture_id: str | None = None,
    kickoff: str | None = None,
) -> dict:
    forecast_store.init_store()
    fixture = find_fixture(league_id, home, away, fixture_id=fixture_id, kickoff=kickoff)
    if fixture:
        home = fixture["home"]
        away = fixture["away"]
        fixture_id = fixture.get("fixture_id") or fixture_id
        kickoff = fixture.get("kickoff") or kickoff
        forecast_kind = (
            "upcoming_fixture"
            if fixture.get("status") in {"scheduled", "live"}
            else "completed_fixture"
        )
    elif fixture_id and kickoff:
        # Client supplied a dated fixture identity even if live fixture lookup failed.
        try:
            kick_dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
            if kick_dt.tzinfo is None:
                kick_dt = kick_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if kick_dt > now:
                forecast_kind = "upcoming_fixture"
            else:
                forecast_kind = "completed_fixture"
        except ValueError:
            forecast_kind = "upcoming_fixture"
    else:
        forecast_kind = "hypothetical_matchup"

    cutoff = training_cutoff(kickoff)
    state = get_league_state(league_id, cutoff=cutoff)
    teams = state["teams"]
    if home not in teams or away not in teams:
        raise ValueError(
            "Use team names from the selected season catalog. "
            f"{home!r} / {away!r} not both in {state['league']['season_label']}."
        )
    if home == away:
        raise ValueError("Choose two different teams.")

    prediction = state["model"].predict(home, away, max_goals=MODEL_MAX_GOALS, normalize=True)
    ranked = top_scores(prediction)
    combo_bundle = best_combos(prediction, home, away)
    singles = rank_single_selections(prediction, home, away, limit=3)
    result_side, result_label, result_prob = predicted_1x2(prediction, home, away)
    matches = state["trainable"]
    h2h = matches[
        ((matches["home"] == home) & (matches["away"] == away))
        | ((matches["home"] == away) & (matches["away"] == home))
    ].sort_values("date")
    now = datetime.now(timezone.utc)

    home_form = form_analysis.team_recent_form(matches, home, cutoff)
    away_form = form_analysis.team_recent_form(matches, away, cutoff)
    home_venue = form_analysis.venue_career_stats(matches, home, "home", cutoff)
    away_venue = form_analysis.venue_career_stats(matches, away, "away", cutoff)

    lineup = {
        "status": "Unavailable",
        "source": None,
        "retrieved_at": None,
        "included_in_model": False,
        "note": (
            "Confirmed/expected lineups, injuries and suspensions are not fetched from a "
            "validated free source in this build. Lineup context is displayed as unavailable "
            "and is not included numerically in the Dixon–Coles forecast."
        ),
    }

    freshness = {
        "forecast_generated_at": now.isoformat(),
        "results_current_through": state["results_through"],
        "training_cutoff": state["cutoff"],
        "data_version": state["data_version"],
        "from_cache": bool(state.get("from_cache")),
        "stale": False,
        "sources": [
            {
                "name": "football-data.co.uk / FotMob results",
                "role": "match results for Dixon–Coles fit",
                "ttl_seconds": CSV_TTL,
            },
            {
                "name": "FotMob fixtures",
                "role": "kickoff, status, fixture identity",
                "ttl_seconds": FOTMOB_LIVE_TTL,
            },
            {
                "name": "FotMob player stats",
                "role": "anytime scorer/assist display only",
                "ttl_seconds": PLAYER_TTL,
            },
        ],
        "lineup_status": lineup["status"],
        "lineup_retrieved_at": None,
    }

    limitations = [
        "Regulation time only (plus stoppage). Extra time and penalties are excluded.",
        "Player anytime probabilities use season FotMob xG/xA shares × team λ; they do not alter the match score grid.",
        "No bookmaker odds feed: fair odds are shown; value is unavailable.",
        TRACKING_NOTE,
        lineup["note"],
        home_form.get("note"),
    ]

    result = {
        "fixture_id": fixture_id,
        "forecast_kind": forecast_kind,
        "league_id": league_id,
        "league": state["league"]["name"],
        "season": state["league"]["season_label"],
        "home": home,
        "away": away,
        "kickoff": kickoff,
        "venue": (fixture or {}).get("venue"),
        "fixture_status": (fixture or {}).get("status"),
        "home_logo": lookup_crest(league_id, home) or (fixture or {}).get("home_logo"),
        "away_logo": lookup_crest(league_id, away) or (fixture or {}).get("away_logo"),
        "trained_on": state["trained_on"],
        "current_matches": state["current_matches"],
        "prior_matches": state["prior_matches"],
        "as_of": now.isoformat(),
        "freshness": freshness,
        "lineup": lineup,
        "xg_home": float(prediction.home_goal_expectation),
        "xg_away": float(prediction.away_goal_expectation),
        "xg_definition": (
            "Dixon–Coles team expected goals (λ), not observed shot-based xG."
        ),
        "home_win": float(prediction.home_win),
        "draw": float(prediction.draw),
        "away_win": float(prediction.away_win),
        "predicted_result": result_side,
        "predicted_result_label": result_label,
        "predicted_result_prob": result_prob,
        "btts": float(prediction.btts_yes),
        "over_15": float(prediction.total_goals("over", 1.5)),
        "over_25": float(prediction.total_goals("over", 2.5)),
        "over_35": float(prediction.total_goals("over", 3.5)),
        "under_25": float(prediction.total_goals("under", 2.5)),
        "exact_scores": [
            {"home": h, "away": a, "prob": p} for p, h, a in ranked[:5]
        ],
        "views": {
            "most_likely": {
                "objective": "Rank supported selections by model event probability.",
                "single_picks": singles,
                "combo_2leg": combo_bundle["combo_2leg"],
                "combo_3leg": combo_bundle["combo_3leg"],
                "combo_note": combo_bundle["combo_note"],
            },
            "best_value": {
                "objective": (
                    "Expected return using offered decimal odds. "
                    "Distinct from most-likely probability ranking."
                ),
                "status": "unavailable",
                "reason": "No bookmaker/provider odds feed with timestamp is configured.",
                "single_picks": [
                    {
                        **pick,
                        "fair_odds": pick.get("fair_odds"),
                        "market_odds": None,
                        "expected_value": None,
                        "value_status": "unavailable",
                    }
                    for pick in singles
                ],
            },
        },
        "single_picks": singles,
        "combos": [],
        "combo_2leg": combo_bundle["combo_2leg"],
        "combo_3leg": combo_bundle["combo_3leg"],
        "medium_risk": combo_bundle["combo_3leg"],
        "high_risk": None,
        "combo_note": combo_bundle["combo_note"],
        "grid_diagnostics": combo_bundle["diagnostics"],
        "recent_form": {"home": home_form, "away": away_form},
        "home_form": home_venue,
        "away_form": away_venue,
        "h2h": [
            {
                "date": row.date.date().isoformat(),
                "home": row.home,
                "away": row.away,
                "goals_home": int(row.goals_home),
                "goals_away": int(row.goals_away),
            }
            for row in h2h.itertuples()
        ],
        "h2h_note": (
            "Head-to-head is secondary context and is not a separate model weight."
        ),
        "players": None,
        "player_note": None,
        "player_source": None,
        "limitations": [item for item in limitations if item],
        "tracking_activated_at": forecast_store.tracking_activated_at().isoformat(),
        "settlement": None,
        **dixon_coles_meta(state["model"]),
        "inputs": [
            "penaltyblog DixonColesGoalModel",
            f"time-decay ξ={DEFAULT_XI}",
            "matches strictly before forecast cutoff",
            "football-data.co.uk / FotMob finished results",
        ],
        "inputs_not_in_model": [
            "recent-form summary cards",
            "head-to-head list",
            "lineups / injuries / suspensions",
            "bookmaker odds",
        ],
    }

    # Snapshot / settlement for dated fixtures only
    if fixture_id and kickoff and forecast_kind == "upcoming_fixture":
        save_info = forecast_store.save_live_forecast(fixture_id, result)
        result["forecast_saved"] = save_info
    elif fixture_id and kickoff:
        forecast_store.freeze_at_kickoff(fixture_id, kickoff)
        if fixture and fixture.get("finished") and fixture.get("score"):
            score = parse_score(fixture.get("score"))
            if score:
                settlement = forecast_store.settle_fixture(
                    fixture_id,
                    score[0],
                    score[1],
                    finished=True,
                    cancelled=bool(fixture.get("cancelled")),
                    postponed=bool(fixture.get("postponed")),
                )
                result["settlement"] = settlement
        frozen = forecast_store.get_frozen(fixture_id)
        if frozen and frozen.get("settlement"):
            result["settlement"] = frozen["settlement"]
        elif frozen:
            result["frozen_pre_kickoff"] = {
                "frozen_at": frozen.get("frozen_at"),
                "predicted_result_label": frozen.get("predicted_result_label"),
                "exact_scores": frozen.get("exact_scores"),
            }

    if forecast_kind == "hypothetical_matchup":
        result["forecast_kind_note"] = (
            "No scheduled meeting found for this pair in the loaded fixture list. "
            "Labeled as a hypothetical matchup."
        )

    _players_for_state(state)
    if state["player_error"]:
        result["player_note"] = f"Could not load player stats ({state['player_error']})."
        return result

    players = state["players"]
    names = sorted(players["team"].dropna().unique())
    home_team = match_team(home, names)
    away_team = match_team(away, names)
    if home_team is None or away_team is None:
        result["player_note"] = f"Could not map teams to player stats ({home} / {away})."
        return result

    result["player_source"] = f"FotMob {players['season'].iloc[0]}"
    result["players"] = {
        "home_scorers": players_as_dicts(
            player_picks(players, home_team, prediction.home_goal_expectation, "score")
        ),
        "away_scorers": players_as_dicts(
            player_picks(players, away_team, prediction.away_goal_expectation, "score")
        ),
        "home_assists": players_as_dicts(
            player_picks(
                players, home_team, prediction.home_goal_expectation, "assist", 2
            )
        ),
        "away_assists": players_as_dicts(
            player_picks(
                players, away_team, prediction.away_goal_expectation, "assist", 2
            )
        ),
    }
    return result
