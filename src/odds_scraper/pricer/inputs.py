from __future__ import annotations

from typing import Iterable, Mapping, Optional


# Anything below this is treated as a suspended / placeholder odds value
# and dropped at the input layer so it can't reach the engine. The
# engine's cap step divides by source_odds, so a 0 (BP sometimes writes
# 0 when a selection is suspended) blows up with ZeroDivisionError.
_MIN_VALID_ODDS = 1.01


def _is_valid_odds(v) -> bool:
    return v is not None and v >= _MIN_VALID_ODDS


def _is_valid_prob(v) -> bool:
    return v is not None and 0.0 < v < 1.0


def _extract_1x2(prices: Iterable) -> Optional[dict]:
    """Return {'home': {'odds':_, 'prob':_}, 'draw': …, 'away': …} or None
    if any side's probability is missing/invalid. Treated as one input —
    all three sides' probs must come from the same book so the engine's
    favorite-strength math stays internally consistent.

    A side's ODDS, however, may be None when that side is suspended
    (BP returns odds=0 for a closed selection). The engine's cap step
    accepts None as "no cap source for this side" and falls back to a
    floor, so a suspended away side doesn't disqualify the whole book.
    Cross-book per-side odds fallback happens in `extract()` so the cap
    always lands on the closest-available 1x2 source.
    """
    out: dict = {}
    for r in prices:
        if r["market_id"] != "1x2_ft":
            continue
        if not _is_valid_prob(r["probability"]):
            continue
        odds = r["odds"] if _is_valid_odds(r["odds"]) else None
        out[r["side"]] = {"odds": odds, "prob": r["probability"]}
    if {"home", "draw", "away"} <= out.keys():
        return out
    return None


def _extract_ou(prices: Iterable, market_id: str) -> list[tuple[float, float]]:
    """Return list of (line, over_prob) for the given market. Drops rows
    with invalid (None or out-of-range) probabilities."""
    out: list[tuple[float, float]] = []
    for r in prices:
        if r["market_id"] != market_id or r["side"] != "over":
            continue
        if not _is_valid_prob(r["probability"]):
            continue
        out.append((r["line"], r["probability"]))
    return out


def _extract_ftts(prices: Iterable) -> Optional[dict]:
    """Return {'home': prob, 'away': prob} or None if either is missing
    or has an invalid probability."""
    out: dict = {}
    for r in prices:
        if r["market_id"] != "next_goal_ft":
            continue
        if not _is_valid_prob(r["probability"]):
            continue
        if r["side"] in ("home", "away"):
            out[r["side"]] = r["probability"]
    if {"home", "away"} <= out.keys():
        return out
    return None


def extract(
    prices_by_book: Mapping[str, Iterable],
) -> tuple[Optional[dict], str]:
    """Build engine inputs from per-book prices using BP-first / SB-fallback.

    Inputs treated independently — each may come from a different book:
      - 1X2 (prob+odds, all three sides)
      - total_ou           (list of (line, over_prob))
      - home_ou            (list)
      - away_ou            (list)
      - ftts               (home + away probs)

    Returns (engine_input_dict, basis_used) where basis_used is one of
    'bp' | 'sb' | 'bp+sb'. Returns (None, '') if lambdas can't be derived
    (no OU from either book).
    """
    bp = list(prices_by_book.get("betpawa") or [])
    sb = list(prices_by_book.get("sportybet") or [])
    used_books: set[str] = set()

    def pick(extractor, *, bp_args=(), sb_args=(), nonempty=lambda x: x):
        bp_val = extractor(bp, *bp_args)
        if nonempty(bp_val):
            used_books.add("bp")
            return bp_val
        sb_val = extractor(sb, *sb_args)
        if nonempty(sb_val):
            used_books.add("sb")
            return sb_val
        # Return whichever non-None value we got (may be empty list);
        # don't claim a book if neither produced anything.
        return bp_val if bp_val is not None else sb_val

    one_x_two = pick(_extract_1x2, nonempty=bool)
    if one_x_two is None:
        return None, ""

    # Per-side 1x2 odds cross-book fallback. The chosen book's
    # `one_x_two` may have None on a suspended side; if the OTHER book
    # has it valid, swap that in so the cap step still has a source.
    # Track which book each side's odds came from so the CSV can
    # surface `cap_source_home` / `cap_source_away`.
    other = sb if "bp" in used_books and "sb" not in used_books else (
        bp if "sb" in used_books and "bp" not in used_books else None
    )
    other_1x2 = _extract_1x2(other) if other is not None else None
    chosen_book = "bp" if "bp" in used_books else ("sb" if "sb" in used_books else "")
    other_book = "sb" if chosen_book == "bp" else ("bp" if chosen_book == "sb" else "")
    cap_source: dict[str, str] = {}
    for side in ("home", "draw", "away"):
        if one_x_two[side]["odds"] is not None:
            cap_source[side] = chosen_book
        elif other_1x2 is not None and other_1x2[side]["odds"] is not None:
            one_x_two[side]["odds"] = other_1x2[side]["odds"]
            cap_source[side] = other_book
        else:
            cap_source[side] = ""

    total_ou = pick(_extract_ou, bp_args=("over_under_ft",),
                    sb_args=("over_under_ft",), nonempty=bool)
    home_ou  = pick(_extract_ou, bp_args=("home_over_under_ft",),
                    sb_args=("home_over_under_ft",), nonempty=bool)
    away_ou  = pick(_extract_ou, bp_args=("away_over_under_ft",),
                    sb_args=("away_over_under_ft",), nonempty=bool)

    if not total_ou and not (home_ou and away_ou):
        # Engine deactivates without OU coverage.
        return None, ""

    ftts = pick(_extract_ftts, nonempty=bool)

    basis_used = (
        "bp+sb" if used_books == {"bp", "sb"}
        else "bp" if "bp" in used_books
        else "sb" if "sb" in used_books
        else ""
    )

    return {
        "p_home_win":     one_x_two["home"]["prob"],
        "p_draw":         one_x_two["draw"]["prob"],
        "p_away_win":     one_x_two["away"]["prob"],
        "home_1x2_odds":  one_x_two["home"]["odds"],
        "draw_1x2_odds":  one_x_two["draw"]["odds"],
        "away_1x2_odds":  one_x_two["away"]["odds"],
        "total_ou":       total_ou,
        "home_ou":        home_ou,
        "away_ou":        away_ou,
        "ftts_home_prob": ftts["home"] if ftts else None,
        "ftts_away_prob": ftts["away"] if ftts else None,
        # Private — stripped by runners before engine call. Lets the CSV
        # surface which book each side's cap source actually came from.
        "_cap_source_home": cap_source["home"],
        "_cap_source_away": cap_source["away"],
    }, basis_used
