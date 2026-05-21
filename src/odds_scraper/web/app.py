from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import queries

# Markets visible by default in collapsed view, in display order.
# Derived from queries.COLLAPSED_MARKETS for a single source of truth.
_MARKET_LABELS = {
    "1x2_ft":     ("1x2 — Full Time", "1x2 ft"),
    "1x2_1up_ft": ("1x2 — 1 Up",      "1x2 1up"),
    "1x2_2up_ft": ("1x2 — 2 Up",      "1x2 2up"),
}
_COLLAPSED_ORDER: tuple[tuple[str, str, str], ...] = tuple(
    (m, *_MARKET_LABELS[m]) for m in queries.COLLAPSED_MARKETS
)

# Order in which OU lines render once an event is opened
_OU_LINES = (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5)

_POLL_SECONDS = {"live": 5, "upcoming": 30, "ended": 60}

_SIDE_LABEL = {
    "home": "Home", "draw": "Draw", "away": "Away",
    "over": "Over", "under": "Under",
}

_SIDE_SHORT = {
    "home": "H", "draw": "D", "away": "A", "over": "O", "under": "U",
}


@dataclass
class PriceCell:
    odds: float
    probability: Optional[float]


@dataclass
class OutcomeRow:
    market_label: str       # e.g., "1x2 ft" (used in collapsed view)
    side_label: str         # e.g., "Home" (used in opened view)
    side_short: str         # e.g., "H"
    prices: dict[str, PriceCell]


@dataclass
class MarketGroup:
    label: str              # e.g., "1x2 — Full Time"
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
        open_param: str = Query("", alias="open"),
    ):
        if status not in queries.VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"unknown status {status!r}")
        open_ids = [s for s in open_param.split(",") if s]
        rows = queries.get_events_by_status(conn, status)  # type: ignore[arg-type]
        events = [_build_event_view(conn, row, open_ids) for row in rows]
        return templates.TemplateResponse(
            request,
            "_events_list.html",
            {
                "status": status,
                "events": events,
                "open_ids": open_ids,
                "poll_seconds": _POLL_SECONDS[status],
            },
        )

    return app


def _build_event_view(conn, row, open_ids: list[str]) -> EventView:
    is_open = row["id"] in open_ids
    scope = "opened" if is_open else "collapsed"
    price_rows = queries.get_latest_prices_for_event(
        conn, row["id"], scope=scope,  # type: ignore[arg-type]
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
        for side in ("home", "draw", "away"):
            prices = bucket.get((market_id, 0.0, side), {})
            rows_for_group.append(OutcomeRow(
                market_label=market_short,
                side_label=_SIDE_LABEL[side],
                side_short=_SIDE_SHORT[side],
                prices=prices,
            ))
        groups.append(MarketGroup(label=group_label, rows=rows_for_group))

    if is_open:
        for line in _OU_LINES:
            rows_for_group = []
            for side in ("over", "under"):
                prices = bucket.get(("over_under_ft", line, side), {})
                rows_for_group.append(OutcomeRow(
                    market_label=f"OU {line}",
                    side_label=_SIDE_LABEL[side],
                    side_short=_SIDE_SHORT[side],
                    prices=prices,
                ))
            if any(r.prices for r in rows_for_group):
                groups.append(MarketGroup(
                    label=f"Over/Under {line}", rows=rows_for_group,
                ))

    return EventView(
        id=row["id"], home=row["home"], away=row["away"],
        kickoff_utc=row["kickoff_utc"], status=row["status"],
        match_minute=row["match_minute"],
        score_home=row["score_home"], score_away=row["score_away"],
        market_groups=groups,
    )
