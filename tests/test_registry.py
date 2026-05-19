import json
from pathlib import Path

from bookieskit.markets import parse_markets

from odds_scraper.registry import (
    BP_ONE_UP_MARKET_ID,
    BP_TWO_UP_MARKET_ID,
    build_registry,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _real() -> dict:
    return json.loads((FIXTURES / "betpawa_event_real_upcoming.json").read_text(encoding="utf-8"))


def test_real_capture_parses_1up_and_2up():
    registry = build_registry()
    markets = parse_markets(_real(), platform="betpawa", registry=registry,
                            probability="true")
    canonical = {m.canonical_id for m in markets}
    assert "1x2_1up_ft" in canonical
    assert "1x2_2up_ft" in canonical


def test_1up_outcomes_have_odds_and_probability():
    registry = build_registry()
    markets = parse_markets(_real(), platform="betpawa", registry=registry,
                            probability="true")
    one_up = next(m for m in markets if m.canonical_id == "1x2_1up_ft")
    by_name = {o.canonical_name: o for o in one_up.outcomes}
    assert {"home", "draw", "away"} <= set(by_name)
    for name in ("home", "draw", "away"):
        assert by_name[name].odds is not None and by_name[name].odds > 0
        assert by_name[name].true_probability is not None
        assert 0 < by_name[name].true_probability < 1


def test_2up_outcomes_have_odds_and_probability():
    registry = build_registry()
    markets = parse_markets(_real(), platform="betpawa", registry=registry,
                            probability="true")
    two_up = next(m for m in markets if m.canonical_id == "1x2_2up_ft")
    by_name = {o.canonical_name: o for o in two_up.outcomes}
    assert {"home", "draw", "away"} <= set(by_name)
    for name in ("home", "draw", "away"):
        assert by_name[name].odds is not None and by_name[name].odds > 0
        assert by_name[name].true_probability is not None


def test_market_ids_match_real_response():
    assert BP_ONE_UP_MARKET_ID == "28000810"
    assert BP_TWO_UP_MARKET_ID == "28000850"
