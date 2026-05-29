"""Where is the live score in this table? home_score/away_score are all 0 for
FT 1X2, so find what actually carries in-play score (or the goal progression).

Usage (CH_* env set, tunnel up):
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
    bf = f"brand = '{args.brand}'" if args.brand else "1"

    def show(title, sql):
        print(f"\n=== {title} ===")
        res = client.query(sql)
        print("  " + " | ".join(res.column_names))
        for row in res.result_rows:
            print("  " + " | ".join(str(v) for v in row))

    # 1. Across ALL markets: is home_score/away_score ever non-zero anywhere?
    show("home/away_score presence by in_play (all markets)",
         f"SELECT in_play, count() AS rows, "
         f"countIf(home_score != 0 OR away_score != 0) AS scored, "
         f"max(home_score) AS max_h, max(away_score) AS max_a "
         f"FROM {t} WHERE {bf} GROUP BY in_play ORDER BY in_play")

    # 2. If some market carries the score, surface it.
    show("markets where a score is ever non-zero (top 25)",
         f"SELECT market_name, count() AS rows, "
         f"countIf(home_score != 0 OR away_score != 0) AS scored, "
         f"max(home_score) AS max_h, max(away_score) AS max_a "
         f"FROM {t} WHERE {bf} GROUP BY market_name "
         f"HAVING scored > 0 ORDER BY scored DESC LIMIT 25")

    # 3. What are the current_score_* columns? Sample live rows.
    show("sample LIVE rows: home/away_score + current_score_from/to",
         f"SELECT event_id, odds_timestamp, market_name, handicap, "
         f"home_score, away_score, current_score_from, current_score_to "
         f"FROM {t} WHERE {bf} AND in_play LIMIT 12")

    # 4. Next-goal market encodes goal progression in handicap (=goal#*4).
    #    For LIVE rows, handicap/4 - 1 = goals already scored at snapshot.
    show("LIVE next-goal handicap distribution (handicap/4 = next goal number)",
         f"SELECT handicap, intDiv(handicap, 4) AS next_goal_no, count() AS rows "
         f"FROM {t} WHERE {bf} AND in_play AND market_name = '{{handicap}} Goal' "
         f"GROUP BY handicap ORDER BY handicap LIMIT 20")


if __name__ == "__main__":
    main()
