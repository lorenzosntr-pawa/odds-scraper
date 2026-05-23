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
    "TWOUP_FAVORITE_MARGIN",
    "TWOUP_UNDERDOG_MARGIN",
    "TWOUP_FAVORITE_BOOST_COEFFICIENT",
    "TWOUP_UNDERDOG_BOOST_COEFFICIENT",
    "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION",
    "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION",
    "TWOUP_TRAILING_MIN_REDUCTION",
    "TWOUP_TRAILING_MAX_REDUCTION",
)

# Coefficient names whose engine value is a (slope, intercept) tuple.
# All others are plain floats.
_TUPLE_NAMES = frozenset({
    "ONEUP_FAVORITE_MARGIN", "ONEUP_UNDERDOG_MARGIN",
    "TWOUP_FAVORITE_MARGIN", "TWOUP_UNDERDOG_MARGIN",
})

DEFAULT_COEFFICIENTS = {
    "ONEUP_FAVORITE_MARGIN": [0.9969, 0.0313],
    "ONEUP_UNDERDOG_MARGIN": [0.9799, 0.0400],
    "ONEUP_MIN_GUARANTEED_REDUCTION": 0.02,
    "ONEUP_TRAILING_MIN_REDUCTION": 0.05,
    "ONEUP_TRAILING_MAX_REDUCTION": 0.25,
    "TWOUP_FAVORITE_MARGIN": [0.998, 0.010],
    "TWOUP_UNDERDOG_MARGIN": [0.994, 0.008],
    "TWOUP_FAVORITE_BOOST_COEFFICIENT": 0.9,
    "TWOUP_UNDERDOG_BOOST_COEFFICIENT": 0.6,
    "TWOUP_FAVORITE_MIN_GUARANTEED_REDUCTION": 0.02,
    "TWOUP_UNDERDOG_MIN_GUARANTEED_REDUCTION": 0.005,
    "TWOUP_TRAILING_MIN_REDUCTION": 0.05,
    "TWOUP_TRAILING_MAX_REDUCTION": 0.25,
}


@dataclass(frozen=True)
class Profile:
    id: int
    name: str
    created_at: str
    is_default: bool
    coefficients: dict


def _row_to_profile(row: sqlite3.Row) -> Profile:
    return Profile(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        is_default=bool(row["is_default"]),
        coefficients=json.loads(row["coefficients"]),
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


def create_profile(conn: sqlite3.Connection, name: str, coefficients: dict) -> int:
    # Validate keys before insert so partial data never lands in the DB.
    unknown = set(coefficients) - set(TUNABLE_NAMES)
    if unknown:
        raise ValueError(f"unknown coefficient names: {sorted(unknown)}")
    missing = set(TUNABLE_NAMES) - set(coefficients)
    if missing:
        raise ValueError(f"missing coefficient names: {sorted(missing)}")
    cur = conn.execute(
        "INSERT INTO pricer_configs (name, created_at, is_default, coefficients) "
        "VALUES (?, datetime('now'), 0, ?)",
        (name, json.dumps(coefficients)),
    )
    return cur.lastrowid


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
    for scalars. Use this just before applying via with_coefficients()."""
    out: dict = {}
    for k in TUNABLE_NAMES:
        v = coefficients[k]
        if k in _TUPLE_NAMES:
            out[k] = tuple(v)
        else:
            out[k] = v
    return out
