"""Durable pre-kickoff forecast snapshots and post-match settlement.

Only fixtures forecast after TRACKING_ACTIVATED_AT are tracked.
No retrospective backfill.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

STORE_DIR = Path(__file__).resolve().parent / ".data"
DB_PATH = STORE_DIR / "forecasts.sqlite"
# Activation: first process that opens the store records this; do not backfill earlier matches.
_DEFAULT_ACTIVATION = "2026-09-15T00:00:00+00:00"

_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def connect() -> sqlite3.Connection:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_store() -> dict:
    with _LOCK:
        conn = connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS forecasts (
                    fixture_id TEXT PRIMARY KEY,
                    league_id TEXT NOT NULL,
                    home TEXT NOT NULL,
                    away TEXT NOT NULL,
                    kickoff TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    frozen_at TEXT,
                    settled_at TEXT,
                    forecast_json TEXT NOT NULL,
                    frozen_json TEXT,
                    actual_json TEXT,
                    settlement_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_forecasts_league ON forecasts(league_id);
                """
            )
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'tracking_activated_at'"
            ).fetchone()
            if row is None:
                activated = _DEFAULT_ACTIVATION
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES (?, ?)",
                    ("tracking_activated_at", activated),
                )
                conn.commit()
            else:
                activated = row["value"]
            return {"tracking_activated_at": activated, "db_path": str(DB_PATH)}
        finally:
            conn.close()


def tracking_activated_at() -> datetime:
    meta = init_store()
    return _parse(meta["tracking_activated_at"]) or datetime.fromisoformat(_DEFAULT_ACTIVATION)


def fixture_eligible_for_tracking(kickoff: str | None, generated_at: datetime | None = None) -> bool:
    """Only track fixtures whose kickoff is at/after activation and forecast made before kickoff."""
    activated = tracking_activated_at()
    kick = _parse(kickoff)
    gen = generated_at or _utc_now()
    if kick is None:
        return False
    if kick < activated:
        return False
    if gen >= kick:
        return False
    return True


def save_live_forecast(fixture_id: str, payload: dict) -> dict:
    """Upsert the latest pre-kickoff forecast. Never overwrite a frozen snapshot."""
    init_store()
    now = _utc_now().isoformat()
    kickoff = payload.get("kickoff")
    if not fixture_eligible_for_tracking(kickoff, _utc_now()):
        return {"saved": False, "reason": "not_eligible_for_tracking"}

    with _LOCK:
        conn = connect()
        try:
            existing = conn.execute(
                "SELECT frozen_json, frozen_at, status FROM forecasts WHERE fixture_id = ?",
                (fixture_id,),
            ).fetchone()
            if existing and existing["frozen_json"]:
                return {
                    "saved": False,
                    "reason": "already_frozen",
                    "frozen_at": existing["frozen_at"],
                }
            blob = json.dumps(payload, default=str)
            if existing:
                conn.execute(
                    """
                    UPDATE forecasts
                    SET league_id=?, home=?, away=?, kickoff=?, status=?,
                        updated_at=?, forecast_json=?
                    WHERE fixture_id=?
                    """,
                    (
                        payload.get("league_id"),
                        payload.get("home"),
                        payload.get("away"),
                        kickoff,
                        "pre_kickoff",
                        now,
                        blob,
                        fixture_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO forecasts(
                        fixture_id, league_id, home, away, kickoff, status,
                        created_at, updated_at, forecast_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fixture_id,
                        payload.get("league_id"),
                        payload.get("home"),
                        payload.get("away"),
                        kickoff,
                        "pre_kickoff",
                        now,
                        now,
                        blob,
                    ),
                )
            conn.commit()
            return {"saved": True, "updated_at": now}
        finally:
            conn.close()


def freeze_at_kickoff(fixture_id: str, kickoff: str | None) -> dict | None:
    """Freeze the latest pre-kickoff forecast once kickoff has passed."""
    init_store()
    kick = _parse(kickoff)
    if kick is None or _utc_now() < kick:
        return None
    with _LOCK:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT * FROM forecasts WHERE fixture_id = ?", (fixture_id,)
            ).fetchone()
            if row is None:
                return None
            if row["frozen_json"]:
                return json.loads(row["frozen_json"])
            frozen_at = _utc_now().isoformat()
            conn.execute(
                """
                UPDATE forecasts
                SET frozen_json = forecast_json, frozen_at = ?, status = 'frozen'
                WHERE fixture_id = ?
                """,
                (frozen_at, fixture_id),
            )
            conn.commit()
            payload = json.loads(row["forecast_json"])
            payload["frozen_at"] = frozen_at
            return payload
        finally:
            conn.close()


def get_frozen(fixture_id: str) -> dict | None:
    init_store()
    with _LOCK:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT frozen_json, forecast_json, frozen_at, kickoff, status, settlement_json, actual_json FROM forecasts WHERE fixture_id = ?",
                (fixture_id,),
            ).fetchone()
            if row is None:
                return None
            kick = _parse(row["kickoff"])
            if row["frozen_json"]:
                data = json.loads(row["frozen_json"])
            elif kick and _utc_now() >= kick and row["forecast_json"]:
                # Auto-freeze on read
                conn.execute(
                    """
                    UPDATE forecasts
                    SET frozen_json = forecast_json, frozen_at = ?, status = 'frozen'
                    WHERE fixture_id = ? AND frozen_json IS NULL
                    """,
                    (_utc_now().isoformat(), fixture_id),
                )
                conn.commit()
                data = json.loads(row["forecast_json"])
            else:
                return None
            if row["settlement_json"]:
                data["settlement"] = json.loads(row["settlement_json"])
            if row["actual_json"]:
                data["actual"] = json.loads(row["actual_json"])
            data["snapshot_status"] = row["status"]
            data["frozen_at"] = row["frozen_at"]
            return data
        finally:
            conn.close()


def settle_fixture(
    fixture_id: str,
    goals_home: int,
    goals_away: int,
    finished: bool = True,
    cancelled: bool = False,
    postponed: bool = False,
) -> dict | None:
    """Settle using regulation FT score only. Skip cancelled/postponed."""
    init_store()
    if cancelled or postponed or not finished:
        with _LOCK:
            conn = connect()
            try:
                status = "cancelled" if cancelled else ("postponed" if postponed else "open")
                conn.execute(
                    "UPDATE forecasts SET status = ? WHERE fixture_id = ?",
                    (status, fixture_id),
                )
                conn.commit()
            finally:
                conn.close()
        return None

    frozen = get_frozen(fixture_id)
    if frozen is None:
        return None

    actual_1x2 = (
        "home" if goals_home > goals_away else "away" if goals_away > goals_home else "draw"
    )
    pred_score = (frozen.get("exact_scores") or [{}])[0]
    pred_result = frozen.get("predicted_result")
    exact_hit = (
        int(pred_score.get("home", -1)) == goals_home
        and int(pred_score.get("away", -1)) == goals_away
    )
    result_hit = pred_result == actual_1x2

    def settle_pick(pick: dict) -> dict:
        won = _selection_won(pick, goals_home, goals_away)
        return {
            "selection": pick.get("selection") or pick.get("label"),
            "prob": pick.get("prob"),
            "won": won,
        }

    singles = [settle_pick(p) for p in (frozen.get("single_picks") or [])]
    combos = []
    for key in ("combo_2leg", "combo_3leg", "medium_risk", "high_risk"):
        combo = frozen.get(key)
        if not combo:
            continue
        legs = combo.get("legs") or []
        won = all(_leg_won(leg, goals_home, goals_away) for leg in legs) if legs else None
        combos.append(
            {
                "label": combo.get("label"),
                "n": combo.get("n"),
                "prob": combo.get("prob"),
                "won": won,
            }
        )

    settlement = {
        "settled_at": _utc_now().isoformat(),
        "frozen_at": frozen.get("frozen_at"),
        "actual_score": {"home": goals_home, "away": goals_away},
        "actual_result": actual_1x2,
        "predicted_score": {
            "home": pred_score.get("home"),
            "away": pred_score.get("away"),
            "prob": pred_score.get("prob"),
        },
        "predicted_result": pred_result,
        "predicted_result_label": frozen.get("predicted_result_label"),
        "predicted_result_prob": frozen.get("predicted_result_prob"),
        "exact_score_hit": exact_hit,
        "result_hit": result_hit,
        "singles": singles,
        "combos": combos,
        "note": (
            "Comparison uses the frozen pre-kickoff forecast only. "
            "An exact-score miss can still be a correct 1X2 prediction."
        ),
    }
    actual = {
        "goals_home": goals_home,
        "goals_away": goals_away,
        "result": actual_1x2,
        "regulation_only": True,
    }
    with _LOCK:
        conn = connect()
        try:
            conn.execute(
                """
                UPDATE forecasts
                SET status='settled', settled_at=?, actual_json=?, settlement_json=?
                WHERE fixture_id=?
                """,
                (
                    settlement["settled_at"],
                    json.dumps(actual),
                    json.dumps(settlement),
                    fixture_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return settlement


def _leg_won(label: str, goals_home: int, goals_away: int) -> bool | None:
    text = str(label).lower()
    total = goals_home + goals_away
    btts = goals_home > 0 and goals_away > 0
    if "btts — yes" in text or text == "btts yes":
        return btts
    if "btts — no" in text or text == "btts no":
        return not btts
    if "over 3.5" in text:
        return total > 3.5
    if "under 3.5" in text:
        return total < 3.5
    if "over 2.5" in text:
        return total > 2.5
    if "under 2.5" in text:
        return total < 2.5
    if "over 1.5" in text and "goals" in text and " win" not in text and " or " not in text:
        # could be match totals or team totals — ambiguous without id
        if "over 1.5 goals" == text or text.startswith("over 1.5"):
            return total > 1.5
    if text == "draw":
        return goals_home == goals_away
    if " or draw" in text:
        # double chance — need team name; treat conservatively
        return True if goals_home == goals_away else None
    if text.endswith(" win") or " win" in text:
        # Cannot robustly map team name without ids; leave unknown unless exact patterns
        return None
    return None


def _selection_won(pick: dict, goals_home: int, goals_away: int) -> bool | None:
    pick_id = pick.get("id") or ""
    total = goals_home + goals_away
    if pick_id == "home_win":
        return goals_home > goals_away
    if pick_id == "away_win":
        return goals_away > goals_home
    if pick_id == "draw":
        return goals_home == goals_away
    if pick_id == "btts_yes":
        return goals_home > 0 and goals_away > 0
    if pick_id == "btts_no":
        return not (goals_home > 0 and goals_away > 0)
    if pick_id.startswith("over_"):
        try:
            line = float(pick_id.split("_", 1)[1]) / (10 if len(pick_id.split("_", 1)[1]) == 2 else 1)
            # ids like over_25 -> 2.5
            raw = pick_id.split("_", 1)[1]
            line = float(raw[0] + "." + raw[1:]) if raw.isdigit() and len(raw) == 2 else float(raw)
            return total > line
        except ValueError:
            return None
    if pick_id.startswith("under_"):
        raw = pick_id.split("_", 1)[1]
        try:
            line = float(raw[0] + "." + raw[1:]) if raw.isdigit() and len(raw) == 2 else float(raw)
            return total < line
        except ValueError:
            return None
    return _leg_won(pick.get("selection") or "", goals_home, goals_away)


def list_tracked(limit: int = 50) -> list[dict]:
    init_store()
    with _LOCK:
        conn = connect()
        try:
            rows = conn.execute(
                """
                SELECT fixture_id, league_id, home, away, kickoff, status,
                       created_at, frozen_at, settled_at
                FROM forecasts
                ORDER BY COALESCE(kickoff, created_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
