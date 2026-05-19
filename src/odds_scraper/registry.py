"""Extension of bookieskit's market registry for BetPawa 1up / 2up.

bookieskit ships 1x2_1up_ft with the BetPawa mapping already populated
(market id 28000810). It does NOT include a BetPawa mapping for 1x2_2up_ft,
so we add it here.

Market ids and outcome names were verified against a real captured BetPawa
NG event response on 2026-05-19 (see tests/fixtures/betpawa_event_real_upcoming.json).
"""

from __future__ import annotations

from bookieskit.markets import MarketRegistry, OutcomeMapping

BP_ONE_UP_MARKET_ID: str = "28000810"
BP_TWO_UP_MARKET_ID: str = "28000850"


def build_registry() -> MarketRegistry:
    registry = MarketRegistry(load_builtins=True)
    _add_betpawa_two_up(registry)
    return registry


def _add_betpawa_two_up(registry: MarketRegistry) -> None:
    registry.add(
        canonical_id="1x2_2up_ft",
        name="1X2 (2UP) — Full Time",
        betpawa_id=BP_TWO_UP_MARKET_ID,
        sportybet_id="60100",
        bet9ja_key="S_1X22",
        betway_id="1X2 (2Up)",
        outcomes={
            "home": OutcomeMapping(
                canonical_name="home",
                betpawa="1", sportybet="Home", bet9ja="12", betway="__HOME__",
            ),
            "draw": OutcomeMapping(
                canonical_name="draw",
                betpawa="X", sportybet="Draw", bet9ja="X2", betway="Draw",
            ),
            "away": OutcomeMapping(
                canonical_name="away",
                betpawa="2", sportybet="Away", bet9ja="22", betway="__AWAY__",
            ),
        },
    )
