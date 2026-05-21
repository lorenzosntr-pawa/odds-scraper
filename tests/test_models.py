from datetime import datetime, timezone

from odds_scraper.models import (
    Bookmaker, EventStatus, FetchStatus, MarketSpec, MARKET_MANIFEST,
    PriceKey, ResolvedIds, Snapshot, build_csv_header,
)


def test_manifest_lists_expected_markets():
    canonical_ids = [s.canonical_id for s in MARKET_MANIFEST]
    assert canonical_ids == [
        "1x2_ft", "1x2_1up_ft", "1x2_2up_ft", "over_under_ft",
        "next_goal_ft", "home_over_under_ft", "away_over_under_ft",
    ]


def test_manifest_over_under_lines_are_1_5_to_9_5():
    ou = next(s for s in MARKET_MANIFEST if s.canonical_id == "over_under_ft")
    assert ou.lines == (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5)
    assert ou.sides == ("over", "under")


def test_simple_markets_have_lines_none_and_3_sides():
    for cid in ("1x2_ft", "1x2_1up_ft", "1x2_2up_ft"):
        spec = next(s for s in MARKET_MANIFEST if s.canonical_id == cid)
        assert spec.lines is None
        assert spec.sides == ("home", "draw", "away")


def test_build_csv_header_column_count():
    # 14 meta columns + price columns from MARKET_MANIFEST:
    #   1x2_ft        3 sides × 2 (odds+prob) = 6
    #   1x2_1up_ft    3 × 2 = 6
    #   1x2_2up_ft    3 × 2 = 6
    #   over_under_ft 9 lines × 2 sides × 2 = 36
    #   next_goal_ft  9 lines × 3 sides × 2 = 54
    #   home_over_under_ft 6 lines × 2 sides × 2 = 24
    #   away_over_under_ft 6 lines × 2 sides × 2 = 24
    # 14 + 6+6+6+36+54+24+24 = 170
    header = build_csv_header()
    assert len(header) == 170


def test_build_csv_header_meta_prefix():
    header = build_csv_header()
    assert header[:14] == (
        "ts_utc", "event_bp_id", "sr_id", "genius_id",
        "home", "away", "kickoff_utc",
        "status", "match_minute", "score_home", "score_away",
        "bookmaker", "fetch_status", "fetch_error",
    )


def test_build_csv_header_price_section_order():
    header = build_csv_header()
    # 1x2_ft section
    assert header[14:20] == (
        "1x2_ft_home_odds", "1x2_ft_home_prob",
        "1x2_ft_draw_odds", "1x2_ft_draw_prob",
        "1x2_ft_away_odds", "1x2_ft_away_prob",
    )
    # 1x2_1up_ft section
    assert header[20:26] == (
        "1x2_1up_ft_home_odds", "1x2_1up_ft_home_prob",
        "1x2_1up_ft_draw_odds", "1x2_1up_ft_draw_prob",
        "1x2_1up_ft_away_odds", "1x2_1up_ft_away_prob",
    )
    # 1x2_2up_ft section
    assert header[26:32] == (
        "1x2_2up_ft_home_odds", "1x2_2up_ft_home_prob",
        "1x2_2up_ft_draw_odds", "1x2_2up_ft_draw_prob",
        "1x2_2up_ft_away_odds", "1x2_2up_ft_away_prob",
    )
    # over_under_ft starts at index 32 (first OU line 1.5)
    assert header[32:36] == (
        "ou_1.5_over_odds", "ou_1.5_over_prob",
        "ou_1.5_under_odds", "ou_1.5_under_prob",
    )
    # over_under_ft ends at index 68 (last OU line 9.5 has 4 cells)
    assert header[64:68] == (
        "ou_9.5_over_odds", "ou_9.5_over_prob",
        "ou_9.5_under_odds", "ou_9.5_under_prob",
    )
    # next_goal_ft starts at index 68 (first goal-number line 1.0)
    assert header[68:74] == (
        "ng_1.0_home_odds", "ng_1.0_home_prob",
        "ng_1.0_none_odds", "ng_1.0_none_prob",
        "ng_1.0_away_odds", "ng_1.0_away_prob",
    )
    # home_over_under_ft starts at index 122 (first line 0.5)
    assert header[122:126] == (
        "ou_home_0.5_over_odds", "ou_home_0.5_over_prob",
        "ou_home_0.5_under_odds", "ou_home_0.5_under_prob",
    )
    # away_over_under_ft starts at index 146 (first line 0.5)
    assert header[146:150] == (
        "ou_away_0.5_over_odds", "ou_away_0.5_over_prob",
        "ou_away_0.5_under_odds", "ou_away_0.5_under_prob",
    )
    # Last four columns: away_over_under_ft line 5.5 (the manifest's final line)
    assert header[-4:] == (
        "ou_away_5.5_over_odds", "ou_away_5.5_over_prob",
        "ou_away_5.5_under_odds", "ou_away_5.5_under_prob",
    )


def test_fetch_status_enum_only_four_values():
    values = {fs.value for fs in FetchStatus}
    assert values == {"ok", "lookup_failed", "http_error", "parse_error"}


def _meta_kwargs(**overrides):
    kw = dict(
        ts_utc=datetime(2026, 5, 19, 14, 32, 5, tzinfo=timezone.utc),
        event_bp_id="33660318",
        sr_id="sr:match:12345",
        genius_id="g-67890",
        home="Team A", away="Team B",
        kickoff_utc=datetime(2026, 5, 19, 15, 0, 0, tzinfo=timezone.utc),
        status=EventStatus.STARTED,
        match_minute=34,
        score_home=1, score_away=0,
        bookmaker=Bookmaker.BETPAWA,
        fetch_status=FetchStatus.OK,
        fetch_error="",
        prices={},
    )
    kw.update(overrides)
    return kw


def test_snapshot_to_csv_row_meta_columns():
    snap = Snapshot(**_meta_kwargs())
    row = snap.to_csv_row()
    assert len(row) == 170
    assert row[0] == "2026-05-19T14:32:05Z"
    assert row[1] == "33660318"
    assert row[2] == "sr:match:12345"
    assert row[3] == "g-67890"
    assert row[4] == "Team A"
    assert row[5] == "Team B"
    assert row[6] == "2026-05-19T15:00:00Z"
    assert row[7] == "STARTED"
    assert row[8] == "34"
    assert row[9] == "1"
    assert row[10] == "0"
    assert row[11] == "betpawa"
    assert row[12] == "ok"
    assert row[13] == ""


def test_snapshot_to_csv_row_simple_market_prices():
    prices = {
        PriceKey("1x2_ft", None, "home"): (1.85, 0.54054),
        PriceKey("1x2_ft", None, "draw"): (3.20, 0.31250),
        PriceKey("1x2_ft", None, "away"): (4.50, 0.22222),
    }
    snap = Snapshot(**_meta_kwargs(prices=prices))
    row = snap.to_csv_row()
    assert row[14] == "1.85"
    assert row[15] == "0.54054"
    assert row[16] == "3.20"
    assert row[17] == "0.31250"
    assert row[18] == "4.50"
    assert row[19] == "0.22222"
    assert row[20:26] == ("", "", "", "", "", "")


def test_snapshot_to_csv_row_parameterized_market_prices():
    prices = {
        PriceKey("over_under_ft", 2.5, "over"): (1.70, 0.58),
        PriceKey("over_under_ft", 2.5, "under"): (2.10, 0.42),
        PriceKey("over_under_ft", 3.5, "over"): (2.50, None),
        PriceKey("over_under_ft", 3.5, "under"): (1.50, None),
    }
    snap = Snapshot(**_meta_kwargs(prices=prices))
    row = snap.to_csv_row()
    header = build_csv_header()
    assert row[header.index("ou_2.5_over_odds")] == "1.70"
    assert row[header.index("ou_2.5_over_prob")] == "0.58000"
    assert row[header.index("ou_2.5_under_odds")] == "2.10"
    assert row[header.index("ou_2.5_under_prob")] == "0.42000"
    assert row[header.index("ou_3.5_over_odds")] == "2.50"
    assert row[header.index("ou_3.5_over_prob")] == ""
    assert row[header.index("ou_3.5_under_odds")] == "1.50"
    assert row[header.index("ou_4.5_over_odds")] == ""


def test_snapshot_to_csv_row_blanks_when_failure_status():
    snap = Snapshot(**_meta_kwargs(
        fetch_status=FetchStatus.HTTP_ERROR,
        fetch_error="timeout after 10s",
        prices={},
    ))
    row = snap.to_csv_row()
    assert len(row) == 170
    assert row[12] == "http_error"
    assert row[13] == "timeout after 10s"
    assert all(cell == "" for cell in row[14:])


def test_resolved_ids_matched_bookmakers():
    r = ResolvedIds(sr_id="sr:match:1", genius_id=None, sb_id="sr:match:1",
                    b9j_id=None, bw_id=None)
    assert r.matched_bookmakers() == {"sportybet"}


def test_snapshot_default_country_league_fields_are_empty():
    snap = Snapshot(**_meta_kwargs())
    assert snap.country_id == ""
    assert snap.country_name == ""
    assert snap.league_id == ""
    assert snap.league_name == ""


def test_snapshot_accepts_country_league_kwargs():
    snap = Snapshot(**_meta_kwargs(
        country_id="242", country_name="Germany",
        league_id="12091", league_name="2nd Bundesliga",
    ))
    assert snap.country_id == "242"
    assert snap.country_name == "Germany"
    assert snap.league_id == "12091"
    assert snap.league_name == "2nd Bundesliga"
