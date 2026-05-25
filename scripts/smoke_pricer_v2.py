"""Run V1 + V2 side-by-side on a small slice of the live DB.

Reports:
  - Total rows
  - V1 invariant violations (1UP capped > 2UP capped, i.e. V1's
    heuristic-based 1UP trailing pricing inverting monotonicity).
    Sub-counted on STARTED rows where V1 was known to violate ~11.6%.
  - V2 invariant violations in the DP-driven regime (|diff| < 2) —
    target 0.

Usage:
    python scripts/smoke_pricer_v2.py
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from odds_scraper.pricer import configs, runner_v2


def main() -> None:
    db_path = Path("data/odds.db")
    if not db_path.exists():
        print(f"ERROR: {db_path} not found — run the scraper first.")
        return
    db = sqlite3.connect(str(db_path), isolation_level=None)
    db.row_factory = sqlite3.Row
    default = configs.load_default(db)
    out = Path("data/sim/_smoke_v2.csv")
    n_ev, n_rows = runner_v2.run_simulation_dual(
        db, config=default, regime="any", density="latest",
        scope={"country": "", "league": "", "event_id": "",
               "date": "", "search": ""},
        csv_path=out, engines=("v1", "v2"),
    )
    print(f"{n_ev} events / {n_rows} rows")

    v1_violations = 0
    v1_started_violations = 0
    v2_violations_dp_region = 0
    v2_violations_heuristic_region = 0
    started_rows = 0
    started_dp_region = 0

    def _flt(s: str):
        return float(s) if s not in ("", None) else None

    with open(out, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sh = _flt(row["score_home"]) or 0
            sa = _flt(row["score_away"]) or 0
            diff = int(sh) - int(sa)
            dp_region = abs(diff) < 2
            is_started = row["status"] == "STARTED"

            for side in ("home", "away"):
                v1_1up = _flt(row[f"our_1up_{side}_capped"])
                v1_2up = _flt(row[f"our_2up_{side}_capped"])
                v2_1up = _flt(row[f"v2_our_1up_{side}_capped"])
                v2_2up = _flt(row[f"v2_our_2up_{side}_capped"])
                if v1_1up is not None and v1_2up is not None and v1_1up > v1_2up:
                    v1_violations += 1
                    if is_started:
                        v1_started_violations += 1
                if v2_1up is not None and v2_2up is not None and v2_1up > v2_2up:
                    if dp_region:
                        v2_violations_dp_region += 1
                    else:
                        v2_violations_heuristic_region += 1
            if is_started:
                started_rows += 1
                if dp_region:
                    started_dp_region += 1

    print(f"V1 invariant violations (1UP_capped > 2UP_capped): {v1_violations}")
    print(f"  on STARTED rows: {v1_started_violations}/{started_rows}")
    print(f"V2 invariant violations in DP region (|diff| < 2): "
          f"{v2_violations_dp_region}  <-- must be 0")
    print(f"V2 invariant violations in heuristic region (|diff| >= 2): "
          f"{v2_violations_heuristic_region}  (heuristic 2UP trailing — expected)")

    # Don't leave the smoke CSV in the working tree.
    out.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
