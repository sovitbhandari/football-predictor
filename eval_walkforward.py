"""Walk-forward comparison of Dixon–Coles time-decay settings.

Fits only on matches strictly before each simulated kickoff.
Reports 1X2 log loss and Brier on a later holdout period.
Does not invent form multipliers; extension C is documented as not fitted.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from engine import DEFAULT_XI, LEAGUES, arrays, league_by_id, load_league
from penaltyblog.models import DixonColesGoalModel, dixon_coles_weights


def _logloss(p: float) -> float:
    return -math.log(max(min(p, 1 - 1e-12), 1e-12))


def _brier(probs: tuple[float, float, float], outcome: int) -> float:
    onehot = [0.0, 0.0, 0.0]
    onehot[outcome] = 1.0
    return sum((probs[i] - onehot[i]) ** 2 for i in range(3))


def fit_cut(matches: pd.DataFrame, cutoff, xi: float):
    dates = pd.to_datetime(matches["date"], utc=True)
    trainable = matches.loc[dates < pd.Timestamp(cutoff)].copy()
    if len(trainable) < 80:
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
    return model, teams, len(trainable)


def evaluate_league(league_id: str = "laliga", xi_candidates=(0.0018, 0.0035, 0.0010), max_tune=40, max_holdout=40):
    league = league_by_id(league_id)
    matches = load_league(league).sort_values("date")
    matches["date"] = pd.to_datetime(matches["date"], utc=True)
    split = int(len(matches) * 0.8)
    tune_start = int(len(matches) * 0.65)
    tune = matches.iloc[tune_start:split].tail(max_tune)
    holdout = matches.iloc[split:].head(max_holdout)

    print(f"League: {league['name']}")
    print(f"Matches total={len(matches)} tune={len(tune)} holdout={len(holdout)}")
    print(f"Date range {matches['date'].min().date()} → {matches['date'].max().date()}")

    tune_scores = {}
    for xi in xi_candidates:
        losses = []
        briers = []
        n = 0
        for row in tune.itertuples():
            fitted = fit_cut(matches, row.date, xi)
            if fitted is None:
                continue
            model, teams, _ = fitted
            if row.home not in teams or row.away not in teams:
                continue
            pred = model.predict(row.home, row.away)
            probs = (float(pred.home_win), float(pred.draw), float(pred.away_win))
            outcome = 0 if row.goals_home > row.goals_away else 2 if row.goals_away > row.goals_home else 1
            losses.append(_logloss(probs[outcome]))
            briers.append(_brier(probs, outcome))
            n += 1
        tune_scores[xi] = {
            "n": n,
            "log_loss": float(np.mean(losses)) if losses else None,
            "brier": float(np.mean(briers)) if briers else None,
        }
        print(f"  tune xi={xi}: n={n} logloss={tune_scores[xi]['log_loss']} brier={tune_scores[xi]['brier']}")

    valid = {xi: s for xi, s in tune_scores.items() if s["log_loss"] is not None}
    best_xi = min(valid, key=lambda xi: valid[xi]["log_loss"]) if valid else DEFAULT_XI
    print(f"Selected xi on tune period: {best_xi}")

    results = {}
    for label, xi in (("A_baseline_default_xi", DEFAULT_XI), ("B_tuned_xi", best_xi)):
        losses = []
        briers = []
        exact_ll = []
        correct = 0
        n = 0
        for row in holdout.itertuples():
            fitted = fit_cut(matches, row.date, xi)
            if fitted is None:
                continue
            model, teams, _ = fitted
            if row.home not in teams or row.away not in teams:
                continue
            pred = model.predict(row.home, row.away)
            probs = (float(pred.home_win), float(pred.draw), float(pred.away_win))
            outcome = 0 if row.goals_home > row.goals_away else 2 if row.goals_away > row.goals_home else 1
            losses.append(_logloss(probs[outcome]))
            briers.append(_brier(probs, outcome))
            grid = pred.grid
            gh, ga = int(row.goals_home), int(row.goals_away)
            if gh < grid.shape[0] and ga < grid.shape[1]:
                exact_ll.append(_logloss(float(grid[gh, ga])))
            tip = int(np.argmax(probs))
            correct += int(tip == outcome)
            n += 1
        results[label] = {
            "xi": xi,
            "n": n,
            "log_loss": float(np.mean(losses)) if losses else None,
            "brier": float(np.mean(briers)) if briers else None,
            "exact_score_log_loss": float(np.mean(exact_ll)) if exact_ll else None,
            "result_accuracy": (correct / n) if n else None,
        }
        print(f"  holdout {label}: {results[label]}")

    results["C_form_extension"] = {
        "status": "not_fitted",
        "reason": (
            "No validated form→λ mapping without double-counting time-decay. "
            "Recent-form cards are display-only until a walk-forward gain is shown."
        ),
    }
    results["lineup_extension"] = {
        "status": "not_fitted",
        "reason": "No historical lineup/availability feed sufficient to estimate and validate effects.",
    }
    results["market_odds"] = {
        "status": "unavailable",
        "reason": "No timestamp-compatible closing odds in the training CSVs for this run.",
    }
    return results


if __name__ == "__main__":
    evaluate_league("laliga")
