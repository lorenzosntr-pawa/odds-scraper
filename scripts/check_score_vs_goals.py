"""One decisive query: for LIVE next-goal rows where goals are clearly already
scored (handicap >= 12 => next goal is #3+, i.e. >=2 goals in), what do the
home_score/away_score columns say? Expect them still 0 — proving the score
columns are unpopulated even when the goal count is non-zero.

  uv run python scripts/check_score_vs_goals.py \
    --source bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo \
    --brand betpawa-ghana
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odds_scraper.reconstruct import clickhouse_io as chio


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--brand", default=None)
    args = ap.parse_args()
    client = chio.connect()
    bf = f"brand = '{args.brand}' AND " if args.brand else ""
    sql = (
        f"SELECT intDiv(handicap, 4) AS next_goal, "
        f"intDiv(handicap, 4) - 1 AS goals_already_scored, "
        f"home_score, away_score, count() AS rows "
        f"FROM {args.source} "
        f"WHERE {bf} in_play AND market_name = '{{handicap}} Goal' AND handicap >= 12 "
        f"GROUP BY next_goal, home_score, away_score "
        f"ORDER BY next_goal LIMIT 30"
    )
    res = client.query(sql)
    print("  " + " | ".join(res.column_names))
    for row in res.result_rows:
        print("  " + " | ".join(str(v) for v in row))


if __name__ == "__main__":
    main()
