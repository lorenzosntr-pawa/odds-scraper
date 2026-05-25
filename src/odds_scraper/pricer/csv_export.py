from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable


CSV_COLUMNS = (
    "snapshot_id", "event_id",
    "home", "away", "kickoff_utc",
    "ts_utc",
    "status", "match_minute", "score_home", "score_away",
    "basis_used",
    "lambda_home", "lambda_away",
    # OUR engine output. `*_capped_ev` is the EV of OUR probability
    # against OUR capped odds (= negative of the engine's embedded
    # margin for that selection) — useful when BP isn't quoting and
    # you want to see the simulated edge against the engine's own
    # offer rather than nothing at all.
    "our_p_home_1", "our_p_away_1",
    "our_1up_home_fair", "our_1up_home_capped", "our_1up_home_capped_ev",
    "our_1up_away_fair", "our_1up_away_capped", "our_1up_away_capped_ev",
    "our_p_home_2", "our_p_away_2",
    "our_2up_home_fair", "our_2up_home_capped", "our_2up_home_capped_ev",
    "our_2up_away_fair", "our_2up_away_capped", "our_2up_away_capped_ev",
    # BP / SB carry per-selection true prob + odds + EV. EV uses OUR
    # probability against the book's odds (`our_prob * book_odds - 1`)
    # — that's the actionable edge. The `*_p_*` column is the book's
    # own devigged probability so the reader can compare OUR vs the
    # book's view at a glance. B9J / BW have no devigged probability
    # stored, so they remain odds-only.
    "bp_p_1up_home", "bp_1up_home_odds", "bp_1up_home_ev",
    "bp_p_1up_away", "bp_1up_away_odds", "bp_1up_away_ev",
    "bp_p_2up_home", "bp_2up_home_odds", "bp_2up_home_ev",
    "bp_p_2up_away", "bp_2up_away_odds", "bp_2up_away_ev",
    "sb_p_1up_home", "sb_1up_home_odds", "sb_1up_home_ev",
    "sb_p_1up_away", "sb_1up_away_odds", "sb_1up_away_ev",
    "sb_p_2up_home", "sb_2up_home_odds", "sb_2up_home_ev",
    "sb_p_2up_away", "sb_2up_away_odds", "sb_2up_away_ev",
    "b9j_1up_home_odds", "b9j_1up_away_odds",
    "b9j_2up_home_odds", "b9j_2up_away_odds",
    "bw_1up_home_odds",  "bw_1up_away_odds",
    "bw_2up_home_odds",  "bw_2up_away_odds",
)


def write_csv(out_path: Path, rows: Iterable[tuple]) -> None:
    """Materialise the run output to disk. Pure file IO — no DB roundtrip,
    no temporary tables. Rows must be in `CSV_COLUMNS` order.

    The header is always written (so an empty run still produces a
    parseable file with column names)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row)
