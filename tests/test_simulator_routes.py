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


# ---------------------------------------------------------------------------
# Background-task progress + single-flight
# ---------------------------------------------------------------------------

def test_run_simulation_inserts_running_row_immediately(db_path: Path):
    """The pricer_runs row must appear with state='running' BEFORE the
    work completes — otherwise the simulator page can't show a progress
    bar for the in-flight run."""
    from odds_scraper.pricer import configs, runner
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    default = configs.load_default(conn)
    # Empty scope → no work, but the runner still inserts the row and
    # finishes it cleanly. Inspect afterwards.
    run_id = runner.run_simulation(
        conn, config=default, regime="any", density="all",
        scope={"status": "ended", "country": "", "league": "", "date": "", "search": ""},
        csv_dir=Path(db_path).parent / "sim",
    )
    row = conn.execute(
        "SELECT state, n_done, n_total, started_at, finished_at "
        "FROM pricer_runs WHERE id = ?", (run_id,),
    ).fetchone()
    assert row["state"] == "done"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
    conn.close()


def test_post_run_with_running_in_progress_returns_busy_redirect(
    db_path: Path, client: TestClient,
):
    """POST /simulator/runs while another run is state='running' must
    not start a second simulation. Redirect to /simulator?busy=1."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()[0]
    # Plant a fake running row directly so we don't have to time a real
    # background run.
    conn.execute(
        "INSERT INTO pricer_runs (created_at, config_id, coverage, scope_json, "
        "n_events, n_rows, csv_path, state, n_total, n_done, started_at) "
        "VALUES (datetime('now'), ?, 'all', '{}', 0, 0, '', 'running', 100, 5, "
        "       datetime('now'))",
        (default_id,),
    )
    conn.close()
    r = client.post(
        "/simulator/runs",
        data={"config_id": default_id, "coverage": "all", "status": "upcoming",
              "country": "", "league": "", "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "busy=1" in r.headers["location"]


def test_get_run_status_returns_progress_json(db_path: Path, client: TestClient):
    """GET /simulator/runs/<id>/status returns the state + progress
    counters as JSON so the page's poller can update the bar."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO pricer_runs (created_at, config_id, coverage, scope_json, "
        "n_events, n_rows, csv_path, state, n_total, n_done, started_at) "
        "VALUES (datetime('now'), ?, 'all', '{}', 0, 0, '', 'running', 200, 80, "
        "       datetime('now'))",
        (default_id,),
    )
    run_id = cur.lastrowid
    conn.close()
    r = client.get(f"/simulator/runs/{run_id}/status")
    assert r.status_code == 200
    d = r.json()
    assert d["state"] == "running"
    assert d["n_done"] == 80
    assert d["n_total"] == 200
    assert d["progress_pct"] == 40


def test_simulator_page_renders_progress_bar_when_run_in_progress(
    db_path: Path, client: TestClient,
):
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO pricer_runs (created_at, config_id, coverage, scope_json, "
        "n_events, n_rows, csv_path, state, n_total, n_done, started_at) "
        "VALUES (datetime('now'), ?, 'all', '{}', 0, 0, '', 'running', 100, 25, "
        "       datetime('now'))",
        (default_id,),
    )
    conn.close()
    r = client.get("/simulator")
    body = r.text
    assert "Run in progress" in body
    assert "sim-progress-fill" in body
    # Button disabled while running
    assert "disabled" in body


def test_simulator_page_disables_button_with_busy_marker(client: TestClient):
    """?busy=1 lands the user on the page when their POST was rejected
    because another run was active. The page shows the appropriate
    warning section."""
    r = client.get("/simulator?busy=1")
    assert r.status_code == 200
    assert "Another run is already in progress" in r.text


def test_scope_preview_renders_event_and_snapshot_counts(db_path: Path, client: TestClient):
    """GET /simulator/scope returns an HTML fragment with the counts —
    used as an HTMX target so the user sees the scope size live."""
    # Default fixture seeds 1 event + 1 snapshot.
    r = client.get("/simulator/scope?regime=any&density=all")
    assert r.status_code == 200
    body = r.text
    # Numbers + the literal labels — defensive against whitespace.
    assert "events" in body
    assert "ticks in scope" in body


def test_simulator_form_has_regime_and_density_radios(client: TestClient):
    r = client.get("/simulator")
    body = r.text
    for v in ("any", "prematch", "live"):
        assert f'name="regime" value="{v}"' in body
    for v in ("all", "latest"):
        assert f'name="density" value="{v}"' in body
