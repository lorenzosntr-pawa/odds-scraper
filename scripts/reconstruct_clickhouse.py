"""Batch-reconstruct V3/V4 1UP/2UP odds from the ClickHouse betslip log.

Requires a reachable ClickHouse (local Teleport proxy) via CH_* env vars:
  CH_HOST, CH_PORT, CH_USER, CH_PASSWORD, CH_DATABASE
See scripts/RECONSTRUCT_README.md for the full setup and run guide.
"""
from __future__ import annotations

import argparse
import sys
import time
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
                    help="restrict to one brand (e.g. betpawa-ghana)")
    ap.add_argument("--aggregate-brands", action="store_true",
                    help="pool ALL brands per event into one denser timeline "
                         "(true_proba is brand-independent) — more moments at higher "
                         "confidence; output rows are tagged brand='ALL'")
    ap.add_argument("--sample-mod", type=int, default=None,
                    help="representative smoke: keep ~1/N of events (whole events) "
                         "spread across the id range, e.g. --sample-mod 200. "
                         "Unbiased, unlike --limit which takes the lowest event_ids.")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap total source rows scanned (biased to lowest event_ids)")
    ap.add_argument("--max-staleness", type=int, default=None,
                    help="freshness cap (seconds): exclude inputs older than this when "
                         "building a moment; drop the moment if the 1X2 anchor is too old. "
                         "Caps every emitted row's staleness — e.g. 1800 for <=30min.")
    ap.add_argument("--engines", default="v3,v4",
                    help="comma-separated engines to compute: v3, v4, or v3,v4 "
                         "(default v3,v4). V4 alone needs no next-goal data.")
    ap.add_argument("--batch-size", type=int, default=10_000)
    ap.add_argument("--recreate", action="store_true",
                    help="DROP and recreate the output table first (use after a "
                         "schema change, or to start a clean table)")
    ap.add_argument("--shards", type=int, default=1,
                    help="split the work into N bounded chunks (by event_id hash), "
                         "each its own short-lived query/connection — avoids the proxy "
                         "resetting one giant long-running query. e.g. --shards 30 for a "
                         "full run. Whole events stay within a shard.")
    ap.add_argument("--start-shard", type=int, default=0,
                    help="resume a sharded run from this shard index (0-based). The start "
                         "shard's existing rows are cleaned first (it may be half-written).")
    ap.add_argument("--end-shard", type=int, default=None,
                    help="process shards [start-shard, end-shard) only (default: to the "
                         "end). Use disjoint ranges to run two terminals in parallel — "
                         "e.g. --start-shard 0 --end-shard 48 in one, 48..96 in the other.")
    ap.add_argument("--shard-retries", type=int, default=3,
                    help="on a connection error, reconnect + clean + retry a shard this "
                         "many times before giving up (default 3)")
    ap.add_argument("--resume", action="store_true",
                    help="continue a crashed run: keep rows already written, find the "
                         "highest event in the output, clear that boundary event, and "
                         "process only events from there on (no recreate, appends).")
    ap.add_argument("--min-event-id", type=int, default=None,
                    help="only process events with event_id >= this (manual resume)")
    # Which in-play states to price. NOTE: this table has no live home/away
    # score (it is always 0-0), so live rows are NOT score-accurate — a side
    # already ahead won't be deactivated. --prematch is the only fully-correct
    # mode. Default is --complete (no filter) so the choice is explicit.
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--prematch", dest="mode", action="store_const", const="prematch",
                      help="price prematch only (in_play=0, score 0-0) — fully correct")
    mode.add_argument("--live", dest="mode", action="store_const", const="live",
                      help="price live only (in_play=1) — NOT score-accurate (no live score in source)")
    mode.add_argument("--complete", dest="mode", action="store_const", const="complete",
                      help="price both prematch and live (default)")
    ap.set_defaults(mode="complete")
    args = ap.parse_args()

    in_play = {"prematch": 0, "live": 1, "complete": None}[args.mode]

    engines = tuple(e.strip() for e in args.engines.split(",") if e.strip())
    valid = {"v3", "v4"}
    if not engines or set(engines) - valid:
        ap.error(f"--engines must be a comma list from {sorted(valid)}; got {args.engines!r}")
    if args.aggregate_brands and args.brand:
        ap.error("--aggregate-brands pools all brands; do not also pass --brand")
    if args.resume and args.recreate:
        ap.error("--resume continues an existing table; do not pass --recreate")
    if args.resume and args.shards > 1:
        ap.error("--resume (by max event_id) is only valid for an unsharded run. "
                 "To resume a sharded run use --start-shard K (and --min-event-id if the "
                 "original run had a floor); see scripts/RECONSTRUCT_README.md.")
    # Next-goal only feeds V3's FTTS 1UP; skip the market entirely for V4-only.
    include_next_goal = "v3" in engines
    print(f"engines: {', '.join(engines)}"
          f"{' | aggregating brands' if args.aggregate_brands else ''}"
          f"{'' if include_next_goal else ' | next-goal skipped (V4-only)'}", flush=True)

    end_shard = args.shards if args.end_shard is None else args.end_shard
    if args.shards < 1 or not (0 <= args.start_shard < end_shard <= args.shards):
        ap.error("require --shards >= 1 and 0 <= --start-shard < --end-shard <= --shards")

    setup_client = chio.connect()
    if args.mode != "prematch":
        # Probe whether the source actually carries a live score; only warn if
        # it doesn't (the table was historically all 0-0 for live).
        has_live_score = bool(setup_client.query(
            queries.live_score_probe_sql(args.source, brand=args.brand)).result_rows)
        if has_live_score:
            print("live scores present in source — live rows priced with real scores.",
                  flush=True)
        else:
            print("WARNING: no live home/away score found in source; live rows are "
                  "priced as 0-0, so already-ahead sides are NOT deactivated. "
                  "Use --prematch for fully-correct output.", flush=True)
    # --recreate only makes sense before the first shard; resuming must not drop.
    if args.recreate and args.start_shard == 0:
        setup_client.command(queries.drop_table_sql(args.output))
    setup_client.command(queries.output_ddl(args.output))

    min_event_id = args.min_event_id
    if args.resume:
        m = setup_client.query(queries.max_event_id_sql(args.output)).result_rows[0][0]
        if not m:
            ap.error("--resume: output table is empty — run a fresh job (no --resume)")
        print(f"resume: highest event in output is {m}; clearing it and continuing "
              f"from event_id >= {m} ...", flush=True)
        # Wait for the delete mutation to finish before we re-insert (mutations_sync=2).
        setup_client.command(queries.delete_from_event_sql(args.output, int(m)),
                             settings={"mutations_sync": 2})
        min_event_id = int(m)
    setup_client.close()        # shards open their own connections

    restore = pricing.install_dp_cache()
    # These are report counters of rows *processed*. A retried shard is delete+
    # reinserted (the table stays correct), but it is re-streamed, so on a retry
    # these can exceed the table's final row count. The table is the source of
    # truth; the report counts are indicative.
    n_scanned = n_out = n_1up = n_prematch = n_live = flagged = stale_max = inserted = 0
    stale_samples = []          # 1-in-N sample of staleness for report percentiles
    STALE_SAMPLE_EVERY = 25
    STALE_SAMPLE_CAP = 500_000  # bound the sample even on huge runs

    def _counting_scan(it):
        nonlocal n_scanned
        for r in it:
            n_scanned += 1
            if n_scanned % 1_000_000 == 0:
                print(f"  ...scanned {n_scanned:,} source rows, "
                      f"emitted {n_out:,} priced rows", flush=True)
            yield r

    def _accounting(it):
        nonlocal n_out, n_1up, n_prematch, n_live, flagged, stale_max
        for row in it:
            n_out += 1
            # "1UP priced" = actual V4 1UP output (V4 prematch 1UP is DP-direct
            # and needs no next-goal data), not FTTS availability.
            n_1up += 1 if row.get("v4_1up_home_odds") is not None else 0
            n_prematch += 0 if row["in_play"] else 1
            n_live += 1 if row["in_play"] else 0
            if abs(row["renorm_drift"]) > c.RENORM_DRIFT_TOL:
                flagged += 1
            s = row["max_input_staleness_seconds"]
            if s > stale_max:
                stale_max = s
            if n_out % STALE_SAMPLE_EVERY == 0 and len(stale_samples) < STALE_SAMPLE_CAP:
                stale_samples.append(s)   # bounded-memory sample
            yield row

    sharded = args.shards > 1

    def _run_one_shard(k, clean_first):
        """Stream+price+insert one shard, with reconnect-clean-retry on a
        connection error. `clean_first` deletes any existing rows for this shard
        before processing (used for a resumed/retried, possibly-partial shard)."""
        nonlocal inserted
        label = f"shard {k + 1}/{args.shards}" if sharded else "source"
        last_exc = None
        for attempt in range(args.shard_retries + 1):
            client = chio.connect()
            try:
                if (clean_first or attempt > 0) and sharded:
                    print(f"  cleaning existing rows for {label} first ...", flush=True)
                    client.command(
                        queries.delete_shard_sql(args.output, args.shards, k, min_event_id),
                        settings={"mutations_sync": 2})
                sql = queries.extraction_sql(
                    args.source, brand=args.brand, in_play=in_play,
                    sample_mod=args.sample_mod, limit=args.limit,
                    aggregate_brands=args.aggregate_brands,
                    include_next_goal=include_next_goal,
                    shard_index=k if sharded else None,
                    shard_count=args.shards if sharded else None,
                    min_event_id=min_event_id)
                suffix = f" (attempt {attempt + 1})" if attempt else ""
                print(f"streaming {label}{suffix} ...", flush=True)
                rows_stream = _counting_scan(chio.stream_rows(client, sql))
                moments = pricing.moments_from_rows(
                    rows_stream, aggregate_brands=args.aggregate_brands,
                    fresh_seconds=args.max_staleness)
                priced = pricing.run_pricing(moments, run_ts=args.run_ts, engines=engines)
                inserted += chio.insert_rows(client, args.output, _accounting(priced),
                                             columns=c.OUTPUT_COLUMNS,
                                             batch_size=args.batch_size)
                return
            except Exception as exc:                       # noqa: BLE001 - retried below
                last_exc = exc
                if attempt < args.shard_retries:
                    print(f"  {label} failed (attempt {attempt + 1}): {exc}\n"
                          f"  reconnecting, cleaning, and retrying ...", flush=True)
                    time.sleep(min(2 ** attempt, 30))
                else:
                    raise RuntimeError(f"{label} failed after {args.shard_retries + 1} "
                                       f"attempts: {last_exc}") from last_exc
            finally:
                client.close()

    try:
        for k in range(args.start_shard, end_shard):
            _run_one_shard(k, clean_first=(sharded and k == args.start_shard
                                           and args.start_shard > 0))
            if sharded:
                print(f"  shard {k + 1}/{args.shards} done — {inserted:,} rows so far",
                      flush=True)
        cache_info = pricing.dp_cache_info()
    finally:
        restore()

    Path(args.report).write_text(
        report.build_report(source_table=args.source, output_table=args.output,
                            n_out=n_out, n_1up=n_1up, n_prematch=n_prematch,
                            n_live=n_live, staleness_samples=stale_samples,
                            staleness_max=stale_max, flagged_drift=flagged,
                            n_scanned=n_scanned, cache_info=cache_info),
        encoding="utf-8")
    print(f"inserted {inserted} rows -> {args.output} ({n_1up} with 1UP, "
          f"{n_prematch} prematch / {n_live} live)")


if __name__ == "__main__":
    main()
