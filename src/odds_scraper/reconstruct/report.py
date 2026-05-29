"""Markdown reliability report for a reconstruction run."""
from __future__ import annotations


def _pct(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def build_report(*, source_table, output_table, n_out, n_1up, n_prematch,
                 n_live, sample_rows, flagged_drift, n_scanned=None,
                 cache_info=None) -> str:
    stale = [r["max_input_staleness_seconds"] for r in sample_rows]
    lines = [
        "# ClickHouse 1UP/2UP reconstruction report", "",
        f"- source: `{source_table}`",
        f"- output: `{output_table}`",
    ]
    if n_scanned is not None:
        lines.append(f"- source rows scanned: {n_scanned:,}")
    lines += [
        f"- rows emitted: {n_out:,}",
        f"- prematch: {n_prematch:,} | live: {n_live:,}",
        f"- rows with V4 1UP priced: {n_1up:,} ({(100*n_1up/n_out if n_out else 0):.0f}%)",
        f"- rows without V4 1UP: {n_out - n_1up:,}",
        f"- 1X2 renorm-drift flagged (> tol): {flagged_drift:,}",
    ]
    if cache_info is not None:
        lines.append(f"- shared-DP cache: {cache_info}")
    lines += [
        "",
        "## Staleness (emitted moments)",
        f"- seconds — p50 {_pct(stale,50)}, p90 {_pct(stale,90)}, "
        f"max {max(stale) if stale else 0}",
        "",
        "## Limitations",
        "- `max_lead` columns (`max_home_lead`/`max_away_lead`) are approximated "
        "from the max score observed in the available (opportunistic) snapshots for "
        "each event; an unobserved lead swing (e.g. 1-0 -> 1-1 between snapshots) "
        "can mis-price live 1UP/2UP.",
    ]
    return "\n".join(lines) + "\n"
