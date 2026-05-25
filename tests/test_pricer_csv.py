import csv
from pathlib import Path

from odds_scraper.pricer import csv_export


def _build_row(**overrides) -> tuple:
    """Build a row tuple in CSV_COLUMNS order. Defaults to a fully-populated
    row; callers override the few fields they want to assert on."""
    defaults = {
        "engines": "v1",
        "v2_p_home_1": "", "v2_p_away_1": "",
        "v2_our_1up_home_fair": "", "v2_our_1up_home_capped": "", "v2_our_1up_home_capped_ev": "",
        "v2_our_1up_away_fair": "", "v2_our_1up_away_capped": "", "v2_our_1up_away_capped_ev": "",
        "v2_p_home_2": "", "v2_p_away_2": "",
        "v2_our_2up_home_fair": "", "v2_our_2up_home_capped": "", "v2_our_2up_home_capped_ev": "",
        "v2_our_2up_away_fair": "", "v2_our_2up_away_capped": "", "v2_our_2up_away_capped_ev": "",
        "snapshot_id": 1, "event_id": "E1",
        "home": "Home FC", "away": "Away FC",
        "kickoff_utc": "2026-05-22T18:30:00Z",
        "ts_utc": "2026-05-21T10:00:00Z",
        "status": "UPCOMING", "match_minute": "", "score_home": "", "score_away": "",
        "basis_used": "bp",
        "lambda_home": 1.4, "lambda_away": 1.1,
        "our_p_home_1": 0.55, "our_p_away_1": 0.32,
        "our_1up_home_fair": 1.82, "our_1up_home_capped": 1.85,
        "our_1up_home_capped_ev": -0.0175,
        "our_1up_away_fair": 3.10, "our_1up_away_capped": 3.10,
        "our_1up_away_capped_ev": -0.008,
        "our_p_home_2": 0.65, "our_p_away_2": 0.41,
        "our_2up_home_fair": 1.83, "our_2up_home_capped": 1.85,
        "our_2up_home_capped_ev": -0.0175,
        "our_2up_away_fair": 2.40, "our_2up_away_capped": 2.40,
        "our_2up_away_capped_ev": -0.016,
        "bp_p_1up_home": 0.54, "bp_1up_home_odds": 1.85, "bp_1up_home_ev": 0.0175,
        "bp_p_1up_away": 0.32, "bp_1up_away_odds": 3.10, "bp_1up_away_ev": -0.008,
        "bp_p_2up_home": 0.54, "bp_2up_home_odds": 1.83, "bp_2up_home_ev": 0.1895,
        "bp_p_2up_away": 0.41, "bp_2up_away_odds": 2.40, "bp_2up_away_ev": -0.016,
        "sb_p_1up_home": "", "sb_1up_home_odds": "", "sb_1up_home_ev": "",
        "sb_p_1up_away": "", "sb_1up_away_odds": "", "sb_1up_away_ev": "",
        "sb_p_2up_home": "", "sb_2up_home_odds": "", "sb_2up_home_ev": "",
        "sb_p_2up_away": "", "sb_2up_away_odds": "", "sb_2up_away_ev": "",
        "b9j_1up_home_odds": "", "b9j_1up_away_odds": "",
        "b9j_2up_home_odds": "", "b9j_2up_away_odds": "",
        "bw_1up_home_odds": "", "bw_1up_away_odds": "",
        "bw_2up_home_odds": "", "bw_2up_away_odds": "",
    }
    defaults.update(overrides)
    return tuple(defaults[c] for c in csv_export.CSV_COLUMNS)


def test_write_csv_emits_header_and_rows(tmp_path: Path):
    out = tmp_path / "run_0001.csv"
    csv_export.write_csv(out, [_build_row()])
    with open(out, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == "E1"
    assert row["home"] == "Home FC"
    assert row["away"] == "Away FC"
    assert row["basis_used"] == "bp"
    assert float(row["our_2up_home_capped"]) == 1.85
    assert float(row["bp_2up_home_odds"]) == 1.83


def test_write_csv_creates_dirs_and_handles_empty_run(tmp_path: Path):
    """An empty run still produces a CSV with just headers — and the
    parent directory is created on demand so callers don't have to."""
    out = tmp_path / "sub" / "run_0002.csv"
    csv_export.write_csv(out, [])
    with open(out, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert len(lines) == 1  # header only
    assert "event_id" in lines[0]


def test_write_csv_includes_tick_state(tmp_path: Path):
    """CSV must surface per-tick regime + minute + score so a reader can
    interpret rows without joining back to the DB."""
    out = tmp_path / "run_0003.csv"
    row = _build_row(
        event_id="LIV", home="Liv", away="Ars",
        kickoff_utc="2026-05-22T18:30:00Z",
        ts_utc="2026-05-22T19:05:00Z",
        status="STARTED", match_minute=34, score_home=1, score_away=0,
    )
    csv_export.write_csv(out, [row])
    with open(out, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "STARTED"
    assert r["match_minute"] == "34"
    assert r["score_home"] == "1"
    assert r["score_away"] == "0"
    # Header order — tick-state columns sit between ts_utc and basis_used.
    with open(out, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    assert header.index("status") > header.index("ts_utc")
    assert header.index("status") < header.index("basis_used")


def test_csv_columns_include_engines_at_front_and_v2_block():
    """V2 spec: leading `engines` column, then existing V1 layout, then
    a v2 block after V1's OUR section but before bookmaker columns."""
    cols = csv_export.CSV_COLUMNS
    assert cols[0] == "engines"
    # V1 OUR block — unchanged location.
    assert "our_2up_away_capped_ev" in cols
    # New v2 block — must appear strictly after V1's OUR block.
    v1_end_idx = cols.index("our_2up_away_capped_ev")
    for v2_col in (
        "v2_p_home_1", "v2_p_away_1",
        "v2_our_1up_home_fair", "v2_our_1up_home_capped", "v2_our_1up_home_capped_ev",
        "v2_our_1up_away_fair", "v2_our_1up_away_capped", "v2_our_1up_away_capped_ev",
        "v2_p_home_2", "v2_p_away_2",
        "v2_our_2up_home_fair", "v2_our_2up_home_capped", "v2_our_2up_home_capped_ev",
        "v2_our_2up_away_fair", "v2_our_2up_away_capped", "v2_our_2up_away_capped_ev",
    ):
        assert v2_col in cols, f"missing {v2_col}"
        assert cols.index(v2_col) > v1_end_idx, f"{v2_col} comes before V1 OUR block"
    # Bookmaker columns must still come after the v2 block.
    assert cols.index("bp_p_1up_home") > cols.index("v2_our_2up_away_capped_ev")
