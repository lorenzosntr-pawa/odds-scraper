"""Does the source actually carry live scores in home_score/away_score?

Checks the FT 1X2 rows (what we anchor on) for in-play score variety, so we
can tell whether all-zero output scores are a data-slice artifact or a real
bug. Usage (CH_* env set, tunnel up):
  uv run python scripts/check_source_scores.py \
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
    t = args.source
    bf = f" AND brand = '{args.brand}'" if args.brand else ""
    base = f"FROM {t} WHERE market_name = '1X2 - FT'{bf}"

    def show(title, sql):
        print(f"\n=== {title} ===")
        res = client.query(sql)
        print("  " + " | ".join(res.column_names))
        for row in res.result_rows:
            print("  " + " | ".join(str(v) for v in row))

    show("FT 1X2 rows by in_play, and how many have a non-zero score",
         f"SELECT in_play, count() AS rows, "
         f"countIf(home_score != 0 OR away_score != 0) AS scored_rows, "
         f"max(home_score) AS max_h, max(away_score) AS max_a {base} "
         f"GROUP BY in_play ORDER BY in_play")

    show("top (home_score, away_score) among LIVE FT 1X2 rows",
         f"SELECT home_score, away_score, count() AS rows {base} AND in_play "
         f"GROUP BY home_score, away_score ORDER BY rows DESC LIMIT 20")

    show("sample LIVE FT 1X2 rows that DO have a score",
         f"SELECT event_id, odds_timestamp, home_score, away_score, "
         f"selection_name, true_proba {base} AND in_play "
         f"AND (home_score != 0 OR away_score != 0) LIMIT 10")


if __name__ == "__main__":
    main()
