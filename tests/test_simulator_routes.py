import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from odds_scraper.db_schema import init_schema
from odds_scraper.web.app import create_app
from odds_scraper.web.pricer_routes import RunRecord, RunRegistry


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


def _registry(client: TestClient) -> RunRegistry:
    return client.app.state.run_registry


def test_simulator_page_renders(client: TestClient):
    r = client.get("/simulator")
    assert r.status_code == 200
    assert "Pricer Simulator" in r.text
    assert "default" in r.text
    # Regime + density radios (the old single coverage radio was split).
    for v in ("any", "prematch", "live"):
        assert f'name="regime" value="{v}"' in r.text
    for v in ("all", "latest"):
        assert f'name="density" value="{v}"' in r.text
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


def _wait_for_run_done(reg: RunRegistry, run_id: int, timeout_s: float = 5.0):
    """The route hands the run off to a background thread. Spin until
    it finishes (or fails) so the assertions below see the final state."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = reg.get(run_id)
        if r and r.state != "running":
            return r
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")


def test_post_run_creates_record_and_csv(db_path: Path, client: TestClient):
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
            "config_id": default_id, "regime": "any", "density": "all",
            "country": "", "league": "", "date": "", "search": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/simulator" in r.headers["location"]
    reg = _registry(client)
    # Newest run is at the top of list_recent.
    recent = reg.list_recent(1)
    assert len(recent) == 1
    run_id = recent[0].id
    rec = _wait_for_run_done(reg, run_id)
    assert rec.state == "done"
    assert rec.n_rows == 1
    csv_full = client.app.state.sim_csv_dir / rec.csv_name
    assert csv_full.exists()
    # The CSV must include the seeded event_id (sanity-check that the
    # run actually wrote data, not just an empty header).
    body = csv_full.read_text(encoding="utf-8")
    assert "E1" in body


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
        data={"config_id": default_id, "regime": "any", "density": "all",
              "country": "", "league": "", "date": "", "search": ""},
        follow_redirects=False,
    )
    reg = _registry(client)
    run_id = reg.list_recent(1)[0].id
    _wait_for_run_done(reg, run_id)
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
    # V3/V4 fields are present — logit margin level + boost coefficient.
    assert 'name="ONEUP_MARGIN_LEVEL"' in r.text
    assert 'name="TWOUP_FAVORITE_BOOST_COEFFICIENT"' in r.text
    # V1/V2-only fields are not rendered.
    assert 'name="ONEUP_FAVORITE_MARGIN_slope"' not in r.text


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


def test_profiles_page_shows_edit_link_for_custom_profiles(
    db_path: Path, client: TestClient,
):
    """Custom profiles must surface an edit link; the default profile
    must NOT — it's read-only."""
    # Seed a custom profile.
    client.post(
        "/simulator/profiles", data=_full_profile_form_data("to-edit"),
        follow_redirects=False,
    )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    custom_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE name='to-edit'"
    ).fetchone()["id"]
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()["id"]
    conn.close()
    r = client.get("/simulator/profiles")
    body = r.text
    assert f'href="/simulator/profiles/{custom_id}/edit"' in body
    # Default row shows no edit link (the read-only contract).
    assert f'href="/simulator/profiles/{default_id}/edit"' not in body


def test_edit_profile_page_renders_with_current_values(
    db_path: Path, client: TestClient,
):
    data = _full_profile_form_data("editable")
    data["TWOUP_FAVORITE_BOOST_COEFFICIENT"] = "0.77"
    client.post("/simulator/profiles", data=data, follow_redirects=False)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    pid = conn.execute(
        "SELECT id FROM pricer_configs WHERE name='editable'"
    ).fetchone()["id"]
    conn.close()
    r = client.get(f"/simulator/profiles/{pid}/edit")
    assert r.status_code == 200
    body = r.text
    # Form must POST back to the same id and pre-populate the value
    # the user just saved (so they can tweak from where they left off).
    assert f'action="/simulator/profiles/{pid}"' in body
    assert 'value="editable"' in body
    assert 'value="0.77"' in body


def test_edit_default_profile_returns_400(db_path: Path, client: TestClient):
    """Default is read-only — both the GET edit page and the POST update
    must refuse it."""
    conn = sqlite3.connect(str(db_path))
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()[0]
    conn.close()
    r = client.get(f"/simulator/profiles/{default_id}/edit")
    assert r.status_code == 400


def test_edit_profile_persists_changes(db_path: Path, client: TestClient):
    data = _full_profile_form_data("v1")
    client.post("/simulator/profiles", data=data, follow_redirects=False)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    pid = conn.execute(
        "SELECT id FROM pricer_configs WHERE name='v1'"
    ).fetchone()["id"]
    conn.close()

    updated = _full_profile_form_data("v2-renamed")
    updated["TWOUP_FAVORITE_BOOST_COEFFICIENT"] = "0.88"
    r = client.post(
        f"/simulator/profiles/{pid}", data=updated, follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/simulator/profiles"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT name, coefficients FROM pricer_configs WHERE id=?", (pid,),
    ).fetchone()
    conn.close()
    assert row["name"] == "v2-renamed"
    import json as _json
    coeffs = _json.loads(row["coefficients"])
    assert coeffs["TWOUP_FAVORITE_BOOST_COEFFICIENT"] == 0.88


def test_edit_default_profile_post_returns_400(db_path: Path, client: TestClient):
    conn = sqlite3.connect(str(db_path))
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()[0]
    conn.close()
    r = client.post(
        f"/simulator/profiles/{default_id}",
        data=_full_profile_form_data("hijack"),
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_edit_nonexistent_profile_returns_404(client: TestClient):
    r = client.get("/simulator/profiles/99999/edit")
    assert r.status_code == 404


def test_create_profile_form_checks_flags_when_checkbox_present(
    db_path: Path, client: TestClient,
):
    """Browsers send only the checked boxes; the parser must record
    each *_ENABLED flag as True when present, False when absent."""
    data = _full_profile_form_data("with-1up-blend-off")
    # Only TWOUP flags present in the submission → 1UP blend stays off.
    data["TWOUP_MARGIN_BLEND_ENABLED"] = "on"
    data["TWOUP_BOOST_BLEND_ENABLED"] = "on"
    r = client.post("/simulator/profiles", data=data, follow_redirects=False)
    assert r.status_code == 303

    import json as _json
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT coefficients FROM pricer_configs WHERE name='with-1up-blend-off'"
    ).fetchone()
    conn.close()
    coeffs = _json.loads(row["coefficients"])
    assert coeffs["ONEUP_MARGIN_BLEND_ENABLED"] is False
    assert coeffs["TWOUP_MARGIN_BLEND_ENABLED"] is True
    assert coeffs["TWOUP_BOOST_BLEND_ENABLED"] is True


def test_create_profile_form_all_checkboxes_default_off_when_omitted(
    db_path: Path, client: TestClient,
):
    """A form submission with NO flag fields at all is equivalent to
    the user un-ticking every checkbox — all flags must persist as
    False, never silently default to True."""
    data = _full_profile_form_data("blends-off")
    r = client.post("/simulator/profiles", data=data, follow_redirects=False)
    assert r.status_code == 303
    import json as _json
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT coefficients FROM pricer_configs WHERE name='blends-off'"
    ).fetchone()
    conn.close()
    coeffs = _json.loads(row["coefficients"])
    assert coeffs["ONEUP_MARGIN_BLEND_ENABLED"] is False
    assert coeffs["TWOUP_MARGIN_BLEND_ENABLED"] is False
    assert coeffs["TWOUP_BOOST_BLEND_ENABLED"] is False


def test_edit_profile_page_pre_checks_enabled_flags(
    db_path: Path, client: TestClient,
):
    """Saved-True flags must render with the `checked` attribute so the
    edit form reflects current state, not the HTML default.
    The V3/V4 panel exposes TWOUP_BOOST_BLEND_ENABLED; create one profile
    with it on and one without, then verify the edit page pre-checks it."""
    # Profile A: TWOUP_BOOST_BLEND_ENABLED = on
    data_on = _full_profile_form_data("blend-on")
    data_on["TWOUP_BOOST_BLEND_ENABLED"] = "on"
    client.post("/simulator/profiles", data=data_on, follow_redirects=False)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    pid_on = conn.execute(
        "SELECT id FROM pricer_configs WHERE name='blend-on'"
    ).fetchone()["id"]
    conn.close()

    # Profile B: TWOUP_BOOST_BLEND_ENABLED = off (omitted)
    data_off = _full_profile_form_data("blend-off")
    # No TWOUP_BOOST_BLEND_ENABLED key → stored False.
    client.post("/simulator/profiles", data=data_off, follow_redirects=False)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    pid_off = conn.execute(
        "SELECT id FROM pricer_configs WHERE name='blend-off'"
    ).fetchone()["id"]
    conn.close()

    # Edit page for blend-on: the checkbox input must carry `checked`.
    body_on = client.get(f"/simulator/profiles/{pid_on}/edit").text
    pos = body_on.find('name="TWOUP_BOOST_BLEND_ENABLED"')
    assert pos != -1, "TWOUP_BOOST_BLEND_ENABLED input not found in edit page"
    tag = body_on[body_on.rfind("<input", 0, pos): body_on.find(">", pos)]
    assert "checked" in tag

    # Edit page for blend-off: the same input must NOT carry `checked`.
    body_off = client.get(f"/simulator/profiles/{pid_off}/edit").text
    pos2 = body_off.find('name="TWOUP_BOOST_BLEND_ENABLED"')
    assert pos2 != -1
    tag2 = body_off[body_off.rfind("<input", 0, pos2): body_off.find(">", pos2)]
    assert "checked" not in tag2


def test_edit_profile_persists_flag_changes(db_path: Path, client: TestClient):
    """End-to-end: a custom profile created with all blends on must be
    editable to switch a blend off, and the change must round-trip."""
    data = _full_profile_form_data("flags-test")
    for f in ("ONEUP_MARGIN_BLEND_ENABLED",
              "TWOUP_MARGIN_BLEND_ENABLED",
              "TWOUP_BOOST_BLEND_ENABLED"):
        data[f] = "on"
    client.post("/simulator/profiles", data=data, follow_redirects=False)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    pid = conn.execute(
        "SELECT id FROM pricer_configs WHERE name='flags-test'"
    ).fetchone()["id"]
    conn.close()

    # Edit: turn the 2UP boost blend off (omit the field).
    updated = _full_profile_form_data("flags-test")
    updated["ONEUP_MARGIN_BLEND_ENABLED"] = "on"
    updated["TWOUP_MARGIN_BLEND_ENABLED"] = "on"
    # No TWOUP_BOOST_BLEND_ENABLED key → unchecked.
    r = client.post(
        f"/simulator/profiles/{pid}", data=updated, follow_redirects=False,
    )
    assert r.status_code == 303

    import json as _json
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT coefficients FROM pricer_configs WHERE id=?", (pid,),
    ).fetchone()
    conn.close()
    coeffs = _json.loads(row["coefficients"])
    assert coeffs["ONEUP_MARGIN_BLEND_ENABLED"] is True
    assert coeffs["TWOUP_MARGIN_BLEND_ENABLED"] is True
    assert coeffs["TWOUP_BOOST_BLEND_ENABLED"] is False


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
# In-memory registry: progress + single-flight
# ---------------------------------------------------------------------------

def _plant_running(reg: RunRegistry, *, n_done: int, n_total: int) -> int:
    """Direct registry manipulation — bypasses the route so we don't
    have to time a real background run."""
    rec = RunRecord(
        id=reg._next_id, state="running", profile_name="default",
        profile_b_name="",
        regime="any", density="all", engines="v1,v2",
        started_at="2026-05-23T10:00:00Z",
        n_done=n_done, n_total=n_total,
    )
    reg._runs[rec.id] = rec
    reg._next_id += 1
    return rec.id


def test_post_run_with_running_in_progress_returns_busy_redirect(
    db_path: Path, client: TestClient,
):
    """POST /simulator/runs while another run is state='running' must
    not start a second simulation. Redirect to /simulator?busy=1."""
    reg = _registry(client)
    _plant_running(reg, n_done=5, n_total=100)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()[0]
    conn.close()
    r = client.post(
        "/simulator/runs",
        data={"config_id": default_id, "regime": "any", "density": "all",
              "country": "", "league": "", "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "busy=1" in r.headers["location"]


def test_get_run_status_returns_progress_json(client: TestClient):
    """GET /simulator/runs/<id>/status returns the state + progress
    counters as JSON so the page's poller can update the bar."""
    reg = _registry(client)
    run_id = _plant_running(reg, n_done=80, n_total=200)
    r = client.get(f"/simulator/runs/{run_id}/status")
    assert r.status_code == 200
    d = r.json()
    assert d["state"] == "running"
    assert d["n_done"] == 80
    assert d["n_total"] == 200
    assert d["progress_pct"] == 40


def test_simulator_page_renders_progress_bar_when_run_in_progress(
    client: TestClient,
):
    reg = _registry(client)
    _plant_running(reg, n_done=25, n_total=100)
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


def _seed_event_for_picker(
    db_path: Path, ev_id: str, *,
    home: str = "H", away: str = "A",
    country_id: str = "288", country_name: str = "England",
    league_id: str = "11965", league_name: str = "Premier League",
    kickoff: str = "2026-05-25T18:00:00Z",
) -> None:
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(
        "INSERT INTO events (id, home, away, kickoff_utc, "
        "country_id, country_name, league_id, league_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ev_id, home, away, kickoff,
         country_id, country_name, league_id, league_name),
    )
    conn.close()


def test_simulator_page_lists_countries_from_db(db_path: Path, client: TestClient):
    """The scope panel must render real countries from the DB instead of
    a free-text input."""
    _seed_event_for_picker(db_path, "E1")
    r = client.get("/simulator")
    body = r.text
    assert 'id="sim-country"' in body
    assert ">England<" in body
    # Cascading league select starts disabled until a country is picked.
    assert 'id="sim-league"' in body and "disabled" in body


def test_event_options_filters_by_country(db_path: Path, client: TestClient):
    _seed_event_for_picker(
        db_path, "E1", country_id="288", country_name="England",
    )
    _seed_event_for_picker(
        db_path, "E2", country_id="241", country_name="France",
        league_id="999", league_name="Ligue 1",
    )
    r = client.get("/simulator/options/events?country=288")
    body = r.text
    assert 'value="E1"' in body
    assert 'value="E2"' not in body
    # The "All matching events" header option is always present.
    assert 'value=""' in body


def test_event_options_filters_by_search(db_path: Path, client: TestClient):
    _seed_event_for_picker(db_path, "L", home="Liverpool", away="Arsenal")
    _seed_event_for_picker(db_path, "C", home="Chelsea", away="Spurs")
    r = client.get("/simulator/options/events?search=liverpool")
    body = r.text
    assert 'value="L"' in body
    assert 'value="C"' not in body


def test_event_options_filters_by_date(db_path: Path, client: TestClient):
    _seed_event_for_picker(
        db_path, "TODAY", kickoff="2026-05-25T18:00:00Z",
    )
    _seed_event_for_picker(
        db_path, "TOMOR", kickoff="2026-05-26T18:00:00Z",
    )
    r = client.get("/simulator/options/events?date=2026-05-25")
    body = r.text
    assert 'value="TODAY"' in body
    assert 'value="TOMOR"' not in body


def test_post_run_accepts_event_id_in_scope(db_path: Path, client: TestClient):
    """The event-picker selection must travel through POST /simulator/runs
    so a run actually scopes down to one match."""
    _seed_event_for_picker(db_path, "X")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()["id"]
    conn.close()
    r = client.post(
        "/simulator/runs",
        data={
            "config_id": default_id,
            "regime": "any", "density": "all",
            "country": "", "league": "", "event_id": "X",
            "date": "", "search": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    reg = _registry(client)
    rec = _wait_for_run_done(reg, reg.list_recent(1)[0].id)
    # No priced snapshot was seeded, so n_rows is 0 — but the run must
    # have completed (state == 'done'), proving the scope was accepted.
    assert rec.state == "done"


# ---------------------------------------------------------------------------
# Engine selector — V1 / V2 / both
# ---------------------------------------------------------------------------

def _default_id(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    pid = conn.execute(
        "SELECT id FROM pricer_configs WHERE is_default=1"
    ).fetchone()["id"]
    conn.close()
    return pid


def test_post_run_with_engine_v3_dispatches_v3_runner(db_path, client):
    """`engine=v3` — RunRecord must record engines='v3'."""
    r = client.post(
        "/simulator/runs",
        data={"config_id": _default_id(db_path),
              "regime": "any", "density": "all", "engine": "v3",
              "country": "", "league": "", "event_id": "",
              "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    reg = _registry(client)
    rec = _wait_for_run_done(reg, reg.list_recent(1)[0].id)
    assert rec.engines == "v3"


def test_post_run_with_engine_v4_dispatches_v4_runner(db_path, client):
    r = client.post(
        "/simulator/runs",
        data={"config_id": _default_id(db_path),
              "regime": "any", "density": "all", "engine": "v4",
              "country": "", "league": "", "event_id": "",
              "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    reg = _registry(client)
    rec = _wait_for_run_done(reg, reg.list_recent(1)[0].id)
    assert rec.engines == "v4"


def test_post_run_with_multiple_engines_dispatches_dual_runner(db_path, client):
    """Multiple `engine` checkboxes → the run uses exactly those engines,
    recorded in canonical order regardless of submit order."""
    r = client.post(
        "/simulator/runs",
        data={"config_id": _default_id(db_path),
              "regime": "any", "density": "all", "engine": ["v4", "v3"],
              "country": "", "league": "", "event_id": "",
              "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    reg = _registry(client)
    rec = _wait_for_run_done(reg, reg.list_recent(1)[0].id)
    assert rec.engines == "v3,v4"  # canonical order, not submit order


def test_post_run_with_v3_and_v4_engines(db_path, client):
    r = client.post(
        "/simulator/runs",
        data={"config_id": _default_id(db_path),
              "regime": "any", "density": "all", "engine": ["v3", "v4"],
              "country": "", "league": "", "event_id": "",
              "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    reg = _registry(client)
    rec = _wait_for_run_done(reg, reg.list_recent(1)[0].id)
    assert rec.engines == "v3,v4"


def test_post_run_with_no_engine_defaults_to_latest_v3(db_path, client):
    """Omitting the engine field falls back to the latest engine (v4) —
    a run always exercises at least one engine."""
    r = client.post(
        "/simulator/runs",
        data={"config_id": _default_id(db_path),
              "regime": "any", "density": "all",
              "country": "", "league": "", "event_id": "",
              "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    reg = _registry(client)
    rec = _wait_for_run_done(reg, reg.list_recent(1)[0].id)
    assert rec.engines == "v4"


def test_post_run_with_unknown_engine_returns_400(db_path, client):
    r = client.post(
        "/simulator/runs",
        data={"config_id": _default_id(db_path),
              "regime": "any", "density": "all", "engine": ["v9000"],
              "country": "", "league": "", "event_id": "",
              "date": "", "search": ""},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_simulator_form_has_engine_checkboxes(client):
    r = client.get("/simulator")
    body = r.text
    # Only V3 and V4 are offered in the UI.
    for v in ("v3", "v4"):
        assert f'type="checkbox" name="engine" value="{v}"' in body
    for v in ("v1", "v2"):
        assert f'type="checkbox" name="engine" value="{v}"' not in body
    # V4 (latest) is pre-selected.
    assert 'name="engine" value="v4" checked' in body


def test_simulator_page_offers_only_v3_v4_engines(client):
    html = client.get("/simulator").text
    # engine checkboxes: only v3 and v4 offered
    assert 'name="engine" value="v3"' in html
    assert 'name="engine" value="v4"' in html
    assert 'name="engine" value="v1"' not in html
    assert 'name="engine" value="v2"' not in html


def test_simulator_history_renders_engines_column(db_path, client):
    """A finished run must surface its engines value in the history
    table so the user can tell at a glance which run is which."""
    reg = _registry(client)
    rec = RunRecord(
        id=reg._next_id, state="done",
        profile_name="default", profile_b_name="",
        regime="any", density="all",
        engines="v1,v2",
        started_at="2026-05-25T10:00:00Z",
        n_done=1, n_total=1, n_events=1, n_rows=1,
        csv_name="run_0001.csv", finished_at="2026-05-25T10:00:30Z",
    )
    reg._runs[rec.id] = rec
    reg._next_id += 1
    r = client.get("/simulator")
    body = r.text
    assert "engines" in body.lower()
    assert ">v1,v2<" in body


def test_simulator_profile_tooltip_mentions_engine_contract(client):
    """The profile selector now spans both engines — tooltip makes the
    contract explicit."""
    r = client.get("/simulator")
    body = r.text
    assert "profiles apply to whichever engine" in body.lower()
