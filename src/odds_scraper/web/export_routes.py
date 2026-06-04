from __future__ import annotations

import csv
import io
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import export_service as ex, queries

_MAX_ROWS = 2_000_000


def register_export_routes(
    app: FastAPI, templates: Jinja2Templates, *, db_path: Path, conn,
) -> None:
    """Attach /export routes. `conn` is the long-lived read-only connection."""

    def _scope(country, league, event_id, date, search) -> dict:
        return {"country": country, "league": league, "event_id": event_id,
                "date": date, "search": search}

    def _parse_markets(market: list[str]) -> list[tuple[str, float]] | None:
        if not market:
            return None
        out: list[tuple[str, float]] = []
        for tok in market:
            mid, _, line = tok.partition("|")
            try:
                out.append((mid, float(line or 0.0)))
            except ValueError:
                continue
        return out or None

    def _build_ticks(regime, density, scope, markets, first_n, last_n):
        ticks = ex.select_ticks(conn, regime, density, scope)
        if density == "onchange":
            ticks = ex.collapse_onchange(conn, ticks, markets)
        ticks = ex.limit_first_last(ticks, first_n, last_n)
        return ticks

    @app.get("/export", response_class=HTMLResponse)
    async def export_page(request: Request):
        return templates.TemplateResponse(request, "export.html", {
            "country_league_index": queries.get_country_league_index(conn),
            "markets": ex.available_markets(conn, {}),
            "sim_engines": ex.SIM_ENGINES,
        })

    @app.get("/export/markets", response_class=HTMLResponse)
    async def export_markets(country: str = "", league: str = "",
                             event_id: str = "", date: str = ""):
        scope = _scope(country, league, event_id, date, "")
        from html import escape as e
        parts = []
        for mid, line in ex.available_markets(conn, scope):
            val = f"{mid}|{line}"
            lbl = mid if line == 0.0 else f"{mid} @ {line}"
            parts.append(
                f'<label><input type="checkbox" name="market" value="{e(val)}" checked> {e(lbl)}</label>')
        return HTMLResponse("".join(parts) or "<span>no markets in scope</span>")

    @app.get("/export/count", response_class=HTMLResponse)
    async def export_count(regime: str = "any", density: str = "all",
                           country: str = "", league: str = "", event_id: str = "",
                           date: str = "", search: str = "",
                           market: list[str] = Query(default=[])):
        try:
            markets = _parse_markets(market)
            ticks = _build_ticks(regime, density,
                                 _scope(country, league, event_id, date, search),
                                 markets, 0, 0)
        except ValueError:
            return HTMLResponse("<span class='filter-lbl'>invalid scope</span>")
        n_ev = len({t["event_id"] for t in ticks})
        return HTMLResponse(
            f"<span class='filter-lbl'><b>{n_ev:,}</b> events &middot; "
            f"<b>{len(ticks):,}</b> snapshots in scope</span>")

    @app.get("/export.csv")
    async def export_csv(
        regime: str = "any", density: str = "all", format: str = "long",
        country: str = "", league: str = "", event_id: str = "",
        date: str = "", search: str = "",
        market: list[str] = Query(default=[]),
        book: list[str] = Query(default=[]),
        first_n: int = 0, last_n: int = 0,
        sim: int = 0, engine: list[str] = Query(default=[]),
    ):
        if regime not in ex.VALID_REGIMES:
            raise HTTPException(400, f"unknown regime {regime!r}")
        if density not in ex.VALID_DENSITIES:
            raise HTTPException(400, f"unknown density {density!r}")
        if format not in ("long", "wide"):
            raise HTTPException(400, f"unknown format {format!r}")
        markets = _parse_markets(market)
        books = book or None
        sim_engines = tuple(en for en in ex.SIM_ENGINES if en in set(engine)) if sim else ()
        scope = _scope(country, league, event_id, date, search)
        ticks = _build_ticks(regime, density, scope, markets, first_n, last_n)
        long_iter = ex.iter_long_rows(conn, ticks, markets=markets, books=books,
                                      sim_engines=sim_engines)
        suffix = "_with_simulated" if sim_engines else ""
        fname = f"odds_export_{regime}_{density}{suffix}.csv"

        def _emit_long():
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=ex.LONG_COLUMNS, extrasaction="ignore")
            w.writeheader(); yield buf.getvalue(); buf.seek(0); buf.truncate(0)
            n = 0
            for row in long_iter:
                if n >= _MAX_ROWS:
                    break
                w.writerow({k: ex.csv_safe(v) for k, v in row.items()})
                yield buf.getvalue(); buf.seek(0); buf.truncate(0); n += 1

        def _emit_wide():
            cols, rows = ex.to_wide_rows(long_iter)
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            w.writeheader(); yield buf.getvalue(); buf.seek(0); buf.truncate(0)
            for row in rows[:_MAX_ROWS]:
                w.writerow({k: ex.csv_safe(v) for k, v in row.items()})
                yield buf.getvalue(); buf.seek(0); buf.truncate(0)

        gen = _emit_wide() if format == "wide" else _emit_long()
        return StreamingResponse(
            gen, media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'})
