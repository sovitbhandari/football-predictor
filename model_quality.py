"""Walk-forward model quality checks for Match Grid.

This module intentionally does not change live forecasts. It evaluates candidate
Dixon-Coles settings on historical cutoffs so model changes can be justified by
out-of-sample evidence instead of by numbers that merely look better.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
import penaltyblog
from penaltyblog.models import DixonColesGoalModel, dixon_coles_weights

import ensemble
from engine import DEFAULT_XI, LEAGUES, arrays, league_by_id, load_league

EPS = 1e-12
DEFAULT_XI_CANDIDATES = (0.0008, DEFAULT_XI, 0.0035, 0.006)


@dataclass(frozen=True)
class EvalConfig:
    xi_candidates: tuple[float, ...] = DEFAULT_XI_CANDIDATES
    tune_matches: int = 18
    holdout_matches: int = 24
    min_train_matches: int = 80
    calibration_bins: int = 5


def _clip_prob(value: float) -> float:
    return max(min(float(value), 1.0 - EPS), EPS)


def _log_loss(prob: float) -> float:
    return -math.log(_clip_prob(prob))


def _brier(probs: tuple[float, float, float], outcome_idx: int) -> float:
    one_hot = [0.0, 0.0, 0.0]
    one_hot[outcome_idx] = 1.0
    return float(sum((probs[idx] - one_hot[idx]) ** 2 for idx in range(3)))


def _outcome(row) -> tuple[int, str]:
    if int(row.goals_home) > int(row.goals_away):
        return 0, "home"
    if int(row.goals_away) > int(row.goals_home):
        return 2, "away"
    return 1, "draw"


def _pack_metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "n": 0,
            "log_loss": None,
            "brier": None,
            "exact_score_log_loss": None,
            "result_accuracy": None,
            "avg_confidence": None,
        }
    return {
        "n": len(rows),
        "log_loss": float(np.mean([row["log_loss"] for row in rows])),
        "brier": float(np.mean([row["brier"] for row in rows])),
        "exact_score_log_loss": float(np.mean([row["exact_score_log_loss"] for row in rows if row["exact_score_log_loss"] is not None]))
        if any(row["exact_score_log_loss"] is not None for row in rows)
        else None,
        "result_accuracy": float(np.mean([row["predicted_idx"] == row["outcome_idx"] for row in rows])),
        "avg_confidence": float(np.mean([row["predicted_prob"] for row in rows])),
    }


def _calibration(rows: list[dict[str, Any]], bins: int) -> list[dict[str, Any]]:
    if not rows:
        return []
    edges = np.linspace(0.0, 1.0, bins + 1)
    packed = []
    for idx in range(bins):
        low = float(edges[idx])
        high = float(edges[idx + 1])
        if idx == bins - 1:
            bucket = [row for row in rows if low <= row["predicted_prob"] <= high]
        else:
            bucket = [row for row in rows if low <= row["predicted_prob"] < high]
        if not bucket:
            packed.append({"range": f"{low:.0%}-{high:.0%}", "n": 0, "avg_confidence": None, "hit_rate": None})
            continue
        packed.append(
            {
                "range": f"{low:.0%}-{high:.0%}",
                "n": len(bucket),
                "avg_confidence": float(np.mean([row["predicted_prob"] for row in bucket])),
                "hit_rate": float(np.mean([row["predicted_idx"] == row["outcome_idx"] for row in bucket])),
            }
        )
    return packed


def _fit_cut(matches: pd.DataFrame, cutoff: pd.Timestamp, xi: float, min_train_matches: int):
    trainable = matches.loc[matches["date"] < cutoff].copy()
    if len(trainable) < min_train_matches:
        return None
    teams = set(trainable["home"]) | set(trainable["away"])
    weights = np.array(dixon_coles_weights(trainable["date"].tolist(), xi=xi), dtype=float)
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
    return model, teams, len(trainable), pd.to_datetime(trainable["date"], utc=True).max(), trainable


def _evaluate_rows(
    matches: pd.DataFrame,
    rows: pd.DataFrame,
    xi: float,
    config: EvalConfig,
    *,
    use_ensemble: bool = False,
) -> list[dict[str, Any]]:
    evaluated: list[dict[str, Any]] = []
    for row in rows.itertuples():
        fitted = _fit_cut(matches, row.date, xi, config.min_train_matches)
        if fitted is None:
            continue
        model, teams, trained_on, latest_date, trainable = fitted
        if row.home not in teams or row.away not in teams:
            continue
        try:
            pred = model.predict(row.home, row.away, max_goals=15, normalize=True)
        except ValueError:
            continue
        probs = (float(pred.home_win), float(pred.draw), float(pred.away_win))
        signal = "dixon_coles"
        if use_ensemble:
            try:
                elo_model = ensemble.fit_elo(trainable)
                elo_probs = ensemble.elo_1x2(elo_model, row.home, row.away)
                blended = ensemble.blend_1x2(
                    {"home_win": probs[0], "draw": probs[1], "away_win": probs[2]},
                    elo_probs,
                    None,
                )
                probs = (
                    float(blended["home_win"]),
                    float(blended["draw"]),
                    float(blended["away_win"]),
                )
                signal = "dixon_coles_elo"
            except Exception:
                signal = "dixon_coles"
        outcome_idx, outcome = _outcome(row)
        predicted_idx = int(np.argmax(probs))
        grid = np.asarray(pred.grid, dtype=float)
        gh, ga = int(row.goals_home), int(row.goals_away)
        exact_ll = _log_loss(float(grid[gh, ga])) if gh < grid.shape[0] and ga < grid.shape[1] else None
        evaluated.append(
            {
                "date": row.date.isoformat(),
                "home": row.home,
                "away": row.away,
                "score": f"{gh}-{ga}",
                "outcome": outcome,
                "outcome_idx": outcome_idx,
                "predicted_idx": predicted_idx,
                "predicted_label": ("home", "draw", "away")[predicted_idx],
                "predicted_prob": probs[predicted_idx],
                "actual_prob": probs[outcome_idx],
                "home_win": probs[0],
                "draw": probs[1],
                "away_win": probs[2],
                "model_signal": signal,
                "xg_home": float(pred.home_goal_expectation),
                "xg_away": float(pred.away_goal_expectation),
                "log_loss": _log_loss(probs[outcome_idx]),
                "brier": _brier(probs, outcome_idx),
                "exact_score_log_loss": exact_ll,
                "trained_on": trained_on,
                "results_through": latest_date.date().isoformat(),
            }
        )
    return evaluated


def _load_completed(league_id: str) -> tuple[dict[str, Any], pd.DataFrame]:
    league = league_by_id(league_id)
    matches = load_league(league).sort_values("date").copy()
    if matches.empty:
        raise ValueError(f"No completed matches found for {league['name']}.")
    matches["date"] = pd.to_datetime(matches["date"], utc=True)
    matches = matches.dropna(subset=["goals_home", "goals_away", "home", "away"])
    return league, matches


@lru_cache(maxsize=32)
def evaluate_league_cached(
    league_id: str,
    xi_candidates: tuple[float, ...] = DEFAULT_XI_CANDIDATES,
    tune_matches: int = 18,
    holdout_matches: int = 24,
    min_train_matches: int = 80,
) -> dict[str, Any]:
    config = EvalConfig(
        xi_candidates=xi_candidates,
        tune_matches=tune_matches,
        holdout_matches=holdout_matches,
        min_train_matches=min_train_matches,
    )
    league, matches = _load_completed(league_id)
    needed = config.tune_matches + config.holdout_matches
    if len(matches) <= config.min_train_matches + max(8, needed):
        raise ValueError(
            f"Not enough completed matches to evaluate {league['name']} safely "
            f"({len(matches)} available, min train {config.min_train_matches})."
        )
    eval_pool = matches.tail(needed)
    tune_rows = eval_pool.head(config.tune_matches)
    holdout_rows = eval_pool.tail(config.holdout_matches)

    tune: dict[str, Any] = {}
    for xi in config.xi_candidates:
        rows = _evaluate_rows(matches, tune_rows, xi, config)
        tune[str(xi)] = {"xi": xi, **_pack_metric(rows)}

    valid = [item for item in tune.values() if item["log_loss"] is not None and item["n"] >= max(5, config.tune_matches // 3)]
    selected_xi = float(min(valid, key=lambda item: item["log_loss"])["xi"]) if valid else DEFAULT_XI
    baseline_rows = _evaluate_rows(matches, holdout_rows, DEFAULT_XI, config)
    tuned_rows = _evaluate_rows(matches, holdout_rows, selected_xi, config)
    ensemble_rows = _evaluate_rows(matches, holdout_rows, DEFAULT_XI, config, use_ensemble=True)
    baseline = {"label": "Current baseline", "xi": DEFAULT_XI, **_pack_metric(baseline_rows)}
    tuned = {"label": "Best tuned decay", "xi": selected_xi, **_pack_metric(tuned_rows)}
    ensemble_result = {
        "label": "Dixon-Coles + Elo ensemble",
        "xi": DEFAULT_XI,
        "signals": ["Dixon-Coles score grid", "Elo strength rating"],
        **_pack_metric(ensemble_rows),
    }

    improvement = None
    if baseline["log_loss"] is not None and tuned["log_loss"] is not None:
        improvement = baseline["log_loss"] - tuned["log_loss"]
    ensemble_improvement = None
    if baseline["log_loss"] is not None and ensemble_result["log_loss"] is not None:
        ensemble_improvement = baseline["log_loss"] - ensemble_result["log_loss"]

    recommendation = "keep_baseline"
    recommendation_note = (
        "Keep the current live model settings until a candidate shows a clear holdout improvement."
    )
    if ensemble_improvement is not None and ensemble_improvement > 0.015 and ensemble_result["n"] >= 15:
        recommendation = "ensemble_candidate_improved"
        recommendation_note = (
            "The Dixon-Coles + Elo ensemble beat the current baseline on this holdout sample. "
            "Keep checking multiple leagues before treating it as universally better."
        )
    elif improvement is not None and improvement > 0.015 and tuned["n"] >= 15:
        recommendation = "candidate_improved"
        recommendation_note = (
            "The tuned decay beat the current baseline on this holdout sample. "
            "Validate across more leagues before changing production forecasts."
        )
    elif improvement is not None and improvement < -0.015:
        recommendation_note = "The tuned decay was worse on holdout, so the current baseline should stay."

    return {
        "league": {
            "id": league["id"],
            "name": league["name"],
            "country": league["country"],
            "season": league.get("season_label"),
        },
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "model": "Dixon-Coles",
        "model_class": "penaltyblog.models.dixon_coles.DixonColesGoalModel",
        "penaltyblog_version": penaltyblog.__version__,
        "match_count": int(len(matches)),
        "date_range": {
            "start": matches["date"].min().date().isoformat(),
            "end": matches["date"].max().date().isoformat(),
        },
        "latest_completed_match_included": matches["date"].max().date().isoformat(),
        "freshness_note": (
            "Model Lab evaluates only completed matches already present in the local/source data. "
            "Matches finished after this included date will appear after the source updates and the check is refreshed."
        ),
        "config": {
            "xi_candidates": list(config.xi_candidates),
            "tune_matches": config.tune_matches,
            "holdout_matches": config.holdout_matches,
            "min_train_matches": config.min_train_matches,
            "calibration_bins": config.calibration_bins,
        },
        "tune": list(tune.values()),
        "selected_xi": selected_xi,
        "holdout": [baseline, tuned, ensemble_result],
        "ensemble": ensemble_result,
        "ensemble_improvement_log_loss": ensemble_improvement,
        "calibration": {
            "baseline": _calibration(baseline_rows, config.calibration_bins),
            "tuned": _calibration(tuned_rows, config.calibration_bins),
            "ensemble": _calibration(ensemble_rows, config.calibration_bins),
        },
        "sample_predictions": baseline_rows[-8:],
        "recommendation": recommendation,
        "recommendation_note": recommendation_note,
        "extension_status": {
            "recent_form": (
                "Displayed as context only. No form-to-goals adjustment is applied until "
                "walk-forward validation shows an incremental gain without double-counting time decay."
            ),
            "lineups": (
                "Unavailable as a numeric feature because this project does not yet have a validated "
                "historical lineup/availability feed."
            ),
            "odds_value": (
                "API-Football odds can power value comparison and, when all 1X2 prices are matched, "
                "a market-implied ensemble signal. Value still needs real prices with timestamps."
            ),
        },
    }


def evaluate_league(league_id: str, config: EvalConfig | None = None) -> dict[str, Any]:
    cfg = config or EvalConfig()
    return evaluate_league_cached(
        league_id,
        tuple(cfg.xi_candidates),
        int(cfg.tune_matches),
        int(cfg.holdout_matches),
        int(cfg.min_train_matches),
    )


def evaluation_overview(limit_leagues: int = 6) -> dict[str, Any]:
    rows = []
    errors = []
    for league in LEAGUES[:limit_leagues]:
        try:
            result = evaluate_league(league["id"])
            baseline = result["holdout"][0]
            tuned = result["holdout"][1]
            rows.append(
                {
                    "league": result["league"],
                    "baseline": baseline,
                    "tuned": tuned,
                    "ensemble": result.get("ensemble"),
                    "selected_xi": result["selected_xi"],
                    "recommendation": result["recommendation"],
                }
            )
        except Exception as error:
            errors.append({"league": league["id"], "error": str(error)})
    return {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "items": rows,
        "errors": errors,
    }
