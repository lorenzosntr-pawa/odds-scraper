from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Mapping


def our_block_cols(prob_prefix: str, odds_prefix: str) -> tuple[str, ...]:
    """Column names for one engine's 16-cell OUR block, in canonical order.

    `prob_prefix` keys the four probability cells (V1 uses ``our_`` →
    ``our_p_home_1``; V2 uses ``v2_`` → ``v2_p_home_1``). `odds_prefix` keys
    the twelve fair/capped/capped_ev cells (V1 ``our_`` → ``our_1up_home_fair``;
    V2 ``v2_our_`` → ``v2_our_1up_home_fair``). The two prefixes differ because
    V1's prob cells are ``our_p_*`` while later engines' prob cells drop the
    ``our`` (``v2_p_*``) yet keep ``<eng>_our_`` on the odds cells.

    This is the SINGLE source of truth for the block layout — both CSV_COLUMNS
    (below) and the runners build their blocks from it, so the order can never
    drift between header and row."""
    return (
        f"{prob_prefix}p_home_1", f"{prob_prefix}p_away_1",
        f"{odds_prefix}1up_home_fair", f"{odds_prefix}1up_home_capped", f"{odds_prefix}1up_home_capped_ev",
        f"{odds_prefix}1up_away_fair", f"{odds_prefix}1up_away_capped", f"{odds_prefix}1up_away_capped_ev",
        f"{prob_prefix}p_home_2", f"{prob_prefix}p_away_2",
        f"{odds_prefix}2up_home_fair", f"{odds_prefix}2up_home_capped", f"{odds_prefix}2up_home_capped_ev",
        f"{odds_prefix}2up_away_fair", f"{odds_prefix}2up_away_capped", f"{odds_prefix}2up_away_capped_ev",
    )


# Per-engine (prob_prefix, odds_prefix) pairs, in canonical engine order. Add a
# future engine in ONE place here — its 16 columns get generated automatically
# below — then have the runner that runs it emit the matching block dict via
# `our_block_cols(*prefixes)`. No positional padding anywhere; a runner that
# didn't run an engine simply omits that block and the cells write blank.
OUR_ENGINE_PREFIXES = (
    ("our_", "our_"),       # V1
    ("v2_", "v2_our_"),     # V2
    ("v3_", "v3_our_"),     # V3
    ("v4_", "v4_our_"),     # V4 (latest)
)
PB_ENGINE_PREFIXES = (
    ("pB_our_", "pB_our_"),     # Profile B · V1
    ("pB_v2_", "pB_v2_our_"),   # Profile B · V2
    ("pB_v3_", "pB_v3_our_"),   # Profile B · V3
    ("pB_v4_", "pB_v4_our_"),   # Profile B · V4
)


def _engine_blocks(prefixes: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    out: list[str] = []
    for prob_prefix, odds_prefix in prefixes:
        out.extend(our_block_cols(prob_prefix, odds_prefix))
    return tuple(out)


CSV_COLUMNS = (
    # Leading column so downstream joins can filter v1-only / v2-only
    # rows trivially without parsing column suffixes.
    "engines",
    # Profile A is "the" profile used by every run; profile B is the
    # optional comparison profile selected on the simulator page. When
    # blank, the `pB_*` blocks near the end of the row are also blank
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
    # OUR engine blocks (V1, V2, V3, V4) — each a 16-cell block generated
    # from `our_block_cols` in OUR_ENGINE_PREFIXES order. `*_capped_ev` is
    # the EV of OUR probability against OUR capped odds (= negative of the
    # engine's embedded margin per selection). A block is blank in the CSV
    # unless that engine ran. V2/V3/V4 share V1's layout; V3/V4 differ from
    # V2 only in the margin step (their probs may still match V2's).
    *_engine_blocks(OUR_ENGINE_PREFIXES),
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
    # ===== Profile B blocks (V1..V4) — same layout as Profile A's OUR
    # blocks, prefixed `pB_`, generated from PB_ENGINE_PREFIXES. Blank when
    # no Profile B was selected. Bookmaker prob+odds are profile-independent
    # so they stay in the main block; only EV is duplicated below because
    # EV = profile-B prob × book odds. The same engine selection applies to
    # both profiles, so a `pB_v2_*` cell is blank exactly when `v2_*` is. =====
    *_engine_blocks(PB_ENGINE_PREFIXES),
    "pB_bp_1up_home_ev", "pB_bp_1up_away_ev",
    "pB_bp_2up_home_ev", "pB_bp_2up_away_ev",
    "pB_sb_1up_home_ev", "pB_sb_1up_away_ev",
    "pB_sb_2up_home_ev", "pB_sb_2up_away_ev",
)


def write_csv(out_path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    """Materialise the run output to disk. Pure file IO — no DB roundtrip.

    Each row is a ``{column: value}`` mapping. Columns are written in
    CSV_COLUMNS order; any column a row omits is written blank (``restval=""``)
    — this is how a runner that didn't run a given engine, or a single-profile
    run, leaves those cells empty WITHOUT padding anything positionally. An
    unknown column name in a row raises (``extrasaction="raise"``), so a typo
    or a stale column surfaces loudly instead of silently shifting the layout.

    The header is always written (so an empty run still produces a parseable
    file with column names)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=CSV_COLUMNS, restval="", extrasaction="raise",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
