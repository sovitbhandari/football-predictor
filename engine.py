import hashlib
import io
import json
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from penaltyblog.models import DixonColesGoalModel, dixon_coles_weights

USER_AGENT = "Mozilla/5.0"
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
}

_CACHE: OrderedDict[str, dict] = OrderedDict()
_CRESTS = {}
_HTTP_MEM = {}
_FIXTURE_MEM = {}
_LIVE_BOARD = {"expires": 0.0, "rows": []}
MAX_LEAGUE_CACHE = 2
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
    response = _SESSION.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.content
    path.write_bytes(data)
    return _mem_set(key, data, ttl)


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


def map_to_catalog(name: str, catalog: list[str]) -> str | None:
    folded = fold_name(name)
    hinted = TEAM_HINTS.get(folded, folded)
    exact = [item for item in catalog if fold_name(item) in {folded, hinted}]
    if len(exact) == 1:
        return exact[0]
    via_hint = match_team(hinted, catalog)
    if via_hint:
        return via_hint
    via_name = match_team(name, catalog)
    if via_name:
        return via_name
    query_tokens = set(folded.split())
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
    return {
        "home": home_name,
        "away": away_name,
        "home_source": home,
        "away_source": away,
        "home_short": match["home"].get("shortName") or home_name,
        "away_short": match["away"].get("shortName") or away_name,
        "home_logo": home_logo,
        "away_logo": away_logo,
        "kickoff": kickoff.isoformat() if kickoff else None,
        "status": "live" if live else "finished" if finished else "scheduled",
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


def multi_leg_picks(prediction, home: str, away: str) -> tuple[dict, dict]:
    grid = prediction.grid
    home_goals, away_goals = np.indices(grid.shape)
    total = home_goals + away_goals
    home_win, draw, away_win = (
        float(prediction.home_win),
        float(prediction.draw),
        float(prediction.away_win),
    )

    if home_win >= away_win and home_win >= draw:
        result = (f"{home} win", home_goals > away_goals)
        favorite = home
        favorite_goals = home_goals
    elif away_win >= home_win and away_win >= draw:
        result = (f"{away} win", home_goals < away_goals)
        favorite = away
        favorite_goals = away_goals
    else:
        result = ("Draw", home_goals == away_goals)
        favorite = None
        favorite_goals = None

    btts_mask = (home_goals > 0) & (away_goals > 0)
    btts = (
        ("BTTS — Yes", btts_mask)
        if float(prediction.btts_yes) >= 0.5
        else ("BTTS — No", ~btts_mask)
    )
    over_25 = float(prediction.total_goals("over", 2.5))
    totals = (
        ("Over 2.5 Goals", total > 2.5)
        if over_25 >= 0.5
        else ("Under 2.5 Goals", total < 2.5)
    )

    three = [result, btts, totals]
    three_mask = three[0][1] & three[1][1] & three[2][1]
    medium = {
        "legs": [item[0] for item in three],
        "label": " + ".join(item[0] for item in three),
        "prob": float(grid[three_mask].sum()),
        "n": 3,
        "risk": "MEDIUM",
    }

    if totals[0] == "Over 2.5 Goals":
        fourth = ("Over 3.5 Goals", total > 3.5)
    elif btts[0] == "BTTS no" and favorite is not None:
        clean_sheet = away_goals == 0 if favorite == home else home_goals == 0
        fourth = (f"{favorite} win to nil", result[1] & clean_sheet)
    elif favorite is not None:
        fourth = (f"{favorite} over 1.5 goals", favorite_goals > 1.5)
    else:
        _prob, goals_h, goals_a = top_scores(prediction, 1)[0]
        fourth = (
            f"Exact {goals_h}-{goals_a}",
            (home_goals == goals_h) & (away_goals == goals_a),
        )

    four_mask = three_mask & fourth[1]
    high = {
        "legs": [item[0] for item in three] + [fourth[0]],
        "label": " + ".join([item[0] for item in three] + [fourth[0]]),
        "prob": float(grid[four_mask].sum()),
        "n": 4,
        "risk": "HIGH",
    }
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


def venue_stats(matches: pd.DataFrame, team: str, venue: str) -> dict:
    if venue == "home":
        games = matches[matches["home"] == team]
        scored, conceded = games["goals_home"], games["goals_away"]
    else:
        games = matches[matches["away"] == team]
        scored, conceded = games["goals_away"], games["goals_home"]
    if games.empty:
        return {"team": team, "venue": venue, "games": 0, "scored": 0.0, "conceded": 0.0}
    return {
        "team": team,
        "venue": venue,
        "games": int(len(games)),
        "scored": float(scored.mean()),
        "conceded": float(conceded.mean()),
    }


def fit_league(league: dict) -> dict:
    matches = load_league(league)
    if matches.empty:
        raise ValueError(f"No completed matches found for {league['name']}.")
    current = matches[matches["season"] == league["season"]]
    if current.empty:
        raise ValueError(
            f"No {league['season_label']} matches found for {league['name']} yet."
        )
    weights = np.array(
        dixon_coles_weights(matches["date"].tolist()),
        dtype=float,
        copy=True,
    )
    model = DixonColesGoalModel(
        *arrays(
            matches["goals_home"].to_numpy(dtype=int),
            matches["goals_away"].to_numpy(dtype=int),
            matches["home"].to_numpy(dtype=str),
            matches["away"].to_numpy(dtype=str),
            weights,
        )
    )
    model.fit()
    teams = sorted(set(current["home"]) | set(current["away"]))
    return {
        "league": league,
        "matches": matches,
        "current": current,
        "teams": teams,
        "model": model,
        "players": None,
        "player_error": None,
        "trained_on": len(matches),
        "current_matches": len(current),
        "prior_matches": len(matches) - len(current),
    }


def get_league_state(league_id: str) -> dict:
    with _LOCK:
        cached = _CACHE.get(league_id)
        if cached is not None:
            _CACHE.move_to_end(league_id)
            return cached
        event = _FIT_EVENTS.get(league_id)
        if event is None:
            event = threading.Event()
            _FIT_EVENTS[league_id] = event
            owner = True
        else:
            owner = False
    if not owner:
        event.wait(timeout=120)
        return _CACHE[league_id]
    try:
        state = fit_league(league_by_id(league_id))
        with _LOCK:
            _CACHE[league_id] = state
            _CACHE.move_to_end(league_id)
            while len(_CACHE) > MAX_LEAGUE_CACHE:
                old_id, _ = _CACHE.popitem(last=False)
                _FIT_EVENTS.pop(old_id, None)
        threading.Thread(target=_warmup_players, args=(league_id,), daemon=True).start()
        return state
    finally:
        event.set()


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


def build_prediction(league_id: str, home: str, away: str) -> dict:
    state = get_league_state(league_id)
    teams = state["teams"]
    if home not in teams or away not in teams:
        raise ValueError("Use team names exactly as listed for this league.")
    if home == away:
        raise ValueError("Choose two different teams.")

    prediction = state["model"].predict(home, away)
    ranked = top_scores(prediction)
    combos = combo_markets(prediction, home, away)[:2]
    two_leg = best_two_leg(prediction, home, away)
    medium, high = multi_leg_picks(prediction, home, away)
    singles = rank_single_picks(prediction, home, away)
    result_side, result_label, result_prob = predicted_1x2(prediction, home, away)
    matches = state["matches"]
    h2h = matches[
        ((matches["home"] == home) & (matches["away"] == away))
        | ((matches["home"] == away) & (matches["away"] == home))
    ].sort_values("date")
    now = datetime.now(timezone.utc)

    result = {
        "league_id": league_id,
        "league": state["league"]["name"],
        "season": state["league"]["season_label"],
        "home": home,
        "away": away,
        "home_logo": lookup_crest(league_id, home),
        "away_logo": lookup_crest(league_id, away),
        "trained_on": state["trained_on"],
        "current_matches": state["current_matches"],
        "prior_matches": state["prior_matches"],
        "as_of": now.isoformat(),
        "xg_home": float(prediction.home_goal_expectation),
        "xg_away": float(prediction.away_goal_expectation),
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
        "single_picks": singles,
        "combos": [{"label": label, "prob": prob} for label, prob in combos],
        "combo_2leg": two_leg,
        "medium_risk": medium,
        "high_risk": high,
        "home_form": venue_stats(matches, home, "home"),
        "away_form": venue_stats(matches, away, "away"),
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
        "players": None,
        "player_note": None,
        "player_source": None,
        "model": "Dixon–Coles",
        "inputs": [
            "Recent match results",
            "Home/away strength",
            "Goals for/against",
            "FotMob player xG/xA",
        ],
    }

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
