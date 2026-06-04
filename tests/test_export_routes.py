import csv
import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from odds_scraper.db_schema import init_schema
from odds_scraper.web.app import create_app


@pytest.fixture
def export_db_path(tmp_path: Path) -> Path:
    p = tmp_path / "odds.db"
    conn = sqlite3.connect(str(p), isolation_level=None)
    init_schema(conn)
    conn.close()
    _seed(p)
    return p


@pytest.fixture
def export_client(export_db_path: Path) -> TestClient:
    return TestClient(create_app(db_path=export_db_path))


def _seed(db_path: Path) -> None:
    """One event with a 1x2 + 1UP tick and a pricer_live_results row so the
    long/wide/sim export paths all have data in the regime=any/density=all scope.
    """
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc, "
        "country_id, country_name, league_id, league_name) "
        "VALUES ('E1', 'Liverpool', 'Arsenal', '2026-05-22T18:30:00Z', "
        "'288', 'England', '11965', 'Premier League')",
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status, "
        "match_minute, score_home, score_away) "
        "VALUES ('2026-05-21T10:00:00Z', 'E1', 'betpawa', 'UPCOMING', 'ok', "
        "0, 0, 0)",
    )
    snap_id = cur.lastrowid
    inserts = [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("1x2_1up_ft", 0.0, "home", 1.50, 0.66),
        ("1x2_1up_ft", 0.0, "away", 2.60, 0.38),
    ]
    for mid, line, side, odds, prob in inserts:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, mid, line, side, odds, prob),
        )
    # A pricer_live_results row so the sim export path yields OUR rows.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pricer_live_results)")}
    sim_vals = {
        "event_id": "E1", "ts_utc": "2026-05-21T10:00:00Z",
        "basis_used": "betpawa",
        "v3_1up_home_capped": 1.45, "v3_1up_away_capped": 2.70,
        "v3_p_home_1": 0.69, "v3_p_away_1": 0.37,
        "v4_1up_home_capped": 1.47, "v4_1up_away_capped": 2.65,
        "v4_p_home_1": 0.68, "v4_p_away_1": 0.38,
    }
    use = {k: v for k, v in sim_vals.items() if k in cols}
    placeholders = ", ".join("?" for _ in use)
    conn.execute(
        f"INSERT INTO pricer_live_results ({', '.join(use)}) VALUES ({placeholders})",
        tuple(use.values()),
    )
    conn.close()


def test_export_page_renders(export_client):
    r = export_client.get("/export")
    assert r.status_code == 200
    assert "Export" in r.text


def test_export_csv_long_streams_rows(export_client):
    r = export_client.get("/export.csv", params={"regime": "any", "density": "all", "format": "long"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    reader = list(csv.DictReader(io.StringIO(r.text)))
    assert reader and "bookmaker" in reader[0] and "odds" in reader[0]


def test_export_csv_wide_format(export_client):
    r = export_client.get("/export.csv", params={"regime": "any", "density": "all", "format": "wide"})
    assert r.status_code == 200
    reader = list(csv.DictReader(io.StringIO(r.text)))
    # wide: metadata cols present, at least one value column
    assert reader and "event_id" in reader[0]


def test_export_csv_with_sim_filename_suffix(export_client):
    r = export_client.get("/export.csv", params={"regime": "any", "density": "all", "format": "long", "sim": "1", "engine": ["v4"]})
    assert "_with_simulated" in r.headers["content-disposition"]


def test_export_csv_bad_regime_400(export_client):
    r = export_client.get("/export.csv", params={"regime": "bogus"})
    assert r.status_code == 400


def test_export_count_badge(export_client):
    r = export_client.get("/export/count", params={"regime": "any", "density": "all"})
    assert r.status_code == 200
    assert "snapshots" in r.text.lower() or "event" in r.text.lower()


def test_export_markets_options(export_client):
    r = export_client.get("/export/markets")
    assert r.status_code == 200
    assert 'name="market"' in r.text
