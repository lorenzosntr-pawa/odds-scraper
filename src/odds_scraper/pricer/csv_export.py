from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


CSV_COLUMNS = (
    "run_id", "event_id", "home", "away", "kickoff_utc",
    "snapshot_id", "ts_utc",
    # Per-tick state from the snapshots table — makes the CSV readable
    # without cross-referencing the DB. `status` flags the regime
    # (UPCOMING / STARTED / ENDED); minute + score apply when in-play.
    "status", "match_minute", "score_home", "score_away",
    "basis_used",
    "lambda_home", "lambda_away",
    "our_p_home_1", "our_p_away_1",
    "our_1up_home_fair", "our_1up_home_capped",
    "our_1up_away_fair", "our_1up_away_capped",
    "our_p_home_2", "our_p_away_2",
    "our_2up_home_fair", "our_2up_home_capped",
    "our_2up_away_fair", "our_2up_away_capped",
    "bp_1up_home_odds",  "bp_1up_away_odds",
    "bp_2up_home_odds",  "bp_2up_away_odds",
    "sb_1up_home_odds",  "sb_1up_away_odds",
    "sb_2up_home_odds",  "sb_2up_away_odds",
    "b9j_1up_home_odds", "b9j_1up_away_odds",
    "b9j_2up_home_odds", "b9j_2up_away_odds",
    "bw_1up_home_odds",  "bw_1up_away_odds",
    "bw_2up_home_odds",  "bw_2up_away_odds",
)


def write_run_csv(
    conn: sqlite3.Connection, run_id: int, out_path: Path,
) -> None:
    """Materialise pricer_results for `run_id` as a wide CSV.

    Joins event metadata (home, away, kickoff_utc) onto each row so the
    CSV reads standalone — no DB needed downstream.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """
        SELECT
            r.run_id, r.event_id, e.home, e.away, e.kickoff_utc,
            r.snapshot_id, r.ts_utc,
            s.status, s.match_minute, s.score_home, s.score_away,
            r.basis_used,
            r.lambda_home, r.lambda_away,
            r.our_p_home_1, r.our_p_away_1,
            r.our_1up_home_fair, r.our_1up_home_capped,
            r.our_1up_away_fair, r.our_1up_away_capped,
            r.our_p_home_2, r.our_p_away_2,
            r.our_2up_home_fair, r.our_2up_home_capped,
            r.our_2up_away_fair, r.our_2up_away_capped,
            r.bp_1up_home_odds,  r.bp_1up_away_odds,
            r.bp_2up_home_odds,  r.bp_2up_away_odds,
            r.sb_1up_home_odds,  r.sb_1up_away_odds,
            r.sb_2up_home_odds,  r.sb_2up_away_odds,
            r.b9j_1up_home_odds, r.b9j_1up_away_odds,
            r.b9j_2up_home_odds, r.b9j_2up_away_odds,
            r.bw_1up_home_odds,  r.bw_1up_away_odds,
            r.bw_2up_home_odds,  r.bw_2up_away_odds
        FROM pricer_results r
        JOIN events e ON e.id = r.event_id
        -- LEFT JOIN: the snapshot row should always exist (FK), but
        -- if a manual cleanup ever deleted it, NULL is better than
        -- dropping the result row from the CSV.
        LEFT JOIN snapshots s ON s.id = r.snapshot_id
        WHERE r.run_id = ?
        ORDER BY r.event_id, r.ts_utc
        """,
        (run_id,),
    ).fetchall()

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row)
