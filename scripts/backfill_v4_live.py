"""One-shot: backfill v4_* on existing pricer_live_results rows.

Run once after deploying schema v11. Idempotent — re-running fills only
rows that still lack V4. V3/V2 values are never touched.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import live_writer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="path to the odds SQLite DB")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db, isolation_level=None)
    init_schema(conn)  # ensure schema v11 is applied
    updated, skipped = live_writer.backfill_v4(conn)
    print(f"v4 backfill: updated {updated}, skipped {skipped}")


if __name__ == "__main__":
    main()
