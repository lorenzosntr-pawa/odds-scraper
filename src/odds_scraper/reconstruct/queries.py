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

# All selections we need, in one scan: 1X2, the three O/U families, and the
# next-goal market. The next-goal market_name is the literal "{handicap} Goal"
# (the goal number is in the handicap column); we keep every goal line so live
# can pick the active one in Python by score. MARKET_NEXT_GOAL already holds
# the literal braces, so interpolating it here needs no f-string escaping.
_MARKET_FILTER = (
    f"market_name = '{MARKET_1X2}' "
    f"OR market_name IN ('{MARKET_OU_TOTAL}', '{MARKET_OU_HOME}', '{MARKET_OU_AWAY}') "
    f"OR market_name = '{MARKET_NEXT_GOAL}'"
)

# Operator-supplied identifiers (db.table). Guard so a malformed name fails
# loudly here rather than producing confusing SQL.
_IDENT_RE = re.compile(r"^[A-Za-z0-9_.]+$")
_BRAND_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _check_ident(name: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"unsafe table identifier: {name!r}")
    return name


def extraction_sql(source_table: str, *, brand: str | None = None,
                   limit: int | None = None) -> str:
    """Return one row per (selection, timestamp) for every relevant market,
    ordered by (brand, event_id, in_play, odds_timestamp) so the Python
    carry-forward reducer sees each (brand, event)'s prematch then live
    captures in time order — the table duplicates each event across brands, so
    brand must lead the grouping or brands would interleave.

    Optional `brand` restricts to one brand (recommended for a first run on
    this 250M-row table); `limit` caps total rows scanned for a smoke run.
    Columns: event_id, sr_id, brand, event_name, sr_start_time, in_play, ts,
    home_score, away_score, market_name, line, selection_name, true_proba."""
    _check_ident(source_table)
    where = [f"({_MARKET_FILTER})", "true_proba IS NOT NULL", "true_proba != 0"]
    if brand is not None:
        if not _BRAND_RE.match(brand):
            raise ValueError(f"unsafe brand filter: {brand!r}")
        where.append(f"brand = '{brand}'")
    sql = f"""
SELECT event_id, sr_id, brand, event_name, sr_start_time, in_play,
       odds_timestamp AS ts, home_score, away_score,
       market_name, handicap / 4.0 AS line, selection_name, true_proba
FROM {source_table}
WHERE {' AND '.join(where)}
ORDER BY brand, event_id, in_play, odds_timestamp
"""
    if limit is not None:
        sql = sql.rstrip() + f"\nLIMIT {int(limit)}\n"
    return sql


def output_ddl(output_table: str) -> str:
    """MergeTree DDL covering OUTPUT_COLUMNS. Strings for ids/labels, Float64
    for probs/odds, Int for scores, DateTime for timestamps."""
    string_cols = {"run_ts", "brand", "event_id", "sr_id", "event_name",
                   "sr_start_time", "moment_ts"}
    _check_ident(output_table)
    int_cols = {"home_score", "away_score", "max_input_staleness_seconds"}
    bool_cols = {"in_play", "has_1up"}
    defs = []
    for col in OUTPUT_COLUMNS:
        if col in string_cols:
            t = "String"
        elif col in int_cols:
            t = "Int32"
        elif col in bool_cols:
            t = "UInt8"
        else:
            t = "Nullable(Float64)"
        defs.append(f"    `{col}` {t}")
    cols_sql = ",\n".join(defs)
    return (f"CREATE TABLE IF NOT EXISTS {output_table} (\n{cols_sql}\n) "
            f"ENGINE = MergeTree ORDER BY (event_id, in_play, moment_ts)")
