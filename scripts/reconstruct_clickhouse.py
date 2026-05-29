"""Batch-reconstruct V2/V3/V4 1UP/2UP odds from the ClickHouse betslip log.

Requires a reachable ClickHouse (local Teleport proxy) via CH_* env vars:
  CH_HOST, CH_PORT, CH_USER, CH_PASSWORD, CH_DATABASE
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odds_scraper.reconstruct import clickhouse_io as chio
from odds_scraper.reconstruct import constants as c
from odds_scraper.reconstruct import pricing, queries, report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="e.g. bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo")
    ap.add_argument("--output", default=c.DEFAULT_OUTPUT_TABLE)
    ap.add_argument("--report", required=True)
    ap.add_argument("--run-ts", required=True,
                    help="run identifier timestamp 'YYYY-MM-DD HH:MM:SS'")
    ap.add_argument("--brand", default=None,
                    help="restrict to one brand (e.g. betpawa-ghana); recommended "
                         "for a first run — the source duplicates events across ~13 brands")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap total source rows scanned (smoke run)")
    ap.add_argument("--batch-size", type=int, default=10_000)
    args = ap.parse_args()

    client = chio.connect()
    client.command(queries.output_ddl(args.output))

    restore = pricing.install_dp_cache()
    n_scanned = n_out = n_1up = n_prematch = n_live = flagged = 0
    sample_rows = []
    try:
        def _counting_scan(it):
            nonlocal n_scanned
            for r in it:
                n_scanned += 1
                if n_scanned % 1_000_000 == 0:
                    print(f"  ...scanned {n_scanned:,} source rows, "
                          f"emitted {n_out:,} priced rows", flush=True)
                yield r

        sql = queries.extraction_sql(args.source, brand=args.brand, limit=args.limit)
        rows_stream = _counting_scan(chio.stream_rows(client, sql))
        moments = pricing.moments_from_rows(rows_stream)
        priced = pricing.run_pricing(moments, run_ts=args.run_ts)

        def _accounting(it):
            nonlocal n_out, n_1up, n_prematch, n_live, flagged
            for row in it:
                n_out += 1
                n_1up += 1 if row["has_1up"] else 0
                n_prematch += 0 if row["in_play"] else 1
                n_live += 1 if row["in_play"] else 0
                if abs(row["renorm_drift"]) > c.RENORM_DRIFT_TOL:
                    flagged += 1
                sample_rows.append({
                    "in_play": row["in_play"], "has_1up": row["has_1up"],
                    "max_input_staleness_seconds": row["max_input_staleness_seconds"],
                    "renorm_drift": row["renorm_drift"]})
                yield row

        print(f"streaming source{' (brand=' + args.brand + ')' if args.brand else ''}"
              f"{' limit=' + str(args.limit) if args.limit else ''} ...", flush=True)
        inserted = chio.insert_rows(client, args.output, _accounting(priced),
                                    columns=c.OUTPUT_COLUMNS,
                                    batch_size=args.batch_size)
        cache_info = pricing.dp_cache_info()
    finally:
        restore()

    Path(args.report).write_text(
        report.build_report(source_table=args.source, output_table=args.output,
                            n_out=n_out, n_1up=n_1up, n_prematch=n_prematch,
                            n_live=n_live, sample_rows=sample_rows,
                            flagged_drift=flagged, n_scanned=n_scanned,
                            cache_info=cache_info),
        encoding="utf-8")
    print(f"inserted {inserted} rows -> {args.output} ({n_1up} with 1UP, "
          f"{n_prematch} prematch / {n_live} live)")


if __name__ == "__main__":
    main()
