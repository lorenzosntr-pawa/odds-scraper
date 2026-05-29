"""Vocabulary + tunables for ClickHouse 1UP/2UP reconstruction.

Selection labels and market names are the one place that depends on the exact
ClickHouse table vocabulary; verify them against the live table (see the
integration task) and change here only.
"""
from __future__ import annotations

# --- source market names (exact strings in bi_Samuel...) ---
MARKET_1X2 = "1X2 - FT"
MARKET_OU_TOTAL = "Total Score Over/Under - FT"
MARKET_OU_HOME = "Total Score Over/Under - FT - Home Team"
MARKET_OU_AWAY = "Total Score Over/Under - FT - Away Team"
# Next-goal market name is "{n} Goal" with handicap = n*4 (handicap/4.0 == n).

OU_MARKETS = (MARKET_OU_TOTAL, MARKET_OU_HOME, MARKET_OU_AWAY)

# --- selection labels (verify against live table) ---
SEL_HOME, SEL_DRAW, SEL_AWAY = "Home", "Draw", "Away"
SEL_OVER, SEL_UNDER = "Over", "Under"
SEL_NG_HOME, SEL_NG_AWAY, SEL_NG_NONE = "Home", "Away", "None"

# --- tunables ---
CAP_MARGIN = 0.02          # flat brand-neutral margin baked into cap reference odds
FRESH_SECONDS = 3600       # <1h staleness window for an emitted moment
RENORM_DRIFT_TOL = 0.05    # |sum(1X2 true_proba) - 1| beyond this is flagged

# --- output ---
DEFAULT_OUTPUT_TABLE = "risk_Lorenzo.oneup_twoup_reconstructed"

OUTPUT_COLUMNS = [
    "run_ts", "brand", "event_id", "sr_id", "event_name", "sr_start_time",
    "in_play", "moment_ts", "home_score", "away_score",
    "p_home", "p_draw", "p_away", "lambda_home", "lambda_away",
    "ftts_home", "ftts_away", "has_1up",
    "max_input_staleness_seconds", "renorm_drift",
]
for _e in ("v2", "v3", "v4"):
    for _m in ("1up", "2up"):
        for _s in ("home", "away"):
            OUTPUT_COLUMNS += [f"{_e}_{_m}_{_s}_odds",
                               f"{_e}_{_m}_{_s}_prob",
                               f"{_e}_{_m}_{_s}_ev"]
