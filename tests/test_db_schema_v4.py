import json
import sqlite3
from pathlib import Path

from odds_scraper.db_schema import init_schema, SCHEMA_VERSION


def test_schema_v4_creates_pricer_tables(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    # pricer_configs survives — profiles persist across schema versions.
    # pricer_runs and pricer_results were dropped in v8 (simulator
    # writes CSV only, no DB rows), so they're not asserted here.
    assert "pricer_configs" in tables
    assert SCHEMA_VERSION >= 4


def test_schema_v4_seeds_default_config(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    rows = conn.execute(
        "SELECT name, is_default, coefficients FROM pricer_configs"
    ).fetchall()
    assert len(rows) == 1
    name, is_default, coeff_json = rows[0]
    assert name == "default"
    assert is_default == 1
    coeffs = json.loads(coeff_json)
    assert coeffs["ONEUP_FAVORITE_MARGIN"] == [0.9969, 0.0313]
    assert coeffs["TWOUP_FAVORITE_BOOST_COEFFICIENT"] == 0.9


def test_schema_v8_dropped_pricer_runs_and_results(tmp_path: Path):
    """v8 removed the pricer_runs / pricer_results tables — the
    simulator writes CSV files only now."""
    conn = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(conn)
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "pricer_runs" not in tables
    assert "pricer_results" not in tables
