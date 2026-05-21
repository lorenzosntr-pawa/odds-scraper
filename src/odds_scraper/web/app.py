from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from odds_scraper.models import MARKET_MANIFEST, MarketSpec

from . import queries

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

# Default market for the detail page when none specified — focus is 2up
_DEFAULT_MARKET_SLUG = "1x2_2up_ft"

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
    rows: list[OutcomeRow]
    # is_extra=True groups are hidden by default in the card view; revealed
    # via the expand toggle. The detail page uses the market-picker pills
    # instead and ignores this flag.
    is_extra: bool = False


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


@dataclass
class HistoryRow:
    """One snapshot's prices for a single market, all bookmakers/sides."""
    ts_utc: str
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
    # Currently selected market info
    market_label: str
    market_slug: str
    sides: tuple[str, ...]
    sides_short: tuple[str, ...]
    # Pills: list of (slug, label, is_active)
    pills: list[tuple[str, str, bool]]
    # History rows newest first
    history: list[HistoryRow]


def create_app(db_path: Path) -> FastAPI:
    """Build the FastAPI app with a read-only sqlite handle.

    db_path is captured in the closure so handlers reuse one connection per
    process. SQLite connections in WAL mode + check_same_thread=False are
    safe to share across uvicorn worker threads for read-only access.
    """
    pkg_dir = Path(__file__).parent
    templates = Jinja2Templates(directory=str(pkg_dir / "templates"))

    conn = queries.open_ro_conn(db_path)

    app = FastAPI(title="odds-scraper UX")
    app.mount("/static", StaticFiles(directory=str(pkg_dir / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {})

    @app.get("/events", response_class=HTMLResponse)
    async def events_fragment(
        request: Request,
        status: str = Query("live"),
    ):
        if status not in queries.VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"unknown status {status!r}")
        rows = queries.get_events_by_status(conn, status)  # type: ignore[arg-type]
        events = [_build_event_view(conn, row) for row in rows]
        return templates.TemplateResponse(
            request,
            "_events_list.html",
            {
                "status": status,
                "events": events,
                "poll_seconds": _POLL_SECONDS[status],
            },
        )

    @app.get("/events/{event_id}", response_class=HTMLResponse)
    async def event_detail(
        request: Request,
        event_id: str,
        market: str = Query(_DEFAULT_MARKET_SLUG),
    ):
        if market not in _PICKER_BY_SLUG:
            raise HTTPException(status_code=400,
                                detail=f"unknown market {market!r}")
        ev_row = queries.get_event_meta(conn, event_id)
        if ev_row is None:
            raise HTTPException(status_code=404,
                                detail=f"event {event_id!r} not found")
        detail = _build_event_detail(conn, ev_row, market)
        return templates.TemplateResponse(
            request, "event_detail.html", {"event": detail},
        )

    return app


def _build_event_view(conn, row) -> EventView:
    """Card view: 1x2 family always; OU groups also included (marked as
    is_extra) so the template can render them in a collapsible region.

    The detail page (per-event route) handles deep dives.
    """
    price_rows = queries.get_latest_prices_for_event(
        conn, row["id"], scope="opened",
    )
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
            label=group_label, rows=rows_for_group, is_extra=False,
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
                    rows=rows_for_group,
                    is_extra=True,
                ))

    return EventView(
        id=row["id"], home=row["home"], away=row["away"],
        kickoff_utc=row["kickoff_utc"], status=row["status"],
        match_minute=row["match_minute"],
        score_home=row["score_home"], score_away=row["score_away"],
        market_groups=groups,
    )


def _build_event_detail(conn, ev_row, market_slug: str) -> EventDetail:
    """Build the detail-page view-model for one event + one selected market."""
    market_id, line, market_label = _PICKER_BY_SLUG[market_slug]
    sides = _sides_for(market_id)
    sides_short = tuple(_SIDE_SHORT[s] for s in sides)

    history_rows = queries.get_market_history_for_event(
        conn, ev_row["id"], market_id, line,
    )
    # Bucket by ts: {ts_utc: {bookmaker: {side: PriceCell}}}
    bucket: dict[str, dict[str, dict[str, PriceCell]]] = {}
    for hr in history_rows:
        ts = hr["ts_utc"]
        bm_cells = bucket.setdefault(ts, {})
        bm_cells.setdefault(hr["bookmaker"], {})[hr["side"]] = PriceCell(
            odds=hr["odds"], probability=hr["probability"],
        )

    # Newest first
    history = [
        HistoryRow(ts_utc=ts, cells=bucket[ts])
        for ts in sorted(bucket.keys(), reverse=True)
    ]

    pills = [
        (slug, label, slug == market_slug)
        for mid, _ln, label, slug in _MARKET_PICKER
    ]

    return EventDetail(
        id=ev_row["id"],
        home=ev_row["home"],
        away=ev_row["away"],
        kickoff_utc=ev_row["kickoff_utc"],
        status=ev_row["status"],
        match_minute=ev_row["match_minute"],
        score_home=ev_row["score_home"],
        score_away=ev_row["score_away"],
        market_label=market_label,
        market_slug=market_slug,
        sides=sides,
        sides_short=sides_short,
        pills=pills,
        history=history,
    )
