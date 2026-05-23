from __future__ import annotations

from typing import Iterable, Mapping, Optional


def _extract_1x2(prices: Iterable) -> Optional[dict]:
    """Return {'home': {'odds':_, 'prob':_}, 'draw': …, 'away': …} or None
    if any side is missing or has null prob/odds. Treated as one input —
    all three sides must come from the same book.
    """
    out: dict = {}
    for r in prices:
        if r["market_id"] != "1x2_ft":
            continue
        if r["odds"] is None or r["probability"] is None:
            continue
        out[r["side"]] = {"odds": r["odds"], "prob": r["probability"]}
    if {"home", "draw", "away"} <= out.keys():
        return out
    return None


def _extract_ou(prices: Iterable, market_id: str) -> list[tuple[float, float]]:
    """Return list of (line, over_prob) for the given market. Drops null probs."""
    out: list[tuple[float, float]] = []
    for r in prices:
        if r["market_id"] != market_id or r["side"] != "over":
            continue
        if r["probability"] is None:
            continue
        out.append((r["line"], r["probability"]))
    return out


def _extract_ftts(prices: Iterable) -> Optional[dict]:
    """Return {'home': prob, 'away': prob} or None if either is missing."""
    out: dict = {}
    for r in prices:
        if r["market_id"] != "next_goal_ft":
            continue
        if r["probability"] is None:
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
    }, basis_used
