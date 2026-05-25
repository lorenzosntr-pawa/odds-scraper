from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional


TUNABLE_NAMES = (
    "ONEUP_FAVORITE_MARGIN",
    "ONEUP_UNDERDOG_MARGIN",
    "ONEUP_MIN_GUARANTEED_REDUCTION",
    "ONEUP_TRAILING_MIN_REDUCTION",
    "ONEUP_TRAILING_MAX_REDUCTION",
    "ONEUP_TRAILING_FAVORITE_MARGIN",
    "ONEUP_TRAILING_UNDERDOG_MARGIN",
    "TWOUP_FAVORITE_MARGIN",
    "TWOUP_UNDERDOG_MARGIN",
    "TWOUP_FAVORITE_BOOST_COEFFICIENT",
    "TWOUP_UNDERDOG_BOOST_COEFFICIENT",
    "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION",
    "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION",
    "TWOUP_TRAILING_MIN_REDUCTION",
    "TWOUP_TRAILING_MAX_REDUCTION",
)

# Subset of TUNABLE_NAMES that only engine_v2.py defines. V1's
# with_coefficients does getattr(engine, k) and would crash on a name
# absent from engine.py — runner_v2 filters these out before applying
# the V1 override block. Treated as optional in stored profiles
# (legacy rows backfill from DEFAULT_COEFFICIENTS at load + validate).
V2_ONLY_TUNABLE_NAMES = frozenset({
    "ONEUP_TRAILING_FAVORITE_MARGIN",
    "ONEUP_TRAILING_UNDERDOG_MARGIN",
})

# Boolean toggles. Kept separate from TUNABLE_NAMES because they are
# optional in stored profiles (legacy rows predate them and still need
# to load) — defaults below preserve the original blend-on behaviour.
FLAG_NAMES = (
    "ONEUP_MARGIN_BLEND_ENABLED",
    "TWOUP_MARGIN_BLEND_ENABLED",
    "TWOUP_BOOST_BLEND_ENABLED",
)
DEFAULT_FLAGS = {name: True for name in FLAG_NAMES}

# Coefficient names whose engine value is a (slope, intercept) tuple.
# All others are plain floats.
_TUPLE_NAMES = frozenset({
    "ONEUP_FAVORITE_MARGIN", "ONEUP_UNDERDOG_MARGIN",
    "ONEUP_TRAILING_FAVORITE_MARGIN", "ONEUP_TRAILING_UNDERDOG_MARGIN",
    "TWOUP_FAVORITE_MARGIN", "TWOUP_UNDERDOG_MARGIN",
})

DEFAULT_COEFFICIENTS = {
    "ONEUP_FAVORITE_MARGIN": [0.9969, 0.0313],
    "ONEUP_UNDERDOG_MARGIN": [0.9799, 0.0400],
    "ONEUP_MIN_GUARANTEED_REDUCTION": 0.02,
    "ONEUP_TRAILING_MIN_REDUCTION": 0.05,
    "ONEUP_TRAILING_MAX_REDUCTION": 0.25,
    "ONEUP_TRAILING_FAVORITE_MARGIN": [0.998, 0.010],
    "ONEUP_TRAILING_UNDERDOG_MARGIN": [0.994, 0.014],
    "TWOUP_FAVORITE_MARGIN": [0.998, 0.010],
    "TWOUP_UNDERDOG_MARGIN": [0.994, 0.014],
    "TWOUP_FAVORITE_BOOST_COEFFICIENT": 0.9,
    "TWOUP_UNDERDOG_BOOST_COEFFICIENT": 0.6,
    "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION": 0.02,
    "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION": 0.005,
    "TWOUP_TRAILING_MIN_REDUCTION": 0.05,
    "TWOUP_TRAILING_MAX_REDUCTION": 0.25,
    # Default flag values match Java behaviour (blends on).
    **DEFAULT_FLAGS,
}


@dataclass(frozen=True)
class Profile:
    id: int
    name: str
    created_at: str
    is_default: bool
    coefficients: dict


def _row_to_profile(row: sqlite3.Row) -> Profile:
    coeffs = json.loads(row["coefficients"])
    # Legacy rows predate the flag fields — fill them with defaults so
    # callers always see a complete coefficients dict and don't have to
    # branch on absence.
    for k, v in DEFAULT_FLAGS.items():
        coeffs.setdefault(k, v)
    # V2-only tunables get the same treatment: optional in storage,
    # backfilled from defaults on load.
    for k in V2_ONLY_TUNABLE_NAMES:
        coeffs.setdefault(k, DEFAULT_COEFFICIENTS[k])
    return Profile(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        is_default=bool(row["is_default"]),
        coefficients=coeffs,
    )


def load_default(conn: sqlite3.Connection) -> Profile:
    row = conn.execute(
        "SELECT id, name, created_at, is_default, coefficients "
        "FROM pricer_configs WHERE is_default = 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("default pricer config missing — schema v4 not applied?")
    return _row_to_profile(row)


def load_by_id(conn: sqlite3.Connection, profile_id: int) -> Optional[Profile]:
    row = conn.execute(
        "SELECT id, name, created_at, is_default, coefficients "
        "FROM pricer_configs WHERE id = ?",
        (profile_id,),
    ).fetchone()
    return _row_to_profile(row) if row else None


def list_profiles(conn: sqlite3.Connection) -> list[Profile]:
    rows = conn.execute(
        "SELECT id, name, created_at, is_default, coefficients "
        "FROM pricer_configs ORDER BY is_default DESC, name ASC"
    ).fetchall()
    return [_row_to_profile(r) for r in rows]


_ALL_NAMES = frozenset(TUNABLE_NAMES) | frozenset(FLAG_NAMES)


def _validate_and_fill(coefficients: dict) -> dict:
    """Reject unknown keys, require every numeric tunable, and backfill
    any missing boolean flag with its default. Returns a fresh dict so
    callers can rely on the result being complete."""
    unknown = set(coefficients) - _ALL_NAMES
    if unknown:
        raise ValueError(f"unknown coefficient names: {sorted(unknown)}")
    # V2-only tunables are optional (legacy profiles predate them); only
    # the V1-shared names are required.
    required_num = set(TUNABLE_NAMES) - V2_ONLY_TUNABLE_NAMES
    missing_num = required_num - set(coefficients)
    if missing_num:
        raise ValueError(f"missing coefficient names: {sorted(missing_num)}")
    out = dict(coefficients)
    for k, v in DEFAULT_FLAGS.items():
        out.setdefault(k, v)
    for k in V2_ONLY_TUNABLE_NAMES:
        out.setdefault(k, DEFAULT_COEFFICIENTS[k])
    return out


def create_profile(conn: sqlite3.Connection, name: str, coefficients: dict) -> int:
    coefficients = _validate_and_fill(coefficients)
    cur = conn.execute(
        "INSERT INTO pricer_configs (name, created_at, is_default, coefficients) "
        "VALUES (?, datetime('now'), 0, ?)",
        (name, json.dumps(coefficients)),
    )
    return cur.lastrowid


def update_profile(
    conn: sqlite3.Connection, profile_id: int,
    name: str, coefficients: dict,
) -> None:
    """Replace name + coefficients on a custom profile. Refuses to touch
    the default profile (its values are the seed reference the runner
    falls back to when no override is selected). Same key validation as
    `create_profile` so a partial update can't corrupt the row."""
    row = conn.execute(
        "SELECT is_default FROM pricer_configs WHERE id = ?", (profile_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no such profile {profile_id}")
    if row[0] == 1:
        raise ValueError("cannot edit the default pricer config")
    coefficients = _validate_and_fill(coefficients)
    conn.execute(
        "UPDATE pricer_configs SET name = ?, coefficients = ? WHERE id = ?",
        (name, json.dumps(coefficients), profile_id),
    )


def delete_profile(conn: sqlite3.Connection, profile_id: int) -> None:
    row = conn.execute(
        "SELECT is_default FROM pricer_configs WHERE id = ?", (profile_id,),
    ).fetchone()
    if row is None:
        return
    if row[0] == 1:
        raise ValueError("cannot delete the default pricer config")
    conn.execute("DELETE FROM pricer_configs WHERE id = ?", (profile_id,))


def coefficients_to_engine_overrides(coefficients: dict) -> dict:
    """Convert a stored coefficients dict (lists for tuple constants) into
    the form engine.py expects (tuples for tuple constants). Pass-through
    for scalars and boolean flags. Use this just before applying via
    with_coefficients()."""
    out: dict = {}
    for k in TUNABLE_NAMES:
        v = coefficients[k]
        if k in _TUPLE_NAMES:
            out[k] = tuple(v)
        else:
            out[k] = v
    for k in FLAG_NAMES:
        # Backfill missing flags from defaults so legacy stored profiles
        # — written before flags existed — still apply cleanly.
        out[k] = bool(coefficients.get(k, DEFAULT_FLAGS[k]))
    return out
