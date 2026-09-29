"""Optional API-Football enrichment provider.

The prediction model remains penaltyblog Dixon-Coles. This module only adds
external context such as provider fixture identity and market odds when a local
API_FOOTBALL_KEY is configured.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env.local"
CACHE_DIR = ROOT / ".cache" / "api-football"
BASE_URL = "https://v3.football.api-sports.io"
USER_AGENT = "football-predictor-local/1.0"

FIXTURE_TTL = 6 * 3600
ODDS_TTL = 20 * 60
LINEUP_TTL = 10 * 60
INJURY_TTL = 20 * 60


API_FOOTBALL_LEAGUES: dict[str, int] = {
    "premier-league": 39,
    "championship": 40,
    "efl-cup": 48,
    "fa-cup": 45,
    "ucl": 2,
    "uel": 3,
    "uecl": 848,
    "laliga": 140,
    "copa-del-rey": 143,
    "serie-a": 135,
    "bundesliga": 78,
    "ligue-1": 61,
    "brasileirao": 71,
    "primeira-liga": 94,
    "eredivisie": 88,
    "argentina": 128,
    "mls": 253,
}

TEAM_ALIASES = {
    "ath madrid": "atletico madrid",
    "atl madrid": "atletico madrid",
    "atletico": "atletico madrid",
    "espanol": "espanyol",
    "inter miami cf": "inter miami",
    "nycfc": "new york city fc",
    "ny red bulls": "new york red bulls",
    "bayern munchen": "bayern munich",
    "fc koln": "koln",
}


def _load_local_env() -> None:
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def api_key() -> str | None:
    _load_local_env()
    return os.getenv("API_FOOTBALL_KEY") or os.getenv("APISPORTS_KEY")


def status() -> dict[str, Any]:
    return {
        "provider": "API-Football",
        "configured": bool(api_key()),
        "league_mappings": len(API_FOOTBALL_LEAGUES),
    }


def fold_name(name: str | None) -> str:
    text = unicodedata.normalize("NFD", str(name or ""))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).lower().strip()
    text = re.sub(r"\b(fc|cf|sc|afc|club|de|the)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return TEAM_ALIASES.get(text, text)


def _cache_key(path: str, params: dict[str, Any]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    packed = path + "?" + json.dumps(params, sort_keys=True)
    digest = hashlib.sha256(packed.encode()).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def _request(path: str, params: dict[str, Any], ttl: int) -> dict[str, Any]:
    key = api_key()
    if not key:
        raise RuntimeError("API_FOOTBALL_KEY is not configured")
    cache_path = _cache_key(path, params)
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < ttl:
        return json.loads(cache_path.read_text())
    response = requests.get(
        f"{BASE_URL}{path}",
        params=params,
        headers={"x-apisports-key": key, "User-Agent": USER_AGENT},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    cache_path.write_text(json.dumps(data))
    return data


def _season_from_kickoff(kickoff: str | None) -> int:
    if not kickoff:
        return datetime.now(timezone.utc).year
    try:
        stamp = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc).year
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).year


def _date_from_kickoff(kickoff: str | None) -> str | None:
    if not kickoff:
        return None
    try:
        stamp = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).date().isoformat()


def _team_score(a: str, b: str) -> float:
    fa, fb = fold_name(a), fold_name(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 1.0
    ta, tb = set(fa.split()), set(fb.split())
    if not ta or not tb:
        return 0.0
    if ta <= tb or tb <= ta:
        return 0.86
    return len(ta & tb) / len(ta | tb)


def find_fixture(league_id: str, home: str, away: str, kickoff: str | None) -> dict[str, Any]:
    if league_id not in API_FOOTBALL_LEAGUES:
        return {
            "available": False,
            "reason": "Competition is not mapped for API-Football yet.",
        }
    date = _date_from_kickoff(kickoff)
    if not date:
        return {"available": False, "reason": "Kickoff date is unavailable."}
    params = {
        "league": API_FOOTBALL_LEAGUES[league_id],
        "season": _season_from_kickoff(kickoff),
        "date": date,
    }
    payload = _request("/fixtures", params, FIXTURE_TTL)
    best = None
    best_score = 0.0
    for row in payload.get("response") or []:
        teams = row.get("teams") or {}
        api_home = (teams.get("home") or {}).get("name") or ""
        api_away = (teams.get("away") or {}).get("name") or ""
        score = (_team_score(home, api_home) + _team_score(away, api_away)) / 2
        if score > best_score:
            best = row
            best_score = score
    if not best or best_score < 0.58:
        return {
            "available": False,
            "reason": "No matching API-Football fixture found for the teams/date.",
            "checked_date": date,
        }
    return {
        "available": True,
        "fixture": best,
        "match_score": best_score,
        "api_fixture_id": ((best.get("fixture") or {}).get("id")),
    }


def _decimal(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 1.0 else None


def _offer_key(bet_name: str, value: str) -> str | None:
    bet = fold_name(bet_name)
    val = fold_name(value)
    if "match winner" in bet or bet == "winner":
        if val == "home":
            return "home_win"
        if val == "draw":
            return "draw"
        if val == "away":
            return "away_win"
    if "double chance" in bet:
        if val in {"home draw", "1x"}:
            return "dc_1x"
        if val in {"away draw", "x2"}:
            return "dc_x2"
        if val in {"home away", "12"}:
            return "dc_12"
    if "both teams" in bet or "btts" in bet:
        if val == "yes":
            return "btts_yes"
        if val == "no":
            return "btts_no"
    if "goals over under" in bet or "over under" in bet or "goals" in bet:
        compact = val.replace(" ", "_")
        for line in ("1_5", "2_5", "3_5"):
            if f"over_{line}" == compact:
                return f"over_{line.replace('_', '')}"
            if f"under_{line}" == compact:
                return f"under_{line.replace('_', '')}"
    return None


def fixture_odds(api_fixture_id: int) -> dict[str, Any]:
    payload = _request("/odds", {"fixture": api_fixture_id}, ODDS_TTL)
    offers: dict[str, dict[str, Any]] = {}
    updates = []
    for event in payload.get("response") or []:
        if event.get("update"):
            updates.append(event["update"])
        for bookmaker in event.get("bookmakers") or []:
            book = bookmaker.get("name") or str(bookmaker.get("id") or "bookmaker")
            for bet in bookmaker.get("bets") or []:
                bet_name = bet.get("name") or ""
                for outcome in bet.get("values") or []:
                    key = _offer_key(bet_name, str(outcome.get("value") or ""))
                    odd = _decimal(outcome.get("odd"))
                    if not key or odd is None:
                        continue
                    current = offers.get(key)
                    if current is None or odd > current["odds"]:
                        offers[key] = {
                            "odds": odd,
                            "bookmaker": book,
                            "market": bet_name,
                            "label": outcome.get("value"),
                        }
    return {
        "available": bool(offers),
        "offers": offers,
        "last_update": max(updates) if updates else None,
        "raw_events": len(payload.get("response") or []),
    }


def fixture_lineups(api_fixture_id: int) -> dict[str, Any]:
    payload = _request("/fixtures/lineups", {"fixture": api_fixture_id}, LINEUP_TTL)
    rows = payload.get("response") or []
    return {
        "available": bool(rows),
        "teams": [
            {
                "team": ((row.get("team") or {}).get("name")),
                "formation": row.get("formation"),
                "players": [
                    ((item.get("player") or {}).get("name"))
                    for item in (row.get("startXI") or [])[:11]
                    if item.get("player")
                ],
            }
            for row in rows
        ],
    }


def fixture_injuries(api_fixture_id: int) -> dict[str, Any]:
    payload = _request("/injuries", {"fixture": api_fixture_id}, INJURY_TTL)
    rows = payload.get("response") or []
    return {
        "available": bool(rows),
        "players": [
            {
                "team": ((row.get("team") or {}).get("name")),
                "player": ((row.get("player") or {}).get("name")),
                "type": ((row.get("player") or {}).get("type")),
                "reason": ((row.get("player") or {}).get("reason")),
            }
            for row in rows
        ],
    }


def enrich_fixture(league_id: str, home: str, away: str, kickoff: str | None) -> dict[str, Any]:
    base = {
        "provider": "API-Football",
        "configured": bool(api_key()),
        "fixture": None,
        "odds": None,
        "lineups": None,
        "injuries": None,
        "errors": [],
    }
    if not base["configured"]:
        base["status"] = "missing_key"
        base["note"] = "Set API_FOOTBALL_KEY to enable odds/lineup enrichment."
        return base
    try:
        fixture = find_fixture(league_id, home, away, kickoff)
        base["fixture"] = fixture
        if not fixture.get("available"):
            base["status"] = "fixture_not_matched"
            base["note"] = fixture.get("reason")
            return base
        fixture_id = int(fixture["api_fixture_id"])
        try:
            base["odds"] = fixture_odds(fixture_id)
        except Exception as error:
            base["errors"].append(f"odds: {error}")
        try:
            base["lineups"] = fixture_lineups(fixture_id)
        except Exception as error:
            base["errors"].append(f"lineups: {error}")
        try:
            base["injuries"] = fixture_injuries(fixture_id)
        except Exception as error:
            base["errors"].append(f"injuries: {error}")
        base["status"] = "ok" if not base["errors"] else "partial"
        return base
    except Exception as error:
        base["status"] = "error"
        base["note"] = str(error)
        return base


def apply_odds_to_picks(picks: list[dict[str, Any]], odds: dict[str, Any] | None) -> list[dict[str, Any]]:
    offers = (odds or {}).get("offers") or {}
    enriched = []
    for pick in picks:
        item = dict(pick)
        offer = offers.get(item.get("id"))
        if offer:
            market_odds = float(offer["odds"])
            prob = float(item.get("prob") or 0.0)
            item["market_odds"] = market_odds
            item["bookmaker"] = offer.get("bookmaker")
            item["market_label"] = offer.get("market")
            item["expected_value"] = prob * market_odds - 1.0
            item["value_status"] = "positive" if item["expected_value"] > 0 else "negative"
            item["explain"] = (
                f"{item.get('explain', '').rstrip()} Market {market_odds:.2f} from "
                f"{offer.get('bookmaker')}; EV {item['expected_value']:.1%}."
            ).strip()
        enriched.append(item)
    return sorted(
        enriched,
        key=lambda pick: (
            pick.get("expected_value") is not None,
            pick.get("expected_value") if pick.get("expected_value") is not None else -999,
            pick.get("prob") or 0,
        ),
        reverse=True,
    )
