from odds_scraper.pricer import inputs


def _row(market_id, line, side, odds, prob):
    """Mimics sqlite3.Row indexing for the columns inputs.py needs."""
    return {"market_id": market_id, "line": line, "side": side,
            "odds": odds, "probability": prob}


def _full_1x2(odds=(1.85, 3.4, 4.2), probs=(0.54, 0.29, 0.17)):
    return [
        _row("1x2_ft", 0.0, "home", odds[0], probs[0]),
        _row("1x2_ft", 0.0, "draw", odds[1], probs[1]),
        _row("1x2_ft", 0.0, "away", odds[2], probs[2]),
    ]

def _full_ou(market_id, over_prob_25=0.55):
    return [
        _row(market_id, 2.5, "over",  1.85, over_prob_25),
        _row(market_id, 2.5, "under", 1.95, 1 - over_prob_25),
    ]

def _ftts():
    return [
        _row("next_goal_ft", 1.0, "home", 1.85, 0.54),
        _row("next_goal_ft", 1.0, "none", 8.50, 0.12),
        _row("next_goal_ft", 1.0, "away", 3.50, 0.34),
    ]


def test_bp_full_inputs_uses_bp_only():
    bp_prices = _full_1x2() + _full_ou("over_under_ft") + \
                _full_ou("home_over_under_ft") + _full_ou("away_over_under_ft") + \
                _ftts()
    result, basis = inputs.extract({"betpawa": bp_prices})
    assert basis == "bp"
    assert result["p_home_win"] == 0.54
    assert result["ftts_home_prob"] == 0.54
    assert (2.5, 0.55) in result["total_ou"]


def test_bp_missing_ftts_falls_through_to_sb():
    bp_prices = _full_1x2() + _full_ou("over_under_ft")
    sb_prices = _full_1x2() + _ftts()
    result, basis = inputs.extract(
        {"betpawa": bp_prices, "sportybet": sb_prices},
    )
    assert basis == "bp+sb"
    # 1X2 + OU came from BP
    assert result["p_home_win"] == 0.54
    assert result["home_1x2_odds"] == 1.85
    # FTTS came from SB
    assert result["ftts_home_prob"] == 0.54
    assert result["ftts_away_prob"] == 0.34


def test_bp_missing_everything_uses_sb_only():
    sb_prices = _full_1x2() + _full_ou("over_under_ft") + _ftts()
    result, basis = inputs.extract({"betpawa": [], "sportybet": sb_prices})
    assert basis == "sb"
    assert result["p_home_win"] == 0.54


def test_both_missing_ou_returns_none():
    bp_prices = _full_1x2()
    sb_prices = _full_1x2()
    result, basis = inputs.extract(
        {"betpawa": bp_prices, "sportybet": sb_prices},
    )
    assert result is None
    assert basis == ""


def test_probability_is_consumed_as_is_never_redevigged():
    """The 1X2 probabilities passed to the engine must equal the raw
    `probability` column values — no client-side devigging from the
    odds. Regression guard against accidentally summing 1/odds."""
    bp_prices = _full_1x2(odds=(1.85, 3.4, 4.2), probs=(0.50, 0.30, 0.20)) + \
                _full_ou("over_under_ft")
    result, _ = inputs.extract({"betpawa": bp_prices})
    assert result["p_home_win"] == 0.50
    assert result["p_draw"]     == 0.30
    assert result["p_away_win"] == 0.20


def test_bp_suspended_side_uses_sb_for_that_side_only():
    """BP suspended one 1x2 side (odds=0, prob still valid). The other
    sides' BP odds remain in play; the suspended side falls back to SB
    per-side. cap_source_* records which book the engine actually used
    for each side's cap source."""
    bp_prices = [
        _row("1x2_ft", 0.0, "home", 100.0, 0.005),
        _row("1x2_ft", 0.0, "draw", 22.79, 0.045),
        _row("1x2_ft", 0.0, "away", 0.0,   0.95),  # suspended
    ] + _full_ou("over_under_ft")
    sb_prices = [
        _row("1x2_ft", 0.0, "home", 150.0, 0.004),
        _row("1x2_ft", 0.0, "draw", 14.0,  0.07),
        _row("1x2_ft", 0.0, "away", 1.01,  0.95),
    ] + _full_ou("over_under_ft")
    result, _ = inputs.extract(
        {"betpawa": bp_prices, "sportybet": sb_prices},
    )
    assert result["home_1x2_odds"] == 100.0  # BP's valid home odds
    assert result["away_1x2_odds"] == 1.01   # filled from SB per-side
    # Probabilities still come from BP (all-or-nothing on probs).
    assert result["p_home_win"] == 0.005
    assert result["p_away_win"] == 0.95
    assert result["_cap_source_home"] == "bp"
    assert result["_cap_source_away"] == "sb"


def test_bp_suspended_side_no_sb_fallback_leaves_odds_none():
    """When BP suspends a side and SB isn't present, that side's odds
    arrive at the engine as None — the cap step degrades to floored-only
    rather than rejecting the whole tick."""
    bp_prices = [
        _row("1x2_ft", 0.0, "home", 100.0, 0.005),
        _row("1x2_ft", 0.0, "draw", 22.79, 0.045),
        _row("1x2_ft", 0.0, "away", 0.0,   0.95),
    ] + _full_ou("over_under_ft")
    result, _ = inputs.extract({"betpawa": bp_prices})
    assert result["home_1x2_odds"] == 100.0
    assert result["away_1x2_odds"] is None
    assert result["_cap_source_home"] == "bp"
    assert result["_cap_source_away"] == ""


def test_per_side_ou_kept_independent():
    """home_ou available from BP; away_ou only from SB — each list
    independently falls through, no cross-book merging within a list."""
    bp_prices = _full_1x2() + _full_ou("over_under_ft") + _full_ou("home_over_under_ft")
    sb_prices = _full_1x2() + _full_ou("over_under_ft") + _full_ou("away_over_under_ft")
    result, basis = inputs.extract(
        {"betpawa": bp_prices, "sportybet": sb_prices},
    )
    assert basis == "bp+sb"
    assert result["home_ou"]  # came from BP
    assert result["away_ou"]  # came from SB
