import sys
from datetime import datetime

from engine import LEAGUES, build_prediction, get_fixtures, get_league_state


def choose_league() -> dict:
    print("Football predictor\n")
    for index, league in enumerate(LEAGUES, start=1):
        print(f"  {index:2}. {league['name']} ({league['country']})")

    raw = sys.argv[1] if len(sys.argv) > 1 else input("\nLeague number: ").strip()
    try:
        choice = int(raw)
    except ValueError:
        raise SystemExit("Enter a league number from the list.")
    if not 1 <= choice <= len(LEAGUES):
        raise SystemExit("Enter a league number from the list.")
    return LEAGUES[choice - 1]


def print_players(title: str, rows: list[dict]) -> None:
    print(f"\n{title}")
    if not rows:
        print("  (not enough player minutes yet)")
        return
    print(
        f"  {'Player':22} {'Chance':>7} {'G':>3} {'A':>3} {'xG':>5} {'xA':>5} {'Min':>4} {'Apps':>4}"
    )
    for row in rows:
        print(
            f"  {row['player'][:22]:22} {row['chance']:7.1%} "
            f"{row['goals']:3} {row['assists']:3} "
            f"{row['xg']:5.1f} {row['xa']:5.1f} {row['minutes']:4} {row['matches']:4}"
        )


def print_prediction(result: dict) -> None:
    home, away = result["home"], result["away"]
    print(f"\n{home} vs {away}")
    print(f"Expected goals: {result['xg_home']:.2f} - {result['xg_away']:.2f}")
    print("\nMatch odds")
    print(f"  Home win: {result['home_win']:.1%}")
    print(f"  Draw:     {result['draw']:.1%}")
    print(f"  Away win: {result['away_win']:.1%}")
    print(f"  BTTS:     {result['btts']:.1%}")
    print(f"  Over 2.5: {result['over_25']:.1%}")

    best = result["exact_scores"][0]
    print("\nMost likely exact score")
    print(f"  {best['home']}-{best['away']}  ({best['prob']:.1%})")
    print(
        "  Next: "
        + ", ".join(
            f"{row['home']}-{row['away']} ({row['prob']:.1%})"
            for row in result["exact_scores"][1:4]
        )
    )

    print("\nBest correlated combos")
    for index, combo in enumerate(result["combos"], start=1):
        print(f"  {index}. {combo['label']}: {combo['prob']:.1%}")

    medium, high = result["medium_risk"], result["high_risk"]
    print("\nRisk picks")
    if medium:
        print(f"  Medium (3-leg): {medium['label']}  ({medium['prob']:.1%})")
    if high:
        print(f"  High (4-leg):   {high['label']}  ({high['prob']:.1%})")

    home_form, away_form = result["home_form"], result["away_form"]
    print("\nTeam stats (training data)")
    print(
        f"  {home_form['team']} home: {home_form['games']} games, "
        f"{home_form['scored']:.2f} scored, {home_form['conceded']:.2f} conceded"
    )
    print(
        f"  {away_form['team']} away: {away_form['games']} games, "
        f"{away_form['scored']:.2f} scored, {away_form['conceded']:.2f} conceded"
    )
    if not result["h2h"]:
        print("  No head-to-head games in the training sample.")
    else:
        print("  Head to head:")
        for row in result["h2h"]:
            print(
                f"    {row['date']}  {row['home']} {row['goals_home']}-{row['goals_away']} {row['away']}"
            )

    if result.get("player_note"):
        print(f"\n{result['player_note']}")
        return
    if not result.get("players"):
        return

    print(f"\n  Source: {result.get('player_source', 'FotMob')}")
    players = result["players"]
    print_players(f"Anytime scorers  [{home}]", players["home_scorers"])
    print_players(f"Anytime scorers  [{away}]", players["away_scorers"])
    print_players(f"Anytime assists  [{home}]", players["home_assists"])
    print_players(f"Anytime assists  [{away}]", players["away_assists"])
    print(
        "\nPlayer chances use this match's expected goals, split by each "
        "player's share of team xG / xA this season."
    )


def main() -> None:
    league = choose_league()
    print(
        f"\n{league['name']} {league['season_label']}\n"
        "Training model (first run for this league can take a few seconds)..."
    )
    state = get_league_state(league["id"])
    print(
        f"Training on {state['trained_on']} matches "
        f"({state['current_matches']} from {league['season_label']}, "
        f"{state['prior_matches']} from the previous season)..."
    )
    print("\nAvailable teams:")
    print(", ".join(state["teams"]))
    local_tz = getattr(datetime.now().astimezone().tzinfo, "key", None)
    fixtures = get_fixtures(league["id"], local_tz)
    print(f"\nAs of {fixtures['as_of']} ({fixtures['timezone']})")
    if fixtures["live"]:
        print("LIVE:")
        for match in fixtures["live"]:
            print(
                f"  {match['home']} vs {match['away']}  {match.get('score') or ''} {match.get('minute') or ''}"
            )
    if fixtures["today"]:
        print("Today:")
        for match in fixtures["today"]:
            print(f"  {match['kickoff']}  {match['home']} vs {match['away']} ({match['status']})")
    if fixtures["next_matches"]:
        print("Next:")
        for match in fixtures["next_matches"]:
            print(f"  {match['kickoff']}  {match['home']} vs {match['away']}")

    focused = (
        fixtures["live"][0]
        if fixtures["live"]
        else fixtures["today"][0]
        if fixtures["today"]
        else fixtures["next_matches"][0]
        if fixtures["next_matches"]
        else {}
    )
    default_home = focused.get("home", "")
    default_away = focused.get("away", "")
    home = input(f"\nHome team [{default_home}]: ").strip() or default_home
    away = input(f"Away team [{default_away}]: ").strip() or default_away
    try:
        result = build_prediction(league["id"], home, away)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print_prediction(result)


if __name__ == "__main__":
    main()
