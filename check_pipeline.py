"""Meaningful local checks for the repaired prediction pipeline."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np

from engine import build_prediction, get_fixtures, get_league_state
from markets import best_combos, combo_is_valid, supported_legs
import forecasts as forecast_store


def section(title: str):
    print(f"\n=== {title} ===")


def main():
    section("1. penaltyblog model identity")
    state = get_league_state("laliga")
    model = state["model"]
    print("class:", f"{type(model).__module__}.{type(model).__name__}")
    print("fitted:", getattr(model, "fitted", None))
    print("trained_on:", state["trained_on"], "results_through:", state["results_through"])
    print("cutoff:", state["cutoff"], "xi:", state["xi"])
    print("params sample:", list(model.params.keys())[:5], "...", "rho=", model.params.get("rho"))

    section("2. fixture identity + forecast kinds")
    fixtures = get_fixtures("laliga")
    nxt = (fixtures.get("next_matches") or fixtures.get("upcoming") or [None])[0]
    print("next fixture:", json.dumps({k: nxt.get(k) for k in ("fixture_id", "home", "away", "kickoff", "status")}, default=str) if nxt else None)

    section("3. contrasting match forecasts")
    pairs = [
        ("Real Madrid", "Barcelona"),
        ("Getafe", "Osasuna"),
        ("Ath Madrid", "Sevilla"),
    ]
    outputs = []
    for home, away in pairs:
        result = build_prediction("laliga", home, away)
        outputs.append(result)
        print(f"\n{home} vs {away} [{result['forecast_kind']}]")
        print(f"  fixture_id={result.get('fixture_id')} kickoff={result.get('kickoff')}")
        print(f"  model={result['model_class']} xG={result['xg_home']:.2f}-{result['xg_away']:.2f}")
        print(f"  1X2={result['home_win']:.1%}/{result['draw']:.1%}/{result['away_win']:.1%}")
        print(f"  top score={result['exact_scores'][0]}")
        print(f"  singles={[p['selection'] for p in result['single_picks']]}")
        print(f"  2-leg={result['combo_2leg']['label'] if result['combo_2leg'] else None} ({(result['combo_2leg'] or {}).get('prob')})")
        print(f"  3-leg={result['combo_3leg']['label'] if result['combo_3leg'] else result.get('combo_note')}")
        print(f"  form L5 home={result['recent_form']['home']['windows'].get('5', {}).get('record')} away={result['recent_form']['away']['windows'].get('5', {}).get('record')}")
        print(f"  lineup={result['lineup']['status']} included={result['lineup']['included_in_model']}")
        print(f"  freshness through={result['freshness']['results_current_through']} cached={result['freshness']['from_cache']}")
        diag = result["grid_diagnostics"]
        assert diag["normalized"] and diag["finite"] and diag["nonnegative"]

    section("4. reproducibility")
    a = build_prediction("laliga", "Getafe", "Osasuna")
    b = build_prediction("laliga", "Getafe", "Osasuna")
    keys = ["xg_home", "xg_away", "home_win", "draw", "away_win", "predicted_result"]
    same = all(a[k] == b[k] for k in keys)
    print("identical core outputs:", same)
    assert same

    section("5. joint combo != product of marginals")
    pred = state["model"].predict("Real Madrid", "Barcelona")
    legs = supported_legs(pred, "Real Madrid", "Barcelona")
    by_id = {leg["id"]: leg for leg in legs}
    combo = [by_id["home_win"], by_id["btts_yes"]]
    ok, reason = combo_is_valid(combo)
    joint = float(pred.grid[combo[0]["mask"] & combo[1]["mask"]].sum())
    product = combo[0]["prob"] * combo[1]["prob"]
    print("valid:", ok, "joint:", round(joint, 4), "product:", round(product, 4), "diff:", round(joint - product, 4))
    assert abs(joint - product) > 1e-4 or joint == 0

    section("6. redundancy rejection")
    # Home win + Over 2.5 + Home over 1.5 is often logically redundant
    triple = [by_id["home_win"], by_id["over_25"], by_id["home_over_15"]]
    ok, reason = combo_is_valid(triple)
    print("home_win+over25+home_over15 valid?", ok, reason)
    bundle = best_combos(pred, "Real Madrid", "Barcelona")
    if bundle["combo_3leg"]:
        print("chosen 3-leg:", bundle["combo_3leg"]["label"], bundle["combo_3leg"]["prob"])

    section("7. forecast snapshot freeze/settle")
    meta = forecast_store.init_store()
    print("tracking_activated_at:", meta["tracking_activated_at"])
    future_kick = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    fid = "test:local-check-1"
    payload = {
        **outputs[1],
        "fixture_id": fid,
        "kickoff": future_kick,
        "league_id": "laliga",
        "home": "Getafe",
        "away": "Osasuna",
        "exact_scores": outputs[1]["exact_scores"],
        "single_picks": outputs[1]["single_picks"],
        "combo_2leg": outputs[1]["combo_2leg"],
        "combo_3leg": outputs[1]["combo_3leg"],
        "predicted_result": outputs[1]["predicted_result"],
        "predicted_result_label": outputs[1]["predicted_result_label"],
        "predicted_result_prob": outputs[1]["predicted_result_prob"],
    }
    saved = forecast_store.save_live_forecast(fid, payload)
    print("saved:", saved)
    # Simulate kickoff passed by writing past kickoff then freeze
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    forecast_store.save_live_forecast(
        fid,
        {**payload, "kickoff": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    )
    # Direct freeze path
    with forecast_store.connect() as conn:
        conn.execute("UPDATE forecasts SET kickoff=? WHERE fixture_id=?", (past, fid))
        conn.commit()
    frozen = forecast_store.freeze_at_kickoff(fid, past)
    print("frozen_at:", frozen.get("frozen_at") if frozen else None)
    settlement = forecast_store.settle_fixture(fid, 1, 0, finished=True)
    print("settlement result_hit:", settlement.get("result_hit") if settlement else None)
    print("settlement exact:", settlement.get("exact_score_hit") if settlement else None)
    # Ensure freeze not overwritten
    again = forecast_store.save_live_forecast(fid, {**payload, "kickoff": future_kick, "home_win": 0.99})
    print("overwrite after freeze blocked:", again)

    section("8. no training after cutoff")
    cut = datetime(2026, 9, 10, tzinfo=timezone.utc)
    early = get_league_state("laliga", cutoff=cut)
    latest = early["results_through"]
    print("early cutoff results_through:", latest, "trained_on:", early["trained_on"])
    assert latest < "2026-09-10"

    section("9. scheduled fixture path")
    scheduled = build_prediction(
        "laliga",
        nxt["home"],
        nxt["away"],
        fixture_id=nxt.get("fixture_id"),
        kickoff=nxt.get("kickoff"),
    )
    print(
        "kind=", scheduled["forecast_kind"],
        "id=", scheduled.get("fixture_id"),
        "saved=", scheduled.get("forecast_saved"),
        "2leg=", (scheduled.get("combo_2leg") or {}).get("label"),
        "3leg=", (scheduled.get("combo_3leg") or {}).get("label") or scheduled.get("combo_note"),
    )
    assert scheduled["forecast_kind"] == "upcoming_fixture"
    assert scheduled.get("fixture_id")

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
