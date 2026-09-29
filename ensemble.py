"""Small, auditable ensemble helpers for 1X2 football probabilities.

The score grid remains Dixon-Coles. This module adds a separate Elo-style
result model and an optional market-implied 1X2 signal when real odds exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

ELO_START = 1500.0
ELO_K = 26.0
ELO_HOME_ADVANTAGE = 65.0
DRAW_MIN = 0.18
DRAW_MAX = 0.34


@dataclass(frozen=True)
class EloResultModel:
    ratings: dict[str, float]
    avg_draw_rate: float
    trained_on: int
    latest_match_date: str | None
    params: dict[str, float]


def _outcome_score(home_goals: int, away_goals: int) -> float:
    if home_goals > away_goals:
        return 1.0
    if home_goals < away_goals:
        return 0.0
    return 0.5


def _binary_expectation(home_rating: float, away_rating: float, home_advantage: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-(home_rating + home_advantage - away_rating) / 400.0))


def fit_elo(matches: pd.DataFrame, *, k: float = ELO_K, home_advantage: float = ELO_HOME_ADVANTAGE) -> EloResultModel:
    train = matches.sort_values("date").copy()
    ratings: dict[str, float] = {}
    draws = 0
    latest = None
    for row in train.itertuples():
        home = str(row.home)
        away = str(row.away)
        hg, ag = int(row.goals_home), int(row.goals_away)
        ratings.setdefault(home, ELO_START)
        ratings.setdefault(away, ELO_START)
        expected = _binary_expectation(ratings[home], ratings[away], home_advantage)
        actual = _outcome_score(hg, ag)
        margin = abs(hg - ag)
        multiplier = 1.0 if margin <= 1 else math.log(margin + 1.0)
        delta = k * multiplier * (actual - expected)
        ratings[home] += delta
        ratings[away] -= delta
        draws += int(hg == ag)
        latest = pd.to_datetime(row.date, utc=True)
    avg_draw = draws / len(train) if len(train) else 0.26
    return EloResultModel(
        ratings=ratings,
        avg_draw_rate=float(np.clip(avg_draw, DRAW_MIN, DRAW_MAX)),
        trained_on=int(len(train)),
        latest_match_date=latest.date().isoformat() if latest is not None else None,
        params={"k": float(k), "home_advantage": float(home_advantage), "start_rating": ELO_START},
    )


def elo_1x2(model: EloResultModel, home: str, away: str) -> dict[str, Any]:
    home_rating = model.ratings.get(home, ELO_START)
    away_rating = model.ratings.get(away, ELO_START)
    binary = _binary_expectation(home_rating, away_rating, model.params["home_advantage"])
    rating_gap = abs(home_rating + model.params["home_advantage"] - away_rating) / 400.0
    closeness = math.exp(-1.35 * rating_gap)
    draw = float(np.clip(model.avg_draw_rate * (0.72 + 0.55 * closeness), DRAW_MIN, DRAW_MAX))
    remainder = 1.0 - draw
    home_win = remainder * binary
    away_win = remainder * (1.0 - binary)
    total = home_win + draw + away_win
    return {
        "home_win": float(home_win / total),
        "draw": float(draw / total),
        "away_win": float(away_win / total),
        "home_rating": float(home_rating),
        "away_rating": float(away_rating),
        "rating_gap": float(home_rating + model.params["home_advantage"] - away_rating),
        "trained_on": model.trained_on,
        "results_through": model.latest_match_date,
        "params": model.params,
    }


def market_1x2_from_offers(odds: dict[str, Any] | None) -> dict[str, Any] | None:
    offers = (odds or {}).get("offers") or {}
    needed = ["home_win", "draw", "away_win"]
    if not all(key in offers for key in needed):
        return None
    raw = {key: 1.0 / float(offers[key]["odds"]) for key in needed}
    total = sum(raw.values())
    if total <= 0:
        return None
    return {
        "home_win": raw["home_win"] / total,
        "draw": raw["draw"] / total,
        "away_win": raw["away_win"] / total,
        "overround": total - 1.0,
        "bookmakers": {
            key: offers[key].get("bookmaker")
            for key in needed
        },
    }


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def lineup_availability_1x2(impact_layer: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert available lineup strength into a conservative 1X2 signal.

    This uses FotMob/API-provided starter market values when both sides are
    present. It is intentionally low-information and should be blended at a
    small weight; it is not a trained player-impact model.
    """
    impact = impact_layer or {}
    lineups = impact.get("lineups") or {}
    teams = lineups.get("teams") or []
    if len(teams) < 2:
        return None
    values = []
    for team in teams[:2]:
        starters = team.get("starters") or []
        total = sum(float(row.get("market_value") or 0.0) for row in starters)
        if total <= 0:
            return None
        values.append({"team": team.get("team"), "starter_value": total})
    injuries = (impact.get("injuries") or {}).get("players") or []
    for item in values:
        unavailable_value = sum(
            float(row.get("market_value") or 0.0)
            for row in injuries
            if str(row.get("team") or "").lower() == str(item.get("team") or "").lower()
        )
        item["unavailable_value"] = unavailable_value
        item["adjusted_value"] = max(item["starter_value"] - 0.35 * unavailable_value, item["starter_value"] * 0.72)

    home_value, away_value = values[0]["adjusted_value"], values[1]["adjusted_value"]
    log_ratio = math.log((home_value + 1.0) / (away_value + 1.0))
    binary = _sigmoid(0.55 * log_ratio)
    closeness = math.exp(-abs(log_ratio))
    draw = float(np.clip(0.22 + 0.09 * closeness, DRAW_MIN, DRAW_MAX))
    remainder = 1.0 - draw
    home_win = remainder * binary
    away_win = remainder * (1.0 - binary)
    total = home_win + draw + away_win
    return {
        "home_win": float(home_win / total),
        "draw": float(draw / total),
        "away_win": float(away_win / total),
        "home_adjusted_value": float(home_value),
        "away_adjusted_value": float(away_value),
        "home_starter_value": float(values[0]["starter_value"]),
        "away_starter_value": float(values[1]["starter_value"]),
        "home_unavailable_value": float(values[0].get("unavailable_value") or 0.0),
        "away_unavailable_value": float(values[1].get("unavailable_value") or 0.0),
        "source": lineups.get("source"),
        "note": (
            "Experimental lineup/availability signal from starter market values and listed unavailable players. "
            "Used by the app as a capped score-grid adjustment when available; not a fully trained player model."
        ),
    }


def blend_1x2(
    dixon_coles: dict[str, float],
    elo: dict[str, Any] | None,
    market: dict[str, Any] | None = None,
    lineup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signals = [{"id": "dixon_coles", "label": "Dixon-Coles score grid", "weight": 0.70, "probs": dixon_coles}]
    if elo:
        signals.append({"id": "elo", "label": "Elo strength rating", "weight": 0.30, "probs": elo})
    if lineup:
        signals = [
            {"id": "dixon_coles", "label": "Dixon-Coles score grid", "weight": 0.62, "probs": dixon_coles},
            {"id": "elo", "label": "Elo strength rating", "weight": 0.28, "probs": elo},
            {"id": "lineup", "label": "Lineup availability", "weight": 0.10, "probs": lineup},
        ]
    if market:
        signals = [
            {"id": "dixon_coles", "label": "Dixon-Coles score grid", "weight": 0.45, "probs": dixon_coles},
            {"id": "elo", "label": "Elo strength rating", "weight": 0.20, "probs": elo},
            {"id": "lineup", "label": "Lineup availability", "weight": 0.10, "probs": lineup},
            {"id": "market", "label": "Market-implied odds", "weight": 0.25, "probs": market},
        ]
    signals = [signal for signal in signals if signal.get("probs")]
    weight_sum = sum(float(signal["weight"]) for signal in signals)
    blended = {}
    for key in ("home_win", "draw", "away_win"):
        blended[key] = sum(float(signal["weight"]) * float(signal["probs"][key]) for signal in signals) / weight_sum
    total = sum(blended.values())
    for key in blended:
        blended[key] = float(blended[key] / total)
    return {
        **blended,
        "signals": [
            {
                "id": signal["id"],
                "label": signal["label"],
                "weight": float(signal["weight"] / weight_sum),
            }
            for signal in signals
        ],
        "status": "experimental",
        "note": (
            "Headline 1X2 blend only. Exact score, totals, BTTS and combos still come "
            "from the Dixon-Coles score grid."
        ),
    }
