import math
import pytest
from odds_scraper.reconstruct import pricing
from odds_scraper.reconstruct import constants as c
from odds_scraper.pricer import engine_v2, engine_v3, engine_v4


def test_renormalize_1x2_scales_to_one():
    ph, pd, pa, drift = pricing.renormalize_1x2(0.5, 0.3, 0.4)  # sums to 1.2
    assert math.isclose(ph + pd + pa, 1.0, abs_tol=1e-9)
    assert math.isclose(ph, 0.5 / 1.2, abs_tol=1e-9)
    assert math.isclose(drift, 0.2, abs_tol=1e-9)  # raw sum was 1.2 -> drift 0.2


def test_renormalize_1x2_already_fair():
    ph, pd, pa, drift = pricing.renormalize_1x2(0.45, 0.30, 0.25)
    assert math.isclose(ph + pd + pa, 1.0, abs_tol=1e-9)
    assert math.isclose(drift, 0.0, abs_tol=1e-9)


def test_cap_odds_applies_two_percent_margin():
    # fair odds for p=0.5 is 2.0; with 2% margin -> 1/(0.5*1.02)
    assert math.isclose(pricing.cap_odds_from_prob(0.5), 1.0 / (0.5 * 1.02), abs_tol=1e-9)


def test_cap_odds_none_for_nonpositive_prob():
    assert pricing.cap_odds_from_prob(0.0) is None


def test_next_goal_index_prematch_is_one():
    assert pricing.next_goal_index(0, 0) == 1


def test_next_goal_index_uses_total_goals_plus_one():
    assert pricing.next_goal_index(1, 1) == 3   # 2 scored -> next is goal #3
    assert pricing.next_goal_index(2, 0) == 3


def _moment(**over):
    m = {
        "event_id": "E1", "sr_id": "sr1", "brand": "ng",
        "event_name": "A vs B", "sr_start_time": "2026-05-01 18:00:00",
        "in_play": False, "moment_ts": "2026-05-01 17:30:00",
        "home_score": 0, "away_score": 0,
        "p_home_raw": 0.5, "p_draw_raw": 0.3, "p_away_raw": 0.4,
        "total_ou": [(2.5, 0.55)], "home_ou": [(1.5, 0.5)], "away_ou": [(1.5, 0.45)],
        "ftts_home": 0.45, "ftts_away": 0.40,
        "max_input_staleness_seconds": 100,
    }
    m.update(over)
    return m


def test_assemble_kwargs_uses_renormalized_probs_and_no_devig():
    kw = pricing.assemble_engine_kwargs(_moment())
    assert math.isclose(kw["p_home_win"] + kw["p_draw"] + kw["p_away_win"], 1.0, abs_tol=1e-9)
    # O/U over-prob passed straight through (no devig)
    assert kw["total_ou"] == [(2.5, 0.55)]
    # cap odds derived from renormalized prob with 2% margin
    assert math.isclose(kw["home_1x2_odds"], pricing.cap_odds_from_prob(kw["p_home_win"]), abs_tol=1e-9)
    assert kw["ftts_home_prob"] == 0.45
    assert kw["score"] == (0, 0)


def test_assemble_kwargs_drops_ftts_when_missing():
    kw = pricing.assemble_engine_kwargs(_moment(ftts_home=None, ftts_away=None))
    assert kw["ftts_home_prob"] is None and kw["ftts_away_prob"] is None


def test_install_dp_cache_patches_all_engines_and_restores():
    orig2 = engine_v2.ever_leads_probability
    restore = pricing.install_dp_cache()
    try:
        assert engine_v2.ever_leads_probability is not orig2
        assert engine_v3.ever_leads_probability is engine_v2.ever_leads_probability
        assert engine_v4.ever_leads_probability is engine_v2.ever_leads_probability
        # cache actually memoizes
        engine_v2.ever_leads_probability(1.2, 1.0, 0)
        info_before = pricing.dp_cache_info().misses
        engine_v2.ever_leads_probability(1.2, 1.0, 0)
        assert pricing.dp_cache_info().misses == info_before  # second call hit cache
    finally:
        restore()
    assert engine_v2.ever_leads_probability is orig2


def test_price_moment_emits_all_engine_cells():
    restore = pricing.install_dp_cache()
    try:
        row = pricing.price_moment(_moment(), run_ts="2026-05-29 00:00:00",
                                   max_home_lead=0, max_away_lead=0)
    finally:
        restore()
    assert row is not None
    for e in ("v2", "v3", "v4"):
        for m in ("1up", "2up"):
            for s in ("home", "away"):
                assert f"{e}_{m}_{s}_odds" in row
                assert f"{e}_{m}_{s}_prob" in row
    assert row["has_1up"] is True
    assert row["in_play"] is False
    assert math.isclose(row["p_home"] + row["p_draw"] + row["p_away"], 1.0, abs_tol=1e-9)


def test_price_moment_returns_none_without_full_1x2():
    m = _moment(p_home_raw=0.0, p_draw_raw=0.0, p_away_raw=0.0)
    restore = pricing.install_dp_cache()
    try:
        assert pricing.price_moment(m, run_ts="t", max_home_lead=0, max_away_lead=0) is None
    finally:
        restore()


def test_price_moment_drops_1up_without_ftts():
    restore = pricing.install_dp_cache()
    try:
        row = pricing.price_moment(_moment(ftts_home=None, ftts_away=None),
                                   run_ts="t", max_home_lead=0, max_away_lead=0)
    finally:
        restore()
    assert row is not None
    assert row["has_1up"] is False
    assert row["v2_1up_home_odds"] is None


def _long(market, sel, proba, line=0.0, x12_sel="Home", x12_proba=0.5,
          ts="2026-05-01 17:30:00", sel_ts="2026-05-01 17:29:00",
          in_play=False, hs=0, as_=0):
    return {
        "event_id": "E1", "sr_id": "sr1", "brand": "ng",
        "event_name": "A vs B", "sr_start_time": "2026-05-01 18:00:00",
        "in_play": in_play, "moment_ts": ts, "home_score": hs, "away_score": as_,
        "x12_selection": x12_sel, "x12_proba": x12_proba,
        "market_name": market, "line": line, "selection_name": sel,
        "true_proba": proba, "sel_ts": sel_ts,
    }


def test_moments_from_rows_builds_one_moment_per_timestamp():
    rows = [
        _long(c.MARKET_1X2, "Home", 0.5, x12_sel="Home", x12_proba=0.5),
        _long(c.MARKET_1X2, "Draw", 0.3, x12_sel="Draw", x12_proba=0.3),
        _long(c.MARKET_1X2, "Away", 0.4, x12_sel="Away", x12_proba=0.4),
        _long(c.MARKET_OU_TOTAL, "Over", 0.55, line=2.5),
        _long("1 Goal", "Home", 0.46, line=1.0),
        _long("1 Goal", "Away", 0.40, line=1.0),
        _long("1 Goal", "None", 0.14, line=1.0),
    ]
    moments = list(pricing.moments_from_rows(rows))
    assert len(moments) == 1
    m = moments[0]
    assert m["p_home_raw"] == 0.5 and m["p_draw_raw"] == 0.3 and m["p_away_raw"] == 0.4
    assert m["total_ou"] == [(2.5, 0.55)]
    assert m["ftts_home"] == 0.46 and m["ftts_away"] == 0.40   # active line #1


def test_moments_active_next_goal_line_follows_score():
    # 1-1 -> active next goal is line #3; line #1 must be ignored for ftts
    rows = [
        _long(c.MARKET_1X2, "Home", 0.5, in_play=True, hs=1, as_=1),
        _long(c.MARKET_1X2, "Draw", 0.3, x12_sel="Draw", in_play=True, hs=1, as_=1),
        _long(c.MARKET_1X2, "Away", 0.4, x12_sel="Away", in_play=True, hs=1, as_=1),
        _long(c.MARKET_OU_TOTAL, "Over", 0.6, line=3.5, in_play=True, hs=1, as_=1),
        _long("1 Goal", "Home", 0.9, line=1.0, in_play=True, hs=1, as_=1),
        _long("3 Goal", "Home", 0.30, line=3.0, in_play=True, hs=1, as_=1),
        _long("3 Goal", "Away", 0.25, line=3.0, in_play=True, hs=1, as_=1),
        _long("3 Goal", "None", 0.45, line=3.0, in_play=True, hs=1, as_=1),
    ]
    m = list(pricing.moments_from_rows(rows))[0]
    assert m["ftts_home"] == 0.30 and m["ftts_away"] == 0.25   # line #3, not #1


def test_moments_no_ftts_when_active_line_absent():
    rows = [
        _long(c.MARKET_1X2, "Home", 0.5, in_play=True, hs=2, as_=0),
        _long(c.MARKET_1X2, "Draw", 0.3, x12_sel="Draw", in_play=True, hs=2, as_=0),
        _long(c.MARKET_1X2, "Away", 0.4, x12_sel="Away", in_play=True, hs=2, as_=0),
        _long(c.MARKET_OU_TOTAL, "Over", 0.6, line=3.5, in_play=True, hs=2, as_=0),
    ]
    m = list(pricing.moments_from_rows(rows))[0]
    assert m["ftts_home"] is None and m["ftts_away"] is None
