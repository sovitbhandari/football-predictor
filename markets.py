"""Score-grid markets and joint combo selection.

Every probability is a sum of cells on the Dixon–Coles score grid.
Same-match combo legs are never multiplied.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np

# Supported half-goal lines only — no extreme soft lines for inflated probabilities.
TOTAL_LINES = (1.5, 2.5, 3.5)
TEAM_LINES = (1.5,)
# Skip near-certain markets when ranking display selections.
SOFT_PROB_CAP = 0.75
HARD_PROB_FLOOR = 0.15


def grid_diagnostics(grid: np.ndarray) -> dict[str, Any]:
    arr = np.asarray(grid, dtype=float)
    finite = bool(np.isfinite(arr).all())
    nonnegative = bool((arr >= -1e-12).all())
    total = float(arr.sum()) if finite else float("nan")
    return {
        "shape": list(arr.shape),
        "sum": total,
        "finite": finite,
        "nonnegative": nonnegative,
        "normalized": bool(finite and abs(total - 1.0) < 1e-6),
        "max_cell": float(arr.max()) if arr.size and finite else None,
        "tail_note": (
            "Grid covers 0..max_goals inclusive per team; "
            "penaltyblog default max_goals=15 with normalize=True."
        ),
    }


def event_prob(grid: np.ndarray, mask: np.ndarray) -> float:
    return float(np.asarray(grid, dtype=float)[mask].sum())


def supported_legs(prediction, home: str, away: str) -> list[dict]:
    grid = prediction.grid
    home_goals, away_goals = np.indices(grid.shape)
    total = home_goals + away_goals
    home_win = home_goals > away_goals
    draw = home_goals == away_goals
    away_win = home_goals < away_goals
    btts = (home_goals > 0) & (away_goals > 0)

    legs: list[dict] = [
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
            "id": "dc_12",
            "family": "dc",
            "label": f"{home} or {away}",
            "mask": home_win | away_win,
        },
        {
            "id": "dc_x2",
            "family": "dc",
            "label": f"{away} or Draw",
            "mask": away_win | draw,
        },
        {"id": "btts_yes", "family": "btts", "label": "BTTS — Yes", "mask": btts},
        {"id": "btts_no", "family": "btts", "label": "BTTS — No", "mask": ~btts},
    ]
    for line in TOTAL_LINES:
        legs.append(
            {
                "id": f"over_{str(line).replace('.', '')}",
                "family": "totals",
                "label": f"Over {line} Goals",
                "mask": total > line,
                "line": line,
                "side": "over",
            }
        )
        legs.append(
            {
                "id": f"under_{str(line).replace('.', '')}",
                "family": "totals",
                "label": f"Under {line} Goals",
                "mask": total < line,
                "line": line,
                "side": "under",
            }
        )
    for line in TEAM_LINES:
        legs.append(
            {
                "id": f"home_over_{str(line).replace('.', '')}",
                "family": "home_goals",
                "label": f"{home} over {line} goals",
                "mask": home_goals > line,
                "line": line,
                "side": "over",
            }
        )
        legs.append(
            {
                "id": f"away_over_{str(line).replace('.', '')}",
                "family": "away_goals",
                "label": f"{away} over {line} goals",
                "mask": away_goals > line,
                "line": line,
                "side": "over",
            }
        )
    for leg in legs:
        leg["prob"] = event_prob(grid, leg["mask"])
    return legs


def _masks_equal(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.array_equal(a, b))


def _joint_mask(masks: list[np.ndarray]) -> np.ndarray:
    out = masks[0]
    for mask in masks[1:]:
        out = out & mask
    return out


def combo_is_valid(legs: list[dict]) -> tuple[bool, str | None]:
    """Reject contradictions, duplicates, and logically redundant legs.

    Redundancy: removing a leg leaves the joint event mask unchanged.
    """
    ids = [leg["id"] for leg in legs]
    if len(ids) != len(set(ids)):
        return False, "duplicate_leg"
    families = [leg["family"] for leg in legs]
    if families.count("1x2") > 1 or families.count("dc") > 1 or families.count("btts") > 1:
        return False, "duplicate_family"
    if "1x2" in families and "dc" in families:
        return False, "1x2_with_double_chance"

    joint = _joint_mask([leg["mask"] for leg in legs])
    if not joint.any():
        return False, "contradictory"

    # Nested / opposing totals on the same line family
    totals = [leg for leg in legs if leg["family"] == "totals"]
    for a, b in combinations(totals, 2):
        if a.get("side") != b.get("side") and a.get("line") == b.get("line"):
            return False, "opposing_totals"
        if a.get("side") == "over" and b.get("side") == "over" and a.get("line") != b.get("line"):
            # Over 1.5 + Over 2.5 is redundant (stricter implies looser)
            return False, "nested_over_totals"
        if a.get("side") == "under" and b.get("side") == "under" and a.get("line") != b.get("line"):
            return False, "nested_under_totals"

    for i, leg in enumerate(legs):
        without = _joint_mask([legs[j]["mask"] for j in range(len(legs)) if j != i])
        if _masks_equal(without, joint):
            return False, f"redundant:{leg['id']}"

    return True, None


def _pack_combo(legs: list[dict], grid: np.ndarray, n: int) -> dict:
    joint = _joint_mask([leg["mask"] for leg in legs])
    labels = [leg["label"] for leg in legs]
    prob = event_prob(grid, joint)
    return {
        "legs": labels,
        "leg_ids": [leg["id"] for leg in legs],
        "label": " + ".join(labels),
        "prob": prob,
        "n": n,
        "fair_odds": (1.0 / prob) if prob > 0 else None,
        "market_odds": None,
        "expected_value": None,
        "value_status": "unavailable",
        "explain": (
            "Joint probability = sum of score cells satisfying every leg "
            "(not the product of marginals)."
        ),
        "risk": "LOWER" if n <= 2 and prob >= 0.55 else ("MEDIUM" if n <= 3 else "HIGH"),
    }


def rank_single_selections(prediction, home: str, away: str, limit: int = 3) -> list[dict]:
    """Most-likely singles: one selection per family, ranked by grid probability."""
    legs = supported_legs(prediction, home, away)
    # Prefer informative markets: drop near-certain soft lines when alternatives exist.
    informative = [
        leg
        for leg in legs
        if HARD_PROB_FLOOR <= leg["prob"] <= SOFT_PROB_CAP and leg["id"] != "dc_12"
    ]
    pool = informative or [
        leg for leg in legs if leg["prob"] >= HARD_PROB_FLOOR and leg["id"] != "dc_12"
    ]

    best_by_family: dict[str, dict] = {}
    for leg in pool:
        key = leg["family"]
        prev = best_by_family.get(key)
        if prev is None or leg["prob"] > prev["prob"]:
            best_by_family[key] = leg

    ranked = sorted(best_by_family.values(), key=lambda item: item["prob"], reverse=True)
    picked: list[dict] = []
    seen = set()
    for leg in ranked:
        if leg["family"] in seen:
            continue
        if leg["family"] == "1x2" and "dc" in seen:
            continue
        if leg["family"] == "dc" and "1x2" in seen:
            continue
        picked.append(
            {
                "rank": len(picked) + 1,
                "id": leg["id"],
                "market": {
                    "1x2": "Match result",
                    "dc": "Double chance",
                    "btts": "Both teams to score",
                    "totals": "Total goals",
                    "home_goals": f"{home} team goals",
                    "away_goals": f"{away} team goals",
                }.get(leg["family"], leg["family"]),
                "selection": leg["label"],
                "prob": float(leg["prob"]),
                "fair_odds": (1.0 / leg["prob"]) if leg["prob"] > 0 else None,
                "market_odds": None,
                "expected_value": None,
                "value_status": "unavailable",
                "confidence": (
                    "HIGH" if leg["prob"] >= 0.70 else "MEDIUM" if leg["prob"] >= 0.55 else "SPECULATIVE"
                ),
                "explain": f"Sum of score-grid cells for this event ({leg['prob']:.1%}).",
            }
        )
        seen.add(leg["family"])
        if len(picked) == limit:
            break
    return picked


def best_combos(prediction, home: str, away: str) -> dict[str, Any]:
    """Enumerate valid 2-leg and 3-leg combos; pick highest joint probability each."""
    grid = prediction.grid
    legs = supported_legs(prediction, home, away)
    candidates = [
        leg
        for leg in legs
        if HARD_PROB_FLOOR <= leg["prob"] <= SOFT_PROB_CAP and leg["id"] != "dc_12"
    ]
    if len(candidates) < 2:
        candidates = [
            leg
            for leg in legs
            if 0.10 <= leg["prob"] <= 0.88 and leg["id"] != "dc_12"
        ]

    best_two = None
    for combo in combinations(candidates, 2):
        ok, reason = combo_is_valid(list(combo))
        if not ok:
            continue
        packed = _pack_combo(list(combo), grid, 2)
        if best_two is None or packed["prob"] > best_two["prob"]:
            best_two = packed

    best_three = None
    rejected_three = 0
    for combo in combinations(candidates, 3):
        ok, reason = combo_is_valid(list(combo))
        if not ok:
            rejected_three += 1
            continue
        packed = _pack_combo(list(combo), grid, 3)
        if packed["prob"] < 0.03:
            continue
        if best_three is None or packed["prob"] > best_three["prob"]:
            best_three = packed

    note = None
    if best_three is None:
        note = (
            "No meaningful valid 3-leg combination within supported markets "
            f"(rejected {rejected_three} contradictory/redundant triples). "
            "Showing the strongest valid shorter combination instead."
        )

    return {
        "combo_2leg": best_two,
        "combo_3leg": best_three,
        "combo_note": note,
        "diagnostics": grid_diagnostics(grid),
    }
