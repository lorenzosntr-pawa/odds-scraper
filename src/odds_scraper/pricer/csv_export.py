from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable


CSV_COLUMNS = (
    # Leading column so downstream joins can filter v1-only / v2-only
    # rows trivially without parsing column suffixes.
    "engines",
    # Profile A is "the" profile used by every run; profile B is the
    # optional comparison profile selected on the simulator page. When
    # blank, the `pB_*` blocks at the bottom of the row are also blank
    # and the run is a single-profile run.
    "profile_a", "profile_b",
    "snapshot_id", "event_id",
    "home", "away", "kickoff_utc",
    "ts_utc",
    "status", "match_minute", "score_home", "score_away",
    "basis_used",
    "lambda_home", "lambda_away",
    # 1x2 + next-goal reference block — the engine INPUTS behind the
    # synthetic prices, so a reader can see what the engine priced
    # against without re-deriving. These are profile-independent (they
    # come from the market, not the coefficients), so they're written
    # once in the shared block. `p_*_win` are the devigged 1x2 probs
    # (the cap's `source_true_prob`); `ftts_*` are the next-goal probs
    # that drive the level-score 1UP; `cap_1x2_*_odds` are the RESOLVED
    # per-side cap-source odds (BP-first, SB-fallback) the cap actually
    # used as `source_odds` — naming which book is in `cap_source_*`.
    "p_home_win", "p_draw", "p_away_win",
    "ftts_home_prob", "ftts_away_prob",
    "cap_1x2_home_odds", "cap_1x2_away_odds",
    # OUR engine output. `*_capped_ev` is the EV of OUR probability
    # against OUR capped odds (= negative of the engine's embedded
    # margin for that selection) — useful when BP isn't quoting and
    # you want to see the simulated edge against the engine's own
    # offer rather than nothing at all.
    "our_p_home_1", "our_p_away_1",
    "our_1up_home_fair", "our_1up_home_capped", "our_1up_home_capped_ev",
    "our_1up_away_fair", "our_1up_away_capped", "our_1up_away_capped_ev",
    "our_p_home_2", "our_p_away_2",
    "our_2up_home_fair", "our_2up_home_capped", "our_2up_home_capped_ev",
    "our_2up_away_fair", "our_2up_away_capped", "our_2up_away_capped_ev",
    # ===== V2 engine block — same layout as V1's OUR block, prefixed v2_. =====
    # Blank when only V1 ran; populated by runner_v2 when V2 (or 'both')
    # is selected. Mirrors V1's *_capped_ev semantics (EV of V2's own
    # probability vs V2's own capped odds — surfaces V2's embedded
    # margin per selection).
    "v2_p_home_1", "v2_p_away_1",
    "v2_our_1up_home_fair", "v2_our_1up_home_capped", "v2_our_1up_home_capped_ev",
    "v2_our_1up_away_fair", "v2_our_1up_away_capped", "v2_our_1up_away_capped_ev",
    "v2_p_home_2", "v2_p_away_2",
    "v2_our_2up_home_fair", "v2_our_2up_home_capped", "v2_our_2up_home_capped_ev",
    "v2_our_2up_away_fair", "v2_our_2up_away_capped", "v2_our_2up_away_capped_ev",
    # BP / SB carry per-selection true prob + odds + EV. EV uses OUR
    # probability against the book's odds (`our_prob * book_odds - 1`)
    # — that's the actionable edge. The `*_p_*` column is the book's
    # own devigged probability so the reader can compare OUR vs the
    # book's view at a glance. B9J / BW have no devigged probability
    # stored, so they remain odds-only.
    "bp_p_1up_home", "bp_1up_home_odds", "bp_1up_home_ev",
    "bp_p_1up_away", "bp_1up_away_odds", "bp_1up_away_ev",
    "bp_p_2up_home", "bp_2up_home_odds", "bp_2up_home_ev",
    "bp_p_2up_away", "bp_2up_away_odds", "bp_2up_away_ev",
    "sb_p_1up_home", "sb_1up_home_odds", "sb_1up_home_ev",
    "sb_p_1up_away", "sb_1up_away_odds", "sb_1up_away_ev",
    "sb_p_2up_home", "sb_2up_home_odds", "sb_2up_home_ev",
    "sb_p_2up_away", "sb_2up_away_odds", "sb_2up_away_ev",
    "b9j_1up_home_odds", "b9j_1up_away_odds",
    "b9j_2up_home_odds", "b9j_2up_away_odds",
    "bw_1up_home_odds",  "bw_1up_away_odds",
    "bw_2up_home_odds",  "bw_2up_away_odds",
    # 1x2 odds used for the engine's cap step. `bp_1x2_*` / `sb_1x2_*`
    # are the raw per-book quotes at this tick (so the reader can see
    # when BP suspended a side). `cap_source_home` / `cap_source_away`
    # name which book the engine actually used for each side's cap
    # source (BP preferred; SB only when BP's side was suspended).
    "bp_1x2_home_odds", "bp_1x2_draw_odds", "bp_1x2_away_odds",
    "sb_1x2_home_odds", "sb_1x2_draw_odds", "sb_1x2_away_odds",
    "cap_source_home",  "cap_source_away",
    # ===== Profile B block — same layout as Profile A's OUR + BP/SB
    # EV cells, prefixed `pB_`. Blank when no Profile B was selected.
    # Bookmaker prob+odds are profile-independent so they stay in the
    # main block; only EV is duplicated because EV = profile-B prob ×
    # book odds. Same engine selection applies to both profiles, so
    # the `pB_v2_*` cells are blank when engine=v1, mirroring the main
    # `v2_*` cells. =====
    "pB_our_p_home_1", "pB_our_p_away_1",
    "pB_our_1up_home_fair", "pB_our_1up_home_capped", "pB_our_1up_home_capped_ev",
    "pB_our_1up_away_fair", "pB_our_1up_away_capped", "pB_our_1up_away_capped_ev",
    "pB_our_p_home_2", "pB_our_p_away_2",
    "pB_our_2up_home_fair", "pB_our_2up_home_capped", "pB_our_2up_home_capped_ev",
    "pB_our_2up_away_fair", "pB_our_2up_away_capped", "pB_our_2up_away_capped_ev",
    "pB_v2_p_home_1", "pB_v2_p_away_1",
    "pB_v2_our_1up_home_fair", "pB_v2_our_1up_home_capped", "pB_v2_our_1up_home_capped_ev",
    "pB_v2_our_1up_away_fair", "pB_v2_our_1up_away_capped", "pB_v2_our_1up_away_capped_ev",
    "pB_v2_p_home_2", "pB_v2_p_away_2",
    "pB_v2_our_2up_home_fair", "pB_v2_our_2up_home_capped", "pB_v2_our_2up_home_capped_ev",
    "pB_v2_our_2up_away_fair", "pB_v2_our_2up_away_capped", "pB_v2_our_2up_away_capped_ev",
    "pB_bp_1up_home_ev", "pB_bp_1up_away_ev",
    "pB_bp_2up_home_ev", "pB_bp_2up_away_ev",
    "pB_sb_1up_home_ev", "pB_sb_1up_away_ev",
    "pB_sb_2up_home_ev", "pB_sb_2up_away_ev",
)


def write_csv(out_path: Path, rows: Iterable[tuple]) -> None:
    """Materialise the run output to disk. Pure file IO — no DB roundtrip,
    no temporary tables. Rows must be in `CSV_COLUMNS` order.

    The header is always written (so an empty run still produces a
    parseable file with column names)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row)
