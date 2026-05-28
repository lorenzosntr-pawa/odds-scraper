from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from odds_scraper.models import MARKET_MANIFEST, MarketSpec
from odds_scraper.pricer import (
    engine_v2, inputs as pricer_inputs,
    score_state as pricer_score_state,
)

from . import queries
from .pricer_routes import register_pricer_routes

# Markets visible in the collapsed event-list card, in display order.
# Derived from queries.COLLAPSED_MARKETS for a single source of truth.
_MARKET_LABELS = {
    "1x2_ft":     ("1x2 — Full Time", "1x2 ft"),
    "1x2_1up_ft": ("1x2 — 1 Up",      "1x2 1up"),
    "1x2_2up_ft": ("1x2 — 2 Up",      "1x2 2up"),
}
_COLLAPSED_ORDER: tuple[tuple[str, str, str], ...] = tuple(
    (m, *_MARKET_LABELS[m]) for m in queries.COLLAPSED_MARKETS
)

# Manifest lookup — read MarketSpec.sides without hardcoding outcome strings.
_spec_by_id: dict[str, MarketSpec] = {s.canonical_id: s for s in MARKET_MANIFEST}


def _sides_for(market_id: str) -> tuple[str, ...]:
    return _spec_by_id[market_id].sides


# Single-pill families on the detail page family-chip row. Parameterized
# families come from _EXPANDER_MARKETS.
_FAMILY_PILLS_1X2: tuple[tuple[str, str], ...] = (
    ("1x2_ft",     "1x2 — Full Time"),
    ("1x2_1up_ft", "1x2 — 1 Up"),
    ("1x2_2up_ft", "1x2 — 2 Up"),
)

# Display order for parameterized markets in the detail-page pill bar AND
# the home-page card expander. Sub-project 3's single source of truth.
_EXPANDER_MARKETS: tuple[tuple[str, str], ...] = (
    ("next_goal_ft",       "Next Goal"),
    ("over_under_ft",      "Match O/U"),
    ("home_over_under_ft", "Home O/U"),
    ("away_over_under_ft", "Away O/U"),
)

# Short market-label prefixes used in the per-row outcome labels inside
# each card expander group (e.g., "NG 1 · H", "OU 2.5 · O").
_SHORT_PREFIX: dict[str, str] = {
    "next_goal_ft":       "NG",
    "over_under_ft":      "OU",
    "home_over_under_ft": "H-OU",
    "away_over_under_ft": "A-OU",
}
assert set(_SHORT_PREFIX) == {mid for mid, _ in _EXPANDER_MARKETS}, (
    "_SHORT_PREFIX must cover every entry in _EXPANDER_MARKETS"
)

# Market picker: ordered list of (market_id, line_or_None, label, slug)
# Slug is the URL-safe key passed via ?market=...
def _build_market_picker() -> list[tuple[str, Optional[float], str, str]]:
    picker: list[tuple[str, Optional[float], str, str]] = []
    for mid in queries.COLLAPSED_MARKETS:
        label = _MARKET_LABELS[mid][0]
        picker.append((mid, None, label, mid))
    for market_id, label_prefix in _EXPANDER_MARKETS:
        spec = _spec_by_id[market_id]
        prefix = spec.column_prefix
        for line in spec.lines or ():
            slug = f"{prefix}_{line}"
            picker.append((market_id, line, f"{label_prefix} {line}", slug))
    return picker


_MARKET_PICKER = _build_market_picker()
_PICKER_BY_SLUG = {slug: (mid, line, label) for mid, line, label, slug in _MARKET_PICKER}

# Default market for the detail page when none specified — focus is 1up
_DEFAULT_MARKET_SLUG = "1x2_1up_ft"

_POLL_SECONDS = {"live": 5, "upcoming": 30, "ended": 60}

_SIDE_LABEL = {
    "home": "Home", "draw": "Draw", "away": "Away",
    "over": "Over", "under": "Under",
    "none": "None",
}

_SIDE_SHORT = {
    "home": "H", "draw": "D", "away": "A",
    "over": "O", "under": "U",
    "none": "N",
}

@dataclass
class PriceCell:
    odds: float
    probability: Optional[float]


@dataclass
class OutcomeRow:
    market_label: str       # e.g., "1x2 ft" (used in card view)
    side_label: str         # e.g., "Home"
    side_short: str         # e.g., "H"
    prices: dict[str, PriceCell]


@dataclass
class MarketGroup:
    label: str              # e.g., "1x2 — Full Time"
    group_key: str          # stable key for per-market collapse persistence:
                            #   "1x2_ft" / "1x2_1up_ft" / "1x2_2up_ft" for the
                            #   1x2 family; f"{canonical_id}_{line}" for
                            #   parameterized markets (e.g., "over_under_ft_2.5").
    rows: list[OutcomeRow]


@dataclass
class EventView:
    id: str
    home: str
    away: str
    kickoff_utc: str
    status: str
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    market_groups: list[MarketGroup]
    # OUR-engine output for the SIM column (V2 engine).
    our_1up_home: Optional[float]
    our_1up_away: Optional[float]
    our_2up_home: Optional[float]
    our_2up_away: Optional[float]
    our_p_1up_home: Optional[float]
    our_p_1up_away: Optional[float]
    our_p_2up_home: Optional[float]
    our_p_2up_away: Optional[float]
    # True when BP itself quoted the market — drives the rule "if BP
    # missing, OUR replaces the BP cell instead of going to SIM column".
    bp_has_1up: bool
    bp_has_2up: bool


@dataclass
class HistoryRow:
    """One snapshot's prices for a single market, all bookmakers/sides,
    plus the match state recorded at that tick (minute + score + status)."""
    ts_utc: str
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    status: str
    # cells: {bookmaker: {side: PriceCell}}
    cells: dict[str, dict[str, PriceCell]]


@dataclass
class EventDetail:
    id: str
    home: str
    away: str
    kickoff_utc: str
    status: str
    match_minute: Optional[int]
    score_home: Optional[int]
    score_away: Optional[int]
    country_name: str
    league_name: str
    # Currently selected market info
    market_label: str
    market_slug: str
    sides: tuple[str, ...]
    sides_short: tuple[str, ...]
    # Family-chip row — always rendered. Tuple:
    #   (family_id, label, default_slug, is_active, is_disabled)
    family_pills: list[tuple[str, str, str, bool, bool]]
    # Line-chip row — only populated when active family is parameterized.
    # Tuple: (slug, label, is_active)
    line_pills: list[tuple[str, str, bool]]
    # History rows newest first
    history: list[HistoryRow]
    # Bookmaker columns to render in the history table, in display
    # order. Includes "sim" when the active market is 1UP or 2UP and
    # pricer_live_results carries OUR for any tick.
    history_books: tuple[str, ...]
    # Status tab the user came from (via ?from=... on the detail link).
    # The "← EVENTS" link uses this to send them back to the same tab.
    from_status: str


def create_app(db_path: Path) -> FastAPI:
    """Build the FastAPI app with a read-only sqlite handle.

    db_path is captured in the closure so handlers reuse one connection per
    process. SQLite connections in WAL mode + check_same_thread=False are
    safe to share across uvicorn worker threads for read-only access.

    On boot, open a writeable connection just long enough to run
    init_schema. Migrations are otherwise only applied by the scraper
    writer side, so if the web app is started against a DB that hasn't
    seen the latest version yet (e.g. v4 pricer tables on a long-lived
    DB after a server update), the read-only conn would 500 on the
    first query that touches the new schema.
    """
    import sqlite3

    from odds_scraper.db_schema import init_schema

    pkg_dir = Path(__file__).parent
    templates = Jinja2Templates(directory=str(pkg_dir / "templates"))

    bootstrap = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        init_schema(bootstrap)
    finally:
        bootstrap.close()

    conn = queries.open_ro_conn(db_path)

    app = FastAPI(title="odds-scraper UX")
    app.mount("/static", StaticFiles(directory=str(pkg_dir / "static")), name="static")

    register_pricer_routes(app, templates, db_path=db_path, conn=conn)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, status: str = Query("upcoming")):
        # `status` lets the back link from the event detail page land
        # on the same tab the user came from. Unknown values fall back
        # to upcoming silently — keeps the URL forgiving.
        if status not in queries.VALID_STATUSES:
            status = "upcoming"
        country_league_index = queries.get_country_league_index(conn)
        date_range = queries.get_date_range(conn)
        return templates.TemplateResponse(
            request, "index.html",
            {
                "country_league_index": country_league_index,
                "initial_status": status,
                "date_range": date_range,
            },
        )

    @app.get("/events", response_class=HTMLResponse)
    async def events_fragment(
        request: Request,
        status: str = Query("live"),
        country: str = Query(""),
        league: str = Query(""),
        offset: int = Query(0),
        limit: int = Query(20),
        date_from: str = Query(""),
        date_to: str = Query(""),
    ):
        if status not in queries.VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"unknown status {status!r}")
        rows = queries.get_events_by_status(  # type: ignore[arg-type]
            conn, status, country_id=country, league_id=league,
            offset=offset, limit=limit,
            date_from=date_from, date_to=date_to,
        )
        # Batch the latest-prices fetch across all events: one query for
        # the whole page instead of one per event. Turns ~88s of N+1
        # round-trips on a populated DB into a single sub-second call.
        prices_by_event = queries.get_latest_prices_for_events(
            conn, [row["id"] for row in rows], scope="opened",
        )
        # Bulk lookup of historical max-leads so the engine on each card
        # can deactivate 1UP/2UP sides that already triggered earlier in
        # the match (e.g. score 1-0 → 1-1 must keep home 1UP off).
        latest_leads = pricer_score_state.max_leads_latest_for_events(
            conn, {row["id"] for row in rows},
        )
        events = [
            _build_event_view(
                row, prices_by_event.get(row["id"], []),
                max_leads=latest_leads.get(row["id"], (0, 0)),
            )
            for row in rows
        ]
        template_name = "_events_batch.html" if offset > 0 else "_events_list.html"
        return templates.TemplateResponse(
            request,
            template_name,
            {
                "status": status,
                "events": events,
                "poll_seconds": _POLL_SECONDS[status],
                "offset": offset,
                "limit": limit,
                "country": country,
                "league": league,
                "date_from": date_from,
                "date_to": date_to,
                "has_more": len(events) == limit,
            },
        )

    @app.get("/events/{event_id}", response_class=HTMLResponse)
    async def event_detail(
        request: Request,
        event_id: str,
        market: str = Query(_DEFAULT_MARKET_SLUG),
        from_: str = Query("upcoming", alias="from"),
    ):
        if market not in _PICKER_BY_SLUG:
            raise HTTPException(status_code=400,
                                detail=f"unknown market {market!r}")
        # Sanitize `from` so the back link can't be a hand-crafted
        # arbitrary URL fragment.
        if from_ not in queries.VALID_STATUSES:
            from_ = "upcoming"
        ev_row = queries.get_event_meta(conn, event_id)
        if ev_row is None:
            raise HTTPException(status_code=404,
                                detail=f"event {event_id!r} not found")
        detail = _build_event_detail(conn, ev_row, market, from_status=from_)
        return templates.TemplateResponse(
            request, "event_detail.html", {"event": detail},
        )

    return app


def _build_event_view(
    row, price_rows, *, max_leads: tuple[int, int] = (0, 0),
) -> EventView:
    """Card view: emit one MarketGroup per priced (market, line) tuple in the
    order set by _COLLAPSED_ORDER then _EXPANDER_MARKETS. Each group carries
    a stable group_key the JS layer uses to persist per-market collapse state.

    price_rows are pre-fetched by the route handler (batched across all
    events on the page) — see get_latest_prices_for_events. Splitting the
    fetch from the build keeps the home page on a single DB round-trip
    for prices regardless of event count.

    The detail page (per-event route) handles deep dives.
    """
    bucket: dict[tuple[str, float, str], dict[str, PriceCell]] = {}
    for pr in price_rows:
        key = (pr["market_id"], pr["line"], pr["side"])
        bucket.setdefault(key, {})[pr["bookmaker"]] = PriceCell(
            odds=pr["odds"], probability=pr["probability"],
        )

    groups: list[MarketGroup] = []
    for market_id, group_label, market_short in _COLLAPSED_ORDER:
        rows_for_group = []
        for side in _sides_for(market_id):
            prices = bucket.get((market_id, 0.0, side), {})
            rows_for_group.append(OutcomeRow(
                market_label=market_short,
                side_label=_SIDE_LABEL[side],
                side_short=_SIDE_SHORT[side],
                prices=prices,
            ))
        groups.append(MarketGroup(
            label=group_label, group_key=market_id, rows=rows_for_group,
        ))

    # Parameterized markets as extra (hidden-by-default) groups, in the
    # display order set by _EXPANDER_MARKETS. Only emit a group when at
    # least one outcome is priced for that (market, line) pair.
    for market_id, label_prefix in _EXPANDER_MARKETS:
        spec = _spec_by_id[market_id]
        for line in spec.lines or ():
            rows_for_group = []
            for side in _sides_for(market_id):
                prices = bucket.get((market_id, line, side), {})
                rows_for_group.append(OutcomeRow(
                    market_label=f"{_SHORT_PREFIX[market_id]} {line}",
                    side_label=_SIDE_LABEL[side],
                    side_short=_SIDE_SHORT[side],
                    prices=prices,
                ))
            if any(r.prices for r in rows_for_group):
                groups.append(MarketGroup(
                    label=f"{label_prefix} {line}",
                    group_key=f"{market_id}_{line}",
                    rows=rows_for_group,
                ))

    # Pricer integration: build per-book buckets, run engine on the latest
    # snapshot, surface OUR 1up/2up to the template.
    prices_by_book: dict[str, list] = {}
    for pr in price_rows:
        prices_by_book.setdefault(pr["bookmaker"], []).append({
            "market_id":   pr["market_id"],
            "line":        pr["line"],
            "side":        pr["side"],
            "odds":        pr["odds"],
            "probability": pr["probability"],
        })

    engine_inputs, _basis = pricer_inputs.extract(prices_by_book)
    our_1up_home = our_1up_away = our_2up_home = our_2up_away = None
    our_p_1up_home = our_p_1up_away = our_p_2up_home = our_p_2up_away = None
    if engine_inputs is not None:
        score = (row["score_home"] or 0, row["score_away"] or 0)
        engine_inputs["score"] = (int(score[0]), int(score[1]))
        engine_inputs["max_home_lead"] = max_leads[0]
        engine_inputs["max_away_lead"] = max_leads[1]
        # _cap_source_* metadata lives on the inputs dict but isn't an engine kwarg.
        engine_kwargs = {k: v for k, v in engine_inputs.items() if not k.startswith("_")}
        try:
            result = engine_v2.price_early_payout_markets(**engine_kwargs)
            our_1up_home = result["market_1up"]["home_margin"]
            our_1up_away = result["market_1up"]["away_margin"]
            our_2up_home = result["market_2up"]["home_margin"]
            our_2up_away = result["market_2up"]["away_margin"]
            our_p_1up_home = result["p_home_1"]
            our_p_1up_away = result["p_away_1"]
            our_p_2up_home = result["p_home_2"]
            our_p_2up_away = result["p_away_2"]
        except Exception:  # noqa: BLE001
            pass

    bp_prices = prices_by_book.get("betpawa", [])
    bp_has_1up = any(p["market_id"] == "1x2_1up_ft" and p["side"] in ("home", "away")
                     and p["odds"] is not None for p in bp_prices)
    bp_has_2up = any(p["market_id"] == "1x2_2up_ft" and p["side"] in ("home", "away")
                     and p["odds"] is not None for p in bp_prices)

    return EventView(
        id=row["id"], home=row["home"], away=row["away"],
        kickoff_utc=row["kickoff_utc"], status=row["status"],
        match_minute=row["match_minute"],
        score_home=row["score_home"], score_away=row["score_away"],
        market_groups=groups,
        our_1up_home=our_1up_home, our_1up_away=our_1up_away,
        our_2up_home=our_2up_home, our_2up_away=our_2up_away,
        our_p_1up_home=our_p_1up_home, our_p_1up_away=our_p_1up_away,
        our_p_2up_home=our_p_2up_home, our_p_2up_away=our_p_2up_away,
        bp_has_1up=bp_has_1up, bp_has_2up=bp_has_2up,
    )


def _build_event_detail(
    conn, ev_row, market_slug: str, *, from_status: str = "upcoming",
) -> EventDetail:
    """Build the detail-page view-model for one event + one selected market."""
    market_id, line, market_label = _PICKER_BY_SLUG[market_slug]
    sides = _sides_for(market_id)
    sides_short = tuple(_SIDE_SHORT[s] for s in sides)

    history_rows = queries.get_market_history_for_event(
        conn, ev_row["id"], market_id, line,
    )
    # Bucket by ts: per-bookmaker snapshots at the same ts share identical
    # minute/score/status (all four extract from the same BetPawa detail),
    # so we set state once on the first row encountered for each ts.
    bucket: dict[str, dict] = {}
    for hr in history_rows:
        ts = hr["ts_utc"]
        entry = bucket.setdefault(ts, {
            "cells": {}, "minute": hr["match_minute"],
            "score_home": hr["score_home"], "score_away": hr["score_away"],
            "status": hr["status"] or "",
        })
        entry["cells"].setdefault(hr["bookmaker"], {})[hr["side"]] = PriceCell(
            odds=hr["odds"], probability=hr["probability"],
        )

    # OUR engine output per tick: only loaded for UP markets, where the
    # engine actually produces a result. Merged into each tick's cells
    # under the pseudo-bookmaker key "sim".
    our_by_ts = queries.get_our_history_for_event(
        conn, ev_row["id"], market_id,
    )
    # If OUR has rows at timestamps that have NO prices for this market
    # (typical for live 1UP / 2UP: BP/SB often don't quote them live,
    # B9J/BW are skipped in the live regime, so the prices table is
    # empty for that market — but the engine still computed OUR from
    # the basis book's 1X2+OU+FTTS), backfill empty buckets so the row
    # appears in the timeline carrying just SIM.
    missing_ts = [ts for ts in our_by_ts if ts not in bucket]
    if missing_ts:
        meta_rows = queries.get_snapshot_meta_for_timestamps(
            conn, ev_row["id"], missing_ts,
        )
        for m in meta_rows:
            bucket[m["ts_utc"]] = {
                "cells": {},
                "minute": m["match_minute"],
                "score_home": m["score_home"],
                "score_away": m["score_away"],
                "status": m["status"] or "",
            }

    for ts, our in our_by_ts.items():
        sim_cells: dict[str, PriceCell] = {}
        if our["home_odds"] is not None:
            sim_cells["home"] = PriceCell(
                odds=our["home_odds"], probability=our["home_prob"],
            )
        if our["away_odds"] is not None:
            sim_cells["away"] = PriceCell(
                odds=our["away_odds"], probability=our["away_prob"],
            )
        if sim_cells and ts in bucket:
            bucket[ts]["cells"]["sim"] = sim_cells
        sim_cells_v3: dict[str, PriceCell] = {}
        if our["home_odds_v3"] is not None:
            sim_cells_v3["home"] = PriceCell(
                odds=our["home_odds_v3"], probability=our["home_prob_v3"],
            )
        if our["away_odds_v3"] is not None:
            sim_cells_v3["away"] = PriceCell(
                odds=our["away_odds_v3"], probability=our["away_prob_v3"],
            )
        if sim_cells_v3 and ts in bucket:
            bucket[ts]["cells"]["sim_v3"] = sim_cells_v3

    show_sim_col = bool(our_by_ts) and market_id in ("1x2_1up_ft", "1x2_2up_ft")
    if show_sim_col:
        history_books = ("betpawa", "sportybet", "sim", "sim_v3", "bet9ja", "betway")
    else:
        history_books = ("betpawa", "sportybet", "bet9ja", "betway")

    # Newest first
    history = [
        HistoryRow(
            ts_utc=ts,
            match_minute=bucket[ts]["minute"],
            score_home=bucket[ts]["score_home"],
            score_away=bucket[ts]["score_away"],
            status=bucket[ts]["status"],
            cells=bucket[ts]["cells"],
        )
        for ts in sorted(bucket.keys(), reverse=True)
    ]

    # Build the two-stage pill UI: family chips (always) + line chips (only
    # for parameterized families with data).
    available_lines = queries.get_available_lines(conn, ev_row["id"])

    # Active family — derive from the active slug.
    active_market_id, _active_line, _active_label = _PICKER_BY_SLUG[market_slug]

    # Compute each family's default slug — the URL the family chip points
    # to when clicked.
    family_default_slug: dict[str, str] = {}
    for canonical_id, _label in _FAMILY_PILLS_1X2:
        # 1x2 family default slug equals the canonical_id itself
        family_default_slug[canonical_id] = canonical_id
    for canonical_id, _label in _EXPANDER_MARKETS:
        lines = available_lines.get(canonical_id, [])
        if lines:
            prefix = _spec_by_id[canonical_id].column_prefix
            family_default_slug[canonical_id] = f"{prefix}_{lines[0]}"

    # Family pills: 1x2 trio (always enabled), then the four parameterized
    # families (disabled when no lines are available).
    family_pills: list[tuple[str, str, str, bool, bool]] = []
    for canonical_id, label in _FAMILY_PILLS_1X2:
        family_pills.append((
            canonical_id, label, family_default_slug[canonical_id],
            canonical_id == active_market_id,
            False,
        ))
    for canonical_id, label in _EXPANDER_MARKETS:
        has_lines = bool(available_lines.get(canonical_id))
        family_pills.append((
            canonical_id, label,
            family_default_slug.get(canonical_id, ""),
            canonical_id == active_market_id,
            not has_lines,
        ))

    # Line pills: only if the active family is parameterized AND has data.
    line_pills: list[tuple[str, str, bool]] = []
    expander_ids = {fid for fid, _ in _EXPANDER_MARKETS}
    if active_market_id in expander_ids:
        spec = _spec_by_id[active_market_id]
        prefix = spec.column_prefix
        for line in available_lines.get(active_market_id, []):
            slug = f"{prefix}_{line}"
            line_pills.append((slug, str(line), slug == market_slug))

    return EventDetail(
        id=ev_row["id"],
        home=ev_row["home"],
        away=ev_row["away"],
        kickoff_utc=ev_row["kickoff_utc"],
        status=ev_row["status"],
        match_minute=ev_row["match_minute"],
        score_home=ev_row["score_home"],
        score_away=ev_row["score_away"],
        country_name=ev_row["country_name"] or "",
        league_name=ev_row["league_name"] or "",
        market_label=market_label,
        market_slug=market_slug,
        sides=sides,
        sides_short=sides_short,
        family_pills=family_pills,
        line_pills=line_pills,
        history=history,
        history_books=history_books,
        from_status=from_status,
    )
