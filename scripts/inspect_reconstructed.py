"""Diagnostic for a reconstructed output table — focused on live (in-play)
trailing/leading handling. Verifies the engine deactivated already-triggered
sides correctly.

Usage (CH_* env vars set, tunnel up):
  uv run python scripts/inspect_reconstructed.py --table risk_Lorenzo.recon_smoke
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odds_scraper.reconstruct import clickhouse_io as chio


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="risk_Lorenzo.recon_smoke")
    ap.add_argument("--samples", type=int, default=20)
    args = ap.parse_args()
    client = chio.connect()
    t = args.table

    def show(title, sql):
        print(f"\n=== {title} ===")
        res = client.query(sql)
        cols = res.column_names
        print("  " + " | ".join(cols))
        for row in res.result_rows:
            print("  " + " | ".join("" if v is None else str(v) for v in row))

    print(f"table: {t}")
    show("row counts by state",
         f"SELECT in_play, count() AS rows, "
         f"countIf(home_score != away_score) AS with_lead "
         f"FROM {t} GROUP BY in_play ORDER BY in_play")

    # Leading-side deactivation: when a side already leads, that side's 1UP
    # should be NULL (already triggered). Expect deactivated_1up ~= rows.
    show("LIVE, home leads by >=1  (home 1UP should be deactivated)",
         f"SELECT count() AS rows, "
         f"countIf(v4_1up_home_odds IS NULL) AS home_1up_null, "
         f"countIf(v4_1up_away_odds IS NOT NULL) AS away_1up_live "
         f"FROM {t} WHERE in_play AND home_score - away_score >= 1")
    show("LIVE, home leads by >=2  (home 2UP should be deactivated)",
         f"SELECT count() AS rows, "
         f"countIf(v4_2up_home_odds IS NULL) AS home_2up_null, "
         f"countIf(v4_2up_away_odds IS NOT NULL) AS away_2up_live "
         f"FROM {t} WHERE in_play AND home_score - away_score >= 2")
    show("LIVE, away leads by >=1  (away 1UP should be deactivated)",
         f"SELECT count() AS rows, "
         f"countIf(v4_1up_away_odds IS NULL) AS away_1up_null, "
         f"countIf(v4_1up_home_odds IS NOT NULL) AS home_1up_live "
         f"FROM {t} WHERE in_play AND away_score - home_score >= 1")

    # History-aware case: level live score but a side's 1UP already deactivated
    # => max_lead from an earlier (observed) snapshot fired. Non-zero count here
    # shows history tracking is working; it can't catch leads between snapshots.
    show("LIVE level score (0-0 excluded) with a side's 1UP already deactivated",
         f"SELECT count() AS level_rows, "
         f"countIf(v4_1up_home_odds IS NULL OR v4_1up_away_odds IS NULL) AS some_1up_null "
         f"FROM {t} WHERE in_play AND home_score = away_score AND home_score > 0")

    show(f"sample LIVE rows with a lead ({args.samples})",
         f"SELECT moment_ts, home_score AS h, away_score AS a, "
         f"round(p_home,3) AS ph, round(p_away,3) AS pa, "
         f"round(lambda_home,2) AS lh, round(lambda_away,2) AS la, "
         f"round(ftts_home,3) AS fh, round(ftts_away,3) AS fa, "
         f"v4_1up_home_odds AS h1up, v4_1up_away_odds AS a1up, "
         f"v4_2up_home_odds AS h2up, v4_2up_away_odds AS a2up, "
         f"max_input_staleness_seconds AS stale, round(renorm_drift,4) AS drift "
         f"FROM {t} WHERE in_play AND home_score != away_score "
         f"ORDER BY rand() LIMIT {args.samples}")


if __name__ == "__main__":
    main()
