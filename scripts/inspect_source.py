"""Diagnostic: dump the source table's real vocabulary so we can confirm the
market/selection labels in odds_scraper/reconstruct/constants.py.

Usage (with CH_* env vars set, same as the main script):
  uv run python scripts/inspect_source.py \
    --source bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo
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
    args = ap.parse_args()
    client = chio.connect()

    def show(title, sql):
        print(f"\n=== {title} ===")
        for row in client.query(sql).result_rows:
            print("  ", row)

    total = client.query(f"SELECT count() FROM {args.source}").result_rows[0][0]
    print(f"total rows: {total:,}")
    show("columns (name, type)", f"DESCRIBE TABLE {args.source}")
    show("market_name x count (top 40)",
         f"SELECT market_name, count() c FROM {args.source} "
         f"GROUP BY market_name ORDER BY c DESC LIMIT 40")
    show("selection_name x count (top 40)",
         f"SELECT selection_name, count() c FROM {args.source} "
         f"GROUP BY selection_name ORDER BY c DESC LIMIT 40")
    show("in_play values", f"SELECT in_play, count() c FROM {args.source} GROUP BY in_play")
    show("sample 5 rows", f"SELECT * FROM {args.source} LIMIT 5")


if __name__ == "__main__":
    main()
