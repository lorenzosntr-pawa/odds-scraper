"""SQL for ClickHouse 1UP/2UP reconstruction: the source extraction scan and
the output-table DDL.

Alignment is done in Python (carry-forward; see pricing.moments_from_rows),
NOT in SQL: ClickHouse ASOF JOIN is strictly 1:1 and cannot gather every O/U
line plus all three next-goal selections for one moment. So the extraction is
a single ordered scan of all relevant selections; the carry-forward reducer
assembles each moment from the latest value of every series."""
from __future__ import annotations

import re

from .constants import (MARKET_1X2, MARKET_OU_TOTAL, MARKET_OU_HOME,
                        MARKET_OU_AWAY, MARKET_NEXT_GOAL, OUTPUT_COLUMNS)

# Markets we scan: 1X2, the three O/U families, and (optionally) the next-goal
# market. The next-goal market_name is the literal "{handicap} Goal" (the goal
# number is in the handicap column); we keep every goal line so live can pick
# the active one by score. Next-goal only feeds V3's FTTS 1UP — V4's 1UP is
# DP-direct — so when V3 isn't computed we drop it entirely (smaller scan).
def _market_filter(include_next_goal: bool) -> str:
    parts = [
        f"market_name = '{MARKET_1X2}'",
        f"market_name IN ('{MARKET_OU_TOTAL}', '{MARKET_OU_HOME}', '{MARKET_OU_AWAY}')",
    ]
    if include_next_goal:
        parts.append(f"market_name = '{MARKET_NEXT_GOAL}'")
    return " OR ".join(parts)

# Operator-supplied identifiers (db.table). Guard so a malformed name fails
# loudly here rather than producing confusing SQL.
_IDENT_RE = re.compile(r"^[A-Za-z0-9_.]+$")
_BRAND_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _check_ident(name: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"unsafe table identifier: {name!r}")
    return name


def extraction_sql(source_table: str, *, brand: str | None = None,
                   in_play: int | None = None, sample_mod: int | None = None,
                   limit: int | None = None, aggregate_brands: bool = False,
                   include_next_goal: bool = True,
                   shard_index: int | None = None,
                   shard_count: int | None = None,
                   min_event_id: int | None = None) -> str:
    """Return one row per (selection, timestamp) for every relevant market,
    ordered by (brand, event_id, in_play, odds_timestamp) so the Python
    carry-forward reducer sees each (brand, event)'s prematch then live
    captures in time order — the table duplicates each event across brands, so
    brand must lead the grouping or brands would interleave.

    Optional `brand` restricts to one brand (recommended for a first run on
    this 250M-row table). `in_play` selects 0 (prematch only), 1 (live only),
    or None (both). `sample_mod` keeps ~1/N of events spread across the whole
    id range (whole events, so carry-forward stays intact) — a representative
    smoke, unlike `limit` which just grabs the lowest event_ids. `limit` caps
    total rows scanned.
    Columns: event_id, sr_id, brand, event_name, sr_start_time, in_play, ts,
    home_score, away_score, market_name, line, selection_name, true_proba."""
    _check_ident(source_table)
    where = [f"({_market_filter(include_next_goal)})",
             "true_proba IS NOT NULL", "true_proba != 0"]
    if brand is not None:
        if not _BRAND_RE.match(brand):
            raise ValueError(f"unsafe brand filter: {brand!r}")
        where.append(f"brand = '{brand}'")
    if in_play is not None:
        where.append(f"in_play = {int(bool(in_play))}")
    if sample_mod is not None:
        if int(sample_mod) < 1:
            raise ValueError("sample_mod must be >= 1")
        where.append(f"cityHash64(event_id) % {int(sample_mod)} = 0")
    if shard_count is not None:
        if not (0 <= int(shard_index or 0) < int(shard_count)) or int(shard_count) < 1:
            raise ValueError("require 0 <= shard_index < shard_count")
        where.append(f"cityHash64(event_id) % {int(shard_count)} = {int(shard_index)}")
    if min_event_id is not None:
        where.append(f"event_id >= {int(min_event_id)}")
    # Aggregate mode pools all brands (true_proba is brand-independent), so we
    # order by event first (not brand) — every brand's captures for an event
    # interleave by time, densifying the timeline. Otherwise brand leads so the
    # reducer keeps each brand's stream separate.
    order_by = ("event_id, in_play, odds_timestamp" if aggregate_brands
                else "brand, event_id, in_play, odds_timestamp")
    sql = f"""
SELECT event_id, sr_id, brand, event_name, sr_start_time, in_play,
       odds_timestamp AS ts, home_score, away_score,
       market_name, handicap / 4.0 AS line, selection_name, true_proba
FROM {source_table}
WHERE {' AND '.join(where)}
ORDER BY {order_by}
"""
    if limit is not None:
        sql = sql.rstrip() + f"\nLIMIT {int(limit)}\n"
    return sql


def drop_table_sql(table: str) -> str:
    _check_ident(table)
    return f"DROP TABLE IF EXISTS {table}"


def max_event_id_sql(table: str) -> str:
    _check_ident(table)
    return f"SELECT max(event_id) FROM {table}"


def delete_from_event_sql(table: str, min_event_id: int) -> str:
    """Delete rows for events at/above min_event_id — used on resume to clear
    the possibly-partial boundary event before continuing."""
    _check_ident(table)
    return f"ALTER TABLE {table} DELETE WHERE event_id >= {int(min_event_id)}"


def delete_shard_sql(table: str, shard_count: int, shard_index: int,
                     min_event_id: int | None = None) -> str:
    """Delete one shard's rows (optionally only at/above a floor) — used to
    clean a partially-written shard before re-processing it."""
    _check_ident(table)
    cond = f"cityHash64(event_id) % {int(shard_count)} = {int(shard_index)}"
    if min_event_id is not None:
        cond += f" AND event_id >= {int(min_event_id)}"
    return f"ALTER TABLE {table} DELETE WHERE {cond}"


def live_score_probe_sql(source_table: str, *, brand: str | None = None) -> str:
    """Returns a row iff the source has at least one live snapshot with a
    non-zero home/away score — i.e. live scoring is actually available.
    Cheap: ClickHouse short-circuits on the LIMIT 1."""
    _check_ident(source_table)
    bf = ""
    if brand is not None:
        if not _BRAND_RE.match(brand):
            raise ValueError(f"unsafe brand filter: {brand!r}")
        bf = f" AND brand = '{brand}'"
    return (f"SELECT 1 FROM {source_table} "
            f"WHERE in_play AND (home_score != 0 OR away_score != 0){bf} LIMIT 1")


def output_ddl(output_table: str) -> str:
    """MergeTree DDL covering OUTPUT_COLUMNS. Column types match the Python
    values we insert (which in turn mirror the source column types): event_id
    is the source UInt64, sr_start_time a DateTime, scores Int32, in_play a
    flag, everything numeric Nullable(Float64)."""
    _check_ident(output_table)
    string_cols = {"run_ts", "brand", "sr_id", "event_name", "moment_ts"}
    uint64_cols = {"event_id"}
    datetime_cols = {"sr_start_time"}
    int_cols = {"home_score", "away_score", "max_input_staleness_seconds"}
    uint8_cols = {"in_play"}
    defs = []
    for col in OUTPUT_COLUMNS:
        if col in string_cols:
            t = "String"
        elif col in uint64_cols:
            t = "UInt64"
        elif col in datetime_cols:
            t = "DateTime"
        elif col in int_cols:
            t = "Int32"
        elif col in uint8_cols:
            t = "UInt8"
        else:
            t = "Nullable(Float64)"
        defs.append(f"    `{col}` {t}")
    cols_sql = ",\n".join(defs)
    return (f"CREATE TABLE IF NOT EXISTS {output_table} (\n{cols_sql}\n) "
            f"ENGINE = MergeTree ORDER BY (event_id, in_play, moment_ts)")
