from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import forecasts as forecast_store
from engine import (
    LEAGUES,
    build_prediction,
    get_fixtures,
    get_league_catalog,
    get_live_board,
    league_by_id,
)

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Football Predictor")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
forecast_store.init_store()


class PredictRequest(BaseModel):
    league_id: str
    home: str
    away: str
    fixture_id: str | None = None
    kickoff: str | None = None


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/live")
def live(tz: str | None = None):
    return get_live_board(tz)


@app.get("/api/leagues")
def leagues():
    return [
        {
            "id": league["id"],
            "name": league["name"],
            "country": league["country"],
            "season": league["season_label"],
        }
        for league in LEAGUES
    ]


@app.get("/api/leagues/{league_id}/teams")
def teams(league_id: str, tz: str | None = None):
    try:
        league = league_by_id(league_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown league") from None
    try:
        fixtures = get_fixtures(league_id, tz)
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load fixtures ({error}). Try again in a minute.",
        ) from error
    catalog_error = None
    try:
        state = get_league_catalog(league_id)
    except Exception as error:
        catalog_error = str(error)
        fixture_teams = sorted(
            {
                *(item.get("home") for item in (fixtures.get("carousel") or []) if item.get("home")),
                *(item.get("away") for item in (fixtures.get("carousel") or []) if item.get("away")),
                *(item.get("home") for item in (fixtures.get("live") or []) if item.get("home")),
                *(item.get("away") for item in (fixtures.get("live") or []) if item.get("away")),
            }
        )
        state = {
            "league": league,
            "teams": fixture_teams,
            "trained_on": 0,
            "current_matches": 0,
            "prior_matches": 0,
            "results_through": None,
        }
        if not fixture_teams:
            raise HTTPException(
                status_code=503,
                detail=f"Could not load league data ({error}). Try again in a minute.",
            ) from error
    focus = (
        fixtures["live"][0]
        if fixtures.get("live")
        else fixtures["today"][0]
        if fixtures.get("today")
        else fixtures["next_matches"][0]
        if fixtures.get("next_matches")
        else fixtures.get("next")
    )
    return {
        "id": league["id"],
        "name": league["name"],
        "season": state.get("season_label") or league["season_label"],
        "teams": state["teams"],
        "trained_on": state["trained_on"],
        "current_matches": state["current_matches"],
        "prior_matches": state["prior_matches"],
        "results_through": state.get("results_through"),
        "tracking_activated_at": forecast_store.tracking_activated_at().isoformat(),
        "as_of": fixtures.get("as_of"),
        "timezone": fixtures.get("timezone"),
        "live": fixtures.get("live") or [],
        "today": fixtures.get("today") or [],
        "next": fixtures.get("next"),
        "next_matches": fixtures.get("next_matches") or [],
        "upcoming": fixtures.get("upcoming") or [],
        "carousel": fixtures.get("carousel") or [],
        "focus": focus,
        "model_ready": False,
        "catalog_error": catalog_error,
        "note": (
            "No upcoming fixtures published yet — pick teams manually to predict."
            if not (fixtures.get("carousel") or [])
            else "Model fits on demand when you open a match prediction."
        ),
    }


@app.post("/api/predict")
def predict(payload: PredictRequest):
    try:
        league_by_id(payload.league_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown league") from None
    try:
        return build_prediction(
            payload.league_id,
            payload.home,
            payload.away,
            fixture_id=payload.fixture_id,
            kickoff=payload.kickoff,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"Could not score match ({error}). Try again in a minute.",
        ) from error


@app.get("/api/forecasts/tracked")
def tracked_forecasts(limit: int = 50):
    return {
        "tracking_activated_at": forecast_store.tracking_activated_at().isoformat(),
        "note": (
            "Only fixtures forecast before kickoff after activation are listed. "
            "No separate history dashboard is exposed in the UI; settlement appears "
            "on the fixture page when available."
        ),
        "items": forecast_store.list_tracked(limit=limit),
    }


@app.get("/api/forecasts/{fixture_id}")
def forecast_snapshot(fixture_id: str):
    frozen = forecast_store.get_frozen(fixture_id)
    if frozen is None:
        raise HTTPException(status_code=404, detail="No frozen forecast for this fixture")
    return frozen
