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


def test_update_profile_replaces_name_and_coefficients(conn):
    new_id = configs.create_profile(conn, "boost-85", configs.DEFAULT_COEFFICIENTS)
    tweaked = dict(configs.DEFAULT_COEFFICIENTS)
    tweaked["TWOUP_FAVORITE_BOOST_COEFFICIENT"] = 0.77
    configs.update_profile(conn, new_id, "boost-77", tweaked)
    reloaded = configs.load_by_id(conn, new_id)
    assert reloaded.name == "boost-77"
    assert reloaded.coefficients["TWOUP_FAVORITE_BOOST_COEFFICIENT"] == 0.77
    # Untouched fields must keep their original values.
    assert reloaded.coefficients["ONEUP_FAVORITE_MARGIN"] == [0.9969, 0.0313]


def test_update_profile_refuses_default(conn):
    default_id = configs.load_default(conn).id
    with pytest.raises(ValueError, match="default"):
        configs.update_profile(
            conn, default_id, "default", configs.DEFAULT_COEFFICIENTS,
        )


def test_update_profile_rejects_unknown_coefficient_names(conn):
    new_id = configs.create_profile(conn, "x", configs.DEFAULT_COEFFICIENTS)
    bad = dict(configs.DEFAULT_COEFFICIENTS)
    bad["BOGUS_KNOB"] = 1.0
    with pytest.raises(ValueError, match="unknown coefficient"):
        configs.update_profile(conn, new_id, "x", bad)


def test_update_profile_rejects_missing_coefficient_names(conn):
    new_id = configs.create_profile(conn, "x", configs.DEFAULT_COEFFICIENTS)
    partial = dict(configs.DEFAULT_COEFFICIENTS)
    del partial["TWOUP_FAVORITE_BOOST_COEFFICIENT"]
    with pytest.raises(ValueError, match="missing coefficient"):
        configs.update_profile(conn, new_id, "x", partial)


def test_update_profile_unknown_id_raises(conn):
    with pytest.raises(ValueError, match="no such profile"):
        configs.update_profile(conn, 9999, "ghost", configs.DEFAULT_COEFFICIENTS)


def test_legacy_profile_without_flags_loads_with_defaults(conn):
    """A row written before flags existed must still round-trip — the
    loader fills missing flag keys from DEFAULT_FLAGS so callers never
    see an incomplete coefficients dict."""
    import json as _json
    # Bypass create_profile to write a row missing the flag fields,
    # mimicking what's in the DB from before this change.
    legacy = {k: v for k, v in configs.DEFAULT_COEFFICIENTS.items()
              if k not in configs.FLAG_NAMES}
    conn.execute(
        "INSERT INTO pricer_configs (name, created_at, is_default, coefficients) "
        "VALUES ('legacy', datetime('now'), 0, ?)",
        (_json.dumps(legacy),),
    )
    pid = conn.execute(
        "SELECT id FROM pricer_configs WHERE name='legacy'"
    ).fetchone()[0]
    p = configs.load_by_id(conn, pid)
    for k, v in configs.DEFAULT_FLAGS.items():
        assert p.coefficients[k] is v


def test_create_profile_accepts_flags(conn):
    over = dict(configs.DEFAULT_COEFFICIENTS)
    over["ONEUP_MARGIN_BLEND_ENABLED"] = False
    over["TWOUP_MARGIN_BLEND_ENABLED"] = False
    new_id = configs.create_profile(conn, "no-blend", over)
    loaded = configs.load_by_id(conn, new_id)
    assert loaded.coefficients["ONEUP_MARGIN_BLEND_ENABLED"] is False
    assert loaded.coefficients["TWOUP_MARGIN_BLEND_ENABLED"] is False
    # Untouched flag falls through to its default.
    assert loaded.coefficients["TWOUP_BOOST_BLEND_ENABLED"] is True


def test_create_profile_fills_missing_flags_with_defaults(conn):
    """Callers can omit flags entirely — backward-compat for clients
    that don't know about them yet (e.g. an older form submission)."""
    only_numeric = {k: v for k, v in configs.DEFAULT_COEFFICIENTS.items()
                    if k not in configs.FLAG_NAMES}
    new_id = configs.create_profile(conn, "numeric-only", only_numeric)
    loaded = configs.load_by_id(conn, new_id)
    for k, v in configs.DEFAULT_FLAGS.items():
        assert loaded.coefficients[k] is v


def test_engine_overrides_include_flags(conn):
    """`coefficients_to_engine_overrides` must surface the flags so
    `with_coefficients` can apply them on the engine module."""
    overrides = configs.coefficients_to_engine_overrides({
        **configs.DEFAULT_COEFFICIENTS,
        "ONEUP_MARGIN_BLEND_ENABLED": False,
    })
    assert overrides["ONEUP_MARGIN_BLEND_ENABLED"] is False
    assert overrides["TWOUP_MARGIN_BLEND_ENABLED"] is True


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


def test_default_coefficients_use_v2_dog_margin_intercept(conn):
    """Schema-seeded default profile uses the V2 dog-margin intercept
    (0.014). Custom profiles created before this change keep their
    saved values — no implicit migration."""
    default = configs.load_default(conn)
    assert default.coefficients["TWOUP_UNDERDOG_MARGIN"] == [0.994, 0.014]


def test_create_profile_without_v1v2_legacy_fields_backfills(conn):
    """A V3/V4-only form submission that omits the 11 V1/V2-era tunables
    (because the profile panel no longer renders them) must succeed and the
    loaded profile must have those fields backfilled from DEFAULT_COEFFICIENTS.
    This locks the bug where omitting them caused a 400 ('missing coefficient')."""
    # Build a coefficients dict with ONLY the V3/V4 knobs — none of the 11
    # legacy fields that the form no longer renders.
    coeffs = {
        k: configs.DEFAULT_COEFFICIENTS[k]
        for k in configs.TUNABLE_NAMES
        if k not in configs._V1V2_LEGACY_TUNABLE_NAMES
    }
    # Include all flag fields as a real browser submission would (checkboxes
    # that are visible on the panel).
    for f in configs.FLAG_NAMES:
        coeffs[f] = True

    pid = configs.create_profile(conn, "v34only", coeffs)
    loaded = configs.load_by_id(conn, pid)
    assert loaded is not None

    # Every one of the 11 legacy fields must be backfilled from defaults.
    for k in configs._V1V2_LEGACY_TUNABLE_NAMES:
        assert loaded.coefficients[k] == configs.DEFAULT_COEFFICIENTS[k], (
            f"{k} not backfilled correctly"
        )

    # Spot-check two representative fields explicitly.
    assert loaded.coefficients["ONEUP_FAVORITE_MARGIN"] == configs.DEFAULT_COEFFICIENTS["ONEUP_FAVORITE_MARGIN"]
    assert loaded.coefficients["TWOUP_TRAILING_MIN_REDUCTION"] == configs.DEFAULT_COEFFICIENTS["TWOUP_TRAILING_MIN_REDUCTION"]

    # V3/V4 fields the form DID send must also be stored correctly.
    assert loaded.coefficients["ONEUP_MARGIN_LEVEL"] == configs.DEFAULT_COEFFICIENTS["ONEUP_MARGIN_LEVEL"]
    assert loaded.coefficients["TWOUP_FAVORITE_BOOST_COEFFICIENT"] == configs.DEFAULT_COEFFICIENTS["TWOUP_FAVORITE_BOOST_COEFFICIENT"]
