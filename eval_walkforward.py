"""CLI wrapper for walk-forward Dixon-Coles model evaluation."""

from __future__ import annotations

import argparse
import json

from model_quality import evaluate_league


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-forward model evaluation")
    parser.add_argument("league_id", nargs="?", default="laliga")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = evaluate_league(args.league_id)
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
