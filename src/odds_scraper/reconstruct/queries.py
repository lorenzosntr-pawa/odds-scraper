"""SQL for ClickHouse 1UP/2UP reconstruction: aligned extraction (ASOF JOIN)
and the output-table DDL."""
from __future__ import annotations

from .constants import (MARKET_1X2, MARKET_OU_TOTAL, MARKET_OU_HOME,
                        MARKET_OU_AWAY, OUTPUT_COLUMNS)

# Markets we anchor O/U + next-goal to. The 1X2 snapshot is the anchor; every
# other selection is snapped to the nearest 1X2 odds_timestamp within the same
# (event_id, in_play). Next-goal is matched by the "{n} Goal" name pattern so
# live can use any line; the active line is chosen in Python by score.
_NON_ANCHOR_FILTER = (
    f"market_name IN ('{MARKET_OU_TOTAL}', '{MARKET_OU_HOME}', '{MARKET_OU_AWAY}') "
    f"OR match(market_name, '^[0-9]+ Goal$')"
)


def extraction_sql(source_table: str) -> str:
    """Return long aligned rows ordered by (event_id, in_play, moment_ts).
    Columns: event_id, sr_id, brand, event_name, sr_start_time, in_play,
    moment_ts, home_score, away_score, market_name, line, selection_name,
    true_proba, sel_ts."""
    return f"""
WITH anchor AS (
    SELECT event_id, sr_id, brand, event_name, sr_start_time, in_play,
           odds_timestamp AS moment_ts, home_score, away_score,
           selection_name, true_proba
    FROM {source_table}
    WHERE market_name = '{MARKET_1X2}'
      AND true_proba IS NOT NULL AND true_proba != 0
),
other AS (
    SELECT event_id, in_play, odds_timestamp AS sel_ts,
           market_name, handicap / 4.0 AS line, selection_name, true_proba,
           home_score, away_score
    FROM {source_table}
    WHERE ({_NON_ANCHOR_FILTER})
      AND true_proba IS NOT NULL AND true_proba != 0
)
SELECT a.event_id, a.sr_id, a.brand, a.event_name, a.sr_start_time,
       a.in_play, a.moment_ts, a.home_score, a.away_score,
       a.selection_name AS x12_selection, a.true_proba AS x12_proba,
       o.market_name, o.line, o.selection_name, o.true_proba, o.sel_ts
FROM anchor AS a
ASOF LEFT JOIN other AS o
  ON a.event_id = o.event_id AND a.in_play = o.in_play
 AND o.sel_ts <= a.moment_ts
ORDER BY a.event_id, a.in_play, a.moment_ts
"""


def output_ddl(output_table: str) -> str:
    """MergeTree DDL covering OUTPUT_COLUMNS. Strings for ids/labels, Float64
    for probs/odds, Int for scores, DateTime for timestamps."""
    string_cols = {"run_ts", "brand", "event_id", "sr_id", "event_name",
                   "sr_start_time", "moment_ts"}
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
