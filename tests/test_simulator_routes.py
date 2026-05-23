import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from odds_scraper.db_schema import init_schema
from odds_scraper.web.app import create_app


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "odds.db"
    conn = sqlite3.connect(str(p), isolation_level=None)
    init_schema(conn)
    conn.close()
    return p


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path=db_path))


def test_simulator_page_renders(client: TestClient):
    r = client.get("/simulator")
    assert r.status_code == 200
    assert "Pricer Simulator" in r.text
    # Default profile is present in the selector
    assert "default" in r.text
    # Coverage radio options
    for cov in ("all", "latest", "prematch", "live"):
        assert f'value="{cov}"' in r.text
    # Run button
    assert "Run simulation" in r.text


def test_index_links_to_simulator(client: TestClient):
    r = client.get("/")
    assert 'href="/simulator"' in r.text


def _seed_priced_event(db_path: Path):
    """Single event with full inputs at one timestamp."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc) "
        "VALUES ('E1', 'Liverpool', 'Arsenal', '2026-05-22T18:30:00Z')",
    )
    cur = conn.execute(
        "INSERT INTO snapshots (ts_utc, event_id, bookmaker, status, fetch_status) "
        "VALUES ('2026-05-21T10:00:00Z', 'E1', 'betpawa', 'UPCOMING', 'ok')",
    )
    snap_id = cur.lastrowid
    inserts = [
        ("1x2_ft", 0.0, "home", 1.85, 0.54),
        ("1x2_ft", 0.0, "draw", 3.40, 0.29),
        ("1x2_ft", 0.0, "away", 4.20, 0.17),
        ("over_under_ft", 2.5, "over",  1.85, 0.55),
        ("over_under_ft", 2.5, "under", 1.95, 0.45),
        ("next_goal_ft", 1.0, "home", 1.85, 0.54),
        ("next_goal_ft", 1.0, "none", 8.50, 0.12),
        ("next_goal_ft", 1.0, "away", 3.50, 0.34),
    ]
    for mid, line, side, odds, prob in inserts:
        conn.execute(
            "INSERT INTO prices (snapshot_id, event_id, ts_utc, bookmaker, "
            "market_id, line, side, odds, probability) "
            "VALUES (?, 'E1', '2026-05-21T10:00:00Z', 'betpawa', ?, ?, ?, ?, ?)",
            (snap_id, mid, line, side, odds, prob),
        )
    conn.close()


def test_post_run_creates_row_and_csv(db_path: Path, client: TestClient):
    _seed_priced_event(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()["id"]
    conn.close()
    r = client.post(
        "/simulator/runs",
        data={
            "config_id": default_id, "coverage": "all",
            "status": "upcoming", "country": "", "league": "",
            "date": "", "search": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/simulator" in r.headers["location"]
    # Check that a run + at least one result row + CSV file landed.
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    run = conn.execute(
        "SELECT id, n_rows, csv_path FROM pricer_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run["n_rows"] == 1
    csv_full = db_path.parent / run["csv_path"]
    assert csv_full.exists()
    n_result_rows = conn.execute(
        "SELECT COUNT(*) FROM pricer_results WHERE run_id = ?", (run["id"],),
    ).fetchone()[0]
    assert n_result_rows == 1
    conn.close()


def test_get_run_csv_streams_file(db_path: Path, client: TestClient):
    _seed_priced_event(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()["id"]
    conn.close()
    client.post(
        "/simulator/runs",
        data={"config_id": default_id, "coverage": "all", "status": "upcoming",
              "country": "", "league": "", "date": "", "search": ""},
        follow_redirects=False,
    )
    conn = sqlite3.connect(str(db_path))
    run_id = conn.execute("SELECT id FROM pricer_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    r = client.get(f"/simulator/runs/{run_id}/csv")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    body = r.text
    assert "event_id" in body.splitlines()[0]  # header
    assert "E1" in body


# ---------------------------------------------------------------------------
# Profile management UI
# ---------------------------------------------------------------------------

def test_profiles_page_renders(client: TestClient):
    r = client.get("/simulator/profiles")
    assert r.status_code == 200
    assert "default" in r.text  # default profile listed
    assert "Create new profile" in r.text
    # Margin field pairs use _slope / _intercept naming
    assert 'name="ONEUP_FAVORITE_MARGIN_slope"' in r.text
    assert 'name="TWOUP_FAVORITE_BOOST_COEFFICIENT"' in r.text


def test_simulator_runs_page_has_profiles_tab(client: TestClient):
    r = client.get("/simulator")
    assert 'href="/simulator/profiles"' in r.text


def _full_profile_form_data(name: str) -> dict:
    """Mirrors what the create-profile form sends. Tweak any single field
    in the caller to test that the override survives the round-trip."""
    return {
        "name": name,
        "ONEUP_FAVORITE_MARGIN_slope":     "0.9969",
        "ONEUP_FAVORITE_MARGIN_intercept": "0.0313",
        "ONEUP_UNDERDOG_MARGIN_slope":     "0.9799",
        "ONEUP_UNDERDOG_MARGIN_intercept": "0.0400",
        "ONEUP_MIN_GUARANTEED_REDUCTION":  "0.02",
        "ONEUP_TRAILING_MIN_REDUCTION":    "0.05",
        "ONEUP_TRAILING_MAX_REDUCTION":    "0.25",
        "TWOUP_FAVORITE_MARGIN_slope":     "0.998",
        "TWOUP_FAVORITE_MARGIN_intercept": "0.010",
        "TWOUP_UNDERDOG_MARGIN_slope":     "0.994",
        "TWOUP_UNDERDOG_MARGIN_intercept": "0.008",
        "TWOUP_FAVORITE_BOOST_COEFFICIENT": "0.9",
        "TWOUP_UNDERDOG_BOOST_COEFFICIENT": "0.6",
        "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION": "0.02",
        "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION": "0.005",
        "TWOUP_TRAILING_MIN_REDUCTION": "0.05",
        "TWOUP_TRAILING_MAX_REDUCTION": "0.25",
    }


def test_create_profile_persists_and_redirects(db_path: Path, client: TestClient):
    data = _full_profile_form_data("boost-85")
    data["TWOUP_FAVORITE_BOOST_COEFFICIENT"] = "0.85"
    r = client.post(
        "/simulator/profiles", data=data, follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/simulator/profiles"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT name, is_default, coefficients FROM pricer_configs WHERE name=?",
        ("boost-85",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["is_default"] == 0
    import json as _json
    coeffs = _json.loads(row["coefficients"])
    assert coeffs["TWOUP_FAVORITE_BOOST_COEFFICIENT"] == 0.85
    assert coeffs["ONEUP_FAVORITE_MARGIN"] == [0.9969, 0.0313]


def test_create_profile_rejects_blank_name(client: TestClient):
    data = _full_profile_form_data("")
    r = client.post("/simulator/profiles", data=data, follow_redirects=False)
    assert r.status_code == 400


def test_create_profile_rejects_duplicate_name(client: TestClient):
    data = _full_profile_form_data("dup")
    r1 = client.post("/simulator/profiles", data=data, follow_redirects=False)
    assert r1.status_code == 303
    r2 = client.post("/simulator/profiles", data=data, follow_redirects=False)
    assert r2.status_code == 400


def test_delete_profile_removes_custom(db_path: Path, client: TestClient):
    data = _full_profile_form_data("to-delete")
    client.post("/simulator/profiles", data=data, follow_redirects=False)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    pid = conn.execute(
        "SELECT id FROM pricer_configs WHERE name=?", ("to-delete",),
    ).fetchone()["id"]
    conn.close()
    r = client.post(
        f"/simulator/profiles/{pid}/delete", follow_redirects=False,
    )
    assert r.status_code == 303
    conn = sqlite3.connect(str(db_path))
    gone = conn.execute(
        "SELECT 1 FROM pricer_configs WHERE id=?", (pid,),
    ).fetchone()
    conn.close()
    assert gone is None


def test_delete_default_profile_returns_400(db_path: Path, client: TestClient):
    conn = sqlite3.connect(str(db_path))
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()[0]
    conn.close()
    r = client.post(
        f"/simulator/profiles/{default_id}/delete", follow_redirects=False,
    )
    assert r.status_code == 400
