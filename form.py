"""Recent-form summaries from completed matches before a forecast cutoff.

Observed form is displayed separately from Dixon–Coles expected goals.
No invented attack/defence multipliers are applied here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


def _as_utc(stamp) -> datetime | None:
    if stamp is None or (isinstance(stamp, float) and pd.isna(stamp)):
        return None
    if isinstance(stamp, datetime):
        if stamp.tzinfo is None:
            return stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    parsed = pd.to_datetime(stamp, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def matches_before(matches: pd.DataFrame, cutoff: datetime | None) -> pd.DataFrame:
    if cutoff is None or matches.empty:
        return matches.copy()
    cut = _as_utc(cutoff)
    dates = pd.to_datetime(matches["date"], utc=True)
    return matches.loc[dates < cut].copy()


def team_recent_form(
    matches: pd.DataFrame,
    team: str,
    cutoff: datetime | None,
    windows: tuple[int, ...] = (5, 10),
) -> dict:
    """Last N completed matches for a team before cutoff."""
    prior = matches_before(matches, cutoff)
    if prior.empty:
        return {"team": team, "windows": {}, "note": "No completed matches before cutoff."}

    rows = []
    for row in prior.sort_values("date").itertuples():
        if row.home == team:
            scored, conceded = int(row.goals_home), int(row.goals_away)
            venue = "home"
            opponent = row.away
        elif row.away == team:
            scored, conceded = int(row.goals_away), int(row.goals_home)
            venue = "away"
            opponent = row.home
        else:
            continue
        if scored > conceded:
            result, points = "W", 3
        elif scored == conceded:
            result, points = "D", 1
        else:
            result, points = "L", 0
        rows.append(
            {
                "date": pd.Timestamp(row.date).date().isoformat(),
                "venue": venue,
                "opponent": opponent,
                "scored": scored,
                "conceded": conceded,
                "result": result,
                "points": points,
            }
        )

    if not rows:
        return {"team": team, "windows": {}, "note": "Team has no matches before cutoff."}

    frame = pd.DataFrame(rows)
    windows_out = {}
    for n in windows:
        sample = frame.tail(n)
        home_rows = sample[sample["venue"] == "home"]
        away_rows = sample[sample["venue"] == "away"]
        windows_out[str(n)] = {
            "n": int(len(sample)),
            "record": "".join(sample["result"].tolist()),
            "wins": int((sample["result"] == "W").sum()),
            "draws": int((sample["result"] == "D").sum()),
            "losses": int((sample["result"] == "L").sum()),
            "points": int(sample["points"].sum()),
            "points_per_match": float(sample["points"].mean()),
            "goals_for": float(sample["scored"].mean()),
            "goals_against": float(sample["conceded"].mean()),
            "home_n": int(len(home_rows)),
            "away_n": int(len(away_rows)),
            "home_ppg": float(home_rows["points"].mean()) if len(home_rows) else None,
            "away_ppg": float(away_rows["points"].mean()) if len(away_rows) else None,
            "matches": sample.to_dict(orient="records"),
        }

    # Rest days before cutoff (from last completed match)
    last_date = pd.to_datetime(frame.iloc[-1]["date"]).date()
    cut_date = (_as_utc(cutoff) or datetime.now(timezone.utc)).date()
    rest_days = (cut_date - last_date).days

    return {
        "team": team,
        "windows": windows_out,
        "rest_days": int(rest_days),
        "last_match_date": frame.iloc[-1]["date"],
        "xg_note": (
            "Shot-based xG is not available in football-data.co.uk CSVs used for fitting. "
            "Model expected goals below are Dixon–Coles λ estimates, not observed xG."
        ),
        "included_in_model": False,
        "note": (
            "Displayed for context. Numerical forecast uses time-weighted Dixon–Coles "
            "on all matches before the cutoff (default ξ), not a separate form bonus."
        ),
    }


def venue_career_stats(matches: pd.DataFrame, team: str, venue: str, cutoff: datetime | None) -> dict:
    prior = matches_before(matches, cutoff)
    if venue == "home":
        games = prior[prior["home"] == team]
        scored, conceded = games["goals_home"], games["goals_away"]
    else:
        games = prior[prior["away"] == team]
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
