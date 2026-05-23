from __future__ import annotations

import sqlite3
from pathlib import Path


def write_run_csv(conn: sqlite3.Connection, run_id: int, out_path: Path) -> None:
    """Stub — replaced by Task 6 with the real wide-CSV implementation.

    For Task 5 we only need the import to resolve and the file to be
    created so the runner contract holds. Task 6 adds the real impl
    + its own tests.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.touch()
