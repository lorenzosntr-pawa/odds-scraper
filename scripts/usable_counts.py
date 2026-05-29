"""How many usable V4 rows are in an output table, under confidence bands on
max_input_staleness_seconds and |renorm_drift|.

"Usable" = the V4 price exists (odds and prob not null) for that market.
2UP is the always-available product (any priceable moment); 1UP also needs
next-goal data.

  uv run python scripts/usable_counts.py --table risk_Lorenzo.recon_smoke
  uv run python scripts/usable_counts.py --table ... --max-staleness 600 --max-drift 0.03
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odds_scraper.reconstruct import clickhouse_io as chio

# v4 price present for a market (both sides have odds + prob)
V4_2UP = ("v4_2up_home_odds IS NOT NULL AND v4_2up_home_prob IS NOT NULL "
          "AND v4_2up_away_odds IS NOT NULL AND v4_2up_away_prob IS NOT NULL")
V4_1UP = ("v4_1up_home_odds IS NOT NULL AND v4_1up_home_prob IS NOT NULL "
          "AND v4_1up_away_odds IS NOT NULL AND v4_1up_away_prob IS NOT NULL")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="risk_Lorenzo.recon_smoke")
    ap.add_argument("--max-staleness", type=int, default=600,
                    help="custom band: max staleness seconds (default 600)")
    ap.add_argument("--max-drift", type=float, default=0.03,
                    help="custom band: max |renorm_drift| (default 0.03)")
    args = ap.parse_args()
    client = chio.connect()
    t = args.table

    def one(sql):
        return client.query(sql).result_rows[0]

    total, v4_2up, v4_1up = one(
        f"SELECT count(), countIf({V4_2UP}), countIf({V4_1UP}) FROM {t}")
    print(f"table: {t}")
    print(f"total rows:            {total:,}")
    print(f"V4 2UP priced:         {v4_2up:,} ({100*v4_2up/total if total else 0:.1f}%)")
    print(f"V4 1UP priced:         {v4_1up:,} ({100*v4_1up/total if total else 0:.1f}%)")

    q = one(
        f"SELECT quantile(0.5)(max_input_staleness_seconds), "
        f"quantile(0.9)(max_input_staleness_seconds), "
        f"quantile(0.99)(max_input_staleness_seconds), max(max_input_staleness_seconds), "
        f"quantile(0.5)(abs(renorm_drift)), quantile(0.9)(abs(renorm_drift)), "
        f"quantile(0.99)(abs(renorm_drift)), max(abs(renorm_drift)) FROM {t}")
    print(f"\nstaleness sec   p50={q[0]:.0f}  p90={q[1]:.0f}  p99={q[2]:.0f}  max={q[3]:.0f}")
    print(f"|renorm_drift|  p50={q[4]:.4f}  p90={q[5]:.4f}  p99={q[6]:.4f}  max={q[7]:.4f}")

    bands = [
        ("strict   (stale<=300, drift<=0.02)", 300, 0.02),
        ("moderate (stale<=1800, drift<=0.05)", 1800, 0.05),
        (f"custom   (stale<={args.max_staleness}, drift<={args.max_drift})",
         args.max_staleness, args.max_drift),
    ]
    print(f"\n{'band':40} {'usable 2UP':>14} {'usable 1UP':>14}")
    for label, st, dr in bands:
        cond = f"max_input_staleness_seconds <= {st} AND abs(renorm_drift) <= {dr}"
        u2, u1 = one(f"SELECT countIf({V4_2UP} AND {cond}), "
                     f"countIf({V4_1UP} AND {cond}) FROM {t}")
        print(f"{label:40} {u2:>9,} ({100*u2/total if total else 0:>4.1f}%) "
              f"{u1:>9,} ({100*u1/total if total else 0:>4.1f}%)")


if __name__ == "__main__":
    main()
