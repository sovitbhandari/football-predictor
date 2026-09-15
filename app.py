from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import forecasts as forecast_store
from engine import LEAGUES, build_prediction, get_fixtures, get_league_state, get_live_board

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
        state = get_league_state(league_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown league") from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    league = state["league"]
    fixtures = get_fixtures(league_id, tz)
    focus = (
        fixtures["live"][0]
        if fixtures["live"]
        else fixtures["today"][0]
        if fixtures["today"]
        else fixtures["next_matches"][0]
        if fixtures["next_matches"]
        else fixtures["next"]
    )
    return {
        "id": league["id"],
        "name": league["name"],
        "season": league["season_label"],
        "teams": state["teams"],
        "trained_on": state["trained_on"],
        "current_matches": state["current_matches"],
        "prior_matches": state["prior_matches"],
        "results_through": state.get("results_through"),
        "tracking_activated_at": forecast_store.tracking_activated_at().isoformat(),
        "as_of": fixtures["as_of"],
        "timezone": fixtures["timezone"],
        "live": fixtures["live"],
        "today": fixtures["today"],
        "next": fixtures["next"],
        "next_matches": fixtures["next_matches"],
        "upcoming": fixtures["upcoming"],
        "carousel": fixtures.get("carousel") or [],
        "focus": focus,
    }


@app.post("/api/predict")
def predict(payload: PredictRequest):
    try:
        return build_prediction(
            payload.league_id,
            payload.home,
            payload.away,
            fixture_id=payload.fixture_id,
            kickoff=payload.kickoff,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown league") from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


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
