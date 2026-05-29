"""What's in the exact LIMIT slice the run scanned? Counts prematch vs live
source rows for the same extraction_sql(brand, limit) the CLI builds — to tell
whether a '0 prematch' result is a slice artifact or a real bug.

  uv run python scripts/check_slice_split.py \
    --source bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo \
    --brand betpawa-ghana --limit 500000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odds_scraper.reconstruct import clickhouse_io as chio
from odds_scraper.reconstruct import queries


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--brand", default=None)
    ap.add_argument("--limit", type=int, default=500000)
    args = ap.parse_args()
    client = chio.connect()

    inner = queries.extraction_sql(args.source, brand=args.brand, limit=args.limit).rstrip()

    def show(title, sql):
        print(f"\n=== {title} ===")
        res = client.query(sql)
        print("  " + " | ".join(res.column_names))
        for row in res.result_rows:
            print("  " + " | ".join(str(v) for v in row))

    # prematch vs live composition of the exact scanned slice
    show("slice rows by in_play",
         f"SELECT in_play, count() AS rows FROM ({inner}) GROUP BY in_play ORDER BY in_play")

    # how many distinct events the slice touches, and the event_id range
    show("events covered by the slice",
         f"SELECT count(DISTINCT event_id) AS events, min(event_id) AS min_ev, "
         f"max(event_id) AS max_ev FROM ({inner})")

    # do those events even have prematch 1X2 rows?
    show("prematch 1X2 rows among the slice's events",
         f"SELECT count() AS prematch_1x2_rows FROM ({inner}) "
         f"WHERE in_play = 0 AND market_name = '1X2 - FT'")


if __name__ == "__main__":
    main()
