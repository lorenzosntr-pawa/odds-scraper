import sqlite3
from pathlib import Path

import pytest

from odds_scraper.db_schema import init_schema
from odds_scraper.pricer import configs


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "x.db"), isolation_level=None)
    init_schema(c)
    c.row_factory = sqlite3.Row
    return c


def test_load_default_returns_seeded_baseline(conn):
    p = configs.load_default(conn)
    assert p.name == "default"
    assert p.is_default is True
    assert p.coefficients["ONEUP_FAVORITE_MARGIN"] == [0.9969, 0.0313]


def test_list_profiles_initially_returns_only_default(conn):
    rows = configs.list_profiles(conn)
    assert [r.name for r in rows] == ["default"]


def test_create_profile_persists_named_overrides(conn):
    over = dict(configs.DEFAULT_COEFFICIENTS)
    over["TWOUP_FAVORITE_BOOST_COEFFICIENT"] = 0.85
    new_id = configs.create_profile(conn, "boost-85", over)
    assert new_id > 1
    loaded = configs.load_by_id(conn, new_id)
    assert loaded.name == "boost-85"
    assert loaded.is_default is False
    assert loaded.coefficients["TWOUP_FAVORITE_BOOST_COEFFICIENT"] == 0.85


def test_create_profile_rejects_unknown_coefficient_names(conn):
    bad = dict(configs.DEFAULT_COEFFICIENTS)
    bad["BOGUS_KNOB"] = 1.23
    with pytest.raises(ValueError, match="unknown coefficient"):
        configs.create_profile(conn, "bad", bad)


def test_delete_profile_removes_named(conn):
    new_id = configs.create_profile(conn, "tmp", configs.DEFAULT_COEFFICIENTS)
    configs.delete_profile(conn, new_id)
    assert configs.load_by_id(conn, new_id) is None


def test_delete_default_raises(conn):
    default_id = configs.load_default(conn).id
    with pytest.raises(ValueError, match="default"):
        configs.delete_profile(conn, default_id)


def test_apply_to_engine_module_normalises_tuples(conn):
    """List values for tuple constants must round-trip back to tuples
    when applied to the engine module — engine.py reads tuples and the
    code unpacks them positionally."""
    overrides = configs.coefficients_to_engine_overrides(
        configs.DEFAULT_COEFFICIENTS
    )
    assert overrides["ONEUP_FAVORITE_MARGIN"] == (0.9969, 0.0313)
    assert isinstance(overrides["ONEUP_FAVORITE_MARGIN"], tuple)
    assert overrides["TWOUP_FAVORITE_BOOST_COEFFICIENT"] == 0.9
