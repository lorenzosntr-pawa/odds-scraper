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
# The next-goal market name is the LITERAL template string "{handicap} Goal"
# (braces included); the goal number lives in the `handicap` column, so
# handicap/4.0 gives the goal index (handicap 4 -> goal #1, the prematch next goal).
MARKET_NEXT_GOAL = "{handicap} Goal"

# --- selection labels (as spelled in the table) ---
# 1X2 (and next-goal team sides) use "1"/"X"/"2"; O/U over leg is "Over";
# next-goal no-goal is "None".
SEL_HOME, SEL_DRAW, SEL_AWAY = "1", "X", "2"
SEL_OVER = "Over"
SEL_NG_HOME, SEL_NG_AWAY, SEL_NG_NONE = "1", "2", "None"

# --- tunables ---
CAP_MARGIN = 0.02          # flat brand-neutral margin baked into cap reference odds
RENORM_DRIFT_TOL = 0.05    # |sum(1X2 true_proba) - 1| beyond this is flagged

# Confidence-weight bands (full trust at GOOD, zero at BAD, linear between).
# Drives the per-row `confidence` weight (0..1) the sim multiplies by.
STALE_GOOD_SEC, STALE_BAD_SEC = 300, 1800      # <=5min full, >=30min none
DRIFT_GOOD, DRIFT_BAD = 0.02, 0.05             # |renorm_drift| bands

# --- output ---
DEFAULT_OUTPUT_TABLE = "risk_Lorenzo.oneup_twoup_reconstructed"

OUTPUT_COLUMNS = [
    "run_ts", "brand", "event_id", "sr_id", "event_name", "sr_start_time",
    "in_play", "moment_ts", "home_score", "away_score",
    "p_home", "p_draw", "p_away", "lambda_home", "lambda_away",
    "ftts_home", "ftts_away",
    "max_input_staleness_seconds", "renorm_drift", "confidence",
]
for _e in ("v3", "v4"):
    for _m in ("1up", "2up"):
        for _s in ("home", "away"):
            OUTPUT_COLUMNS += [f"{_e}_{_m}_{_s}_odds",
                               f"{_e}_{_m}_{_s}_prob",
                               f"{_e}_{_m}_{_s}_ev"]
