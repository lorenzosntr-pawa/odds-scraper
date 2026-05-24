from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from odds_scraper.pricer import configs as config_mod, runner

log = logging.getLogger(__name__)


# Margin constants are stored as (slope, intercept) tuples. Form fields
# arrive as separate `_slope` / `_intercept` inputs, joined here.
_MARGIN_COEFFS = (
    "ONEUP_FAVORITE_MARGIN", "ONEUP_UNDERDOG_MARGIN",
    "TWOUP_FAVORITE_MARGIN", "TWOUP_UNDERDOG_MARGIN",
)


def register_pricer_routes(
    app: FastAPI, templates: Jinja2Templates,
    *, db_path: Path, conn,
) -> None:
    """Attach /simulator + /simulator/runs + /simulator/runs/<id>/csv to `app`.

    `conn` is the read-only connection used by the rest of the app. The
    simulator needs a writeable connection per request to insert run
    rows; we open one fresh inside the POST handler.
    """
    import sqlite3
    csv_dir = db_path.parent / "sim"

    def _open_write_conn() -> sqlite3.Connection:
        """Per-request write connection with WAL-friendly pragmas.

        busy_timeout=30s is the important one: the scraper's writer
        process can be holding the SQLite reserved lock during its own
        tick batch when we land. Without a generous timeout the
        simulator inserts hit `database is locked` immediately.
        """
        c = sqlite3.connect(str(db_path), isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout = 30000")
        return c

    @app.get("/simulator", response_class=HTMLResponse)
    async def simulator_page(request: Request, busy: int = 0):
        profiles = config_mod.list_profiles(conn)
        last_row = conn.execute(
            "SELECT id, created_at, coverage, density, n_events, n_rows, "
            "       state, n_done, n_total "
            "FROM pricer_runs ORDER BY id DESC LIMIT 1",
        ).fetchone()
        last_run = dict(last_row) if last_row else None
        # Running run drives the progress bar at the top of the page.
        running_row = conn.execute(
            "SELECT id, state, n_done, n_total, started_at "
            "FROM pricer_runs WHERE state = 'running' "
            "ORDER BY id DESC LIMIT 1",
        ).fetchone()
        running_run = dict(running_row) if running_row else None
        history_rows = conn.execute(
            "SELECT r.id, r.created_at, c.name AS profile_name, "
            "       r.coverage, r.density, r.n_events, r.n_rows, r.state "
            "FROM pricer_runs r LEFT JOIN pricer_configs c ON c.id = r.config_id "
            "ORDER BY r.id DESC LIMIT 20"
        ).fetchall()
        return templates.TemplateResponse(
            request, "simulator.html",
            {
                "profiles": profiles,
                "last_run": last_run,
                "running_run": running_run,
                "busy": bool(busy),
                "history": [dict(r) for r in history_rows],
            },
        )

    # Serialises check + insert so two concurrent POSTs can't both
    # slip past the "no running run" check and start parallel work.
    post_lock = asyncio.Lock()

    def _run_in_thread(
        profile_id: int, regime: str, density: str, scope: dict,
    ) -> None:
        """Runs in the default executor (a background thread). Each
        invocation opens its own write connection so we don't share
        sqlite handles across threads."""
        write_conn = _open_write_conn()
        try:
            profile = config_mod.load_by_id(write_conn, profile_id)
            if profile is None:
                log.warning("background sim: profile %s vanished", profile_id)
                return
            try:
                runner.run_simulation(
                    write_conn, config=profile,
                    regime=regime, density=density,
                    scope=scope, csv_dir=csv_dir,
                )
            except Exception:
                log.exception("background simulation crashed")
        finally:
            write_conn.close()

    @app.post("/simulator/runs")
    async def post_run(
        config_id: int = Form(...),
        regime:    str = Form("any"),
        density:   str = Form("all"),
        country:   str = Form(""),
        league:    str = Form(""),
        date:      str = Form(""),
        search:    str = Form(""),
    ):
        if regime not in runner.VALID_REGIMES:
            raise HTTPException(400, f"unknown regime {regime!r}")
        if density not in runner.VALID_DENSITIES:
            raise HTTPException(400, f"unknown density {density!r}")
        async with post_lock:
            if runner.is_run_in_progress(conn):
                # Another run is already executing — refuse politely and
                # let the page show the progress bar of the existing run.
                return RedirectResponse(url="/simulator?busy=1", status_code=303)
            # Validate the config before spawning the task so the user
            # gets immediate feedback on a bad profile id.
            probe_conn = _open_write_conn()
            try:
                profile = config_mod.load_by_id(probe_conn, config_id)
            finally:
                probe_conn.close()
            if profile is None:
                raise HTTPException(400, f"unknown config_id {config_id}")
            scope = {"country": country, "league": league,
                     "date": date, "search": search}
            loop = asyncio.get_running_loop()
            loop.run_in_executor(
                None, _run_in_thread, config_id, regime, density, scope,
            )
        return RedirectResponse(url="/simulator", status_code=303)

    @app.get("/simulator/scope", response_class=HTMLResponse)
    async def scope_preview(
        regime: str = "any", density: str = "all",
        country: str = "", league: str = "",
        date: str = "", search: str = "",
    ):
        """Tiny HTMX-target endpoint: returns a single line like
        '12 events · 3,400 ticks' so the form can show the size of
        the scope as the user toggles radios."""
        if regime not in runner.VALID_REGIMES or density not in runner.VALID_DENSITIES:
            return HTMLResponse("<span class='filter-lbl'>invalid scope</span>")
        scope = {"country": country, "league": league, "date": date, "search": search}
        n_ev, n_snap = runner.count_scope(conn, regime, density, scope)
        return HTMLResponse(
            f'<span class="filter-lbl">'
            f'<b style="color:#4ade80">{n_ev:,}</b> events &middot; '
            f'<b style="color:#4ade80">{n_snap:,}</b> ticks in scope'
            f'</span>'
        )

    @app.get("/simulator/runs/{run_id}/status")
    async def get_run_status(run_id: int):
        row = conn.execute(
            "SELECT state, n_done, n_total, n_rows, n_events, "
            "       started_at, finished_at, csv_path "
            "FROM pricer_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"no such run {run_id}")
        d = dict(row)
        d["progress_pct"] = (
            int(100 * d["n_done"] / d["n_total"])
            if d["n_total"] else (100 if d["state"] == "done" else 0)
        )
        return JSONResponse(d)

    @app.get("/simulator/profiles", response_class=HTMLResponse)
    async def profiles_page(request: Request):
        profiles = config_mod.list_profiles(conn)
        return templates.TemplateResponse(
            request, "profiles.html",
            {
                "profiles": profiles,
                "tunable_names": config_mod.TUNABLE_NAMES,
                "margin_coeffs": _MARGIN_COEFFS,
                "default_coefficients": config_mod.DEFAULT_COEFFICIENTS,
            },
        )

    @app.post("/simulator/profiles")
    async def create_profile_route(request: Request):
        form = await request.form()
        name = (form.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        coefficients: dict = {}
        for k in config_mod.TUNABLE_NAMES:
            if k in _MARGIN_COEFFS:
                slope_raw = form.get(f"{k}_slope")
                inter_raw = form.get(f"{k}_intercept")
                try:
                    coefficients[k] = [float(slope_raw), float(inter_raw)]
                except (TypeError, ValueError):
                    raise HTTPException(400, f"invalid value for {k}")
            else:
                raw = form.get(k)
                try:
                    coefficients[k] = float(raw)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"invalid value for {k}")
        write_conn = _open_write_conn()
        try:
            try:
                config_mod.create_profile(write_conn, name, coefficients)
            except ValueError as e:
                raise HTTPException(400, str(e))
            except sqlite3.IntegrityError:
                raise HTTPException(
                    400, f"profile name {name!r} already exists",
                )
        finally:
            write_conn.close()
        return RedirectResponse(url="/simulator/profiles", status_code=303)

    @app.post("/simulator/profiles/{profile_id}/delete")
    async def delete_profile_route(profile_id: int):
        write_conn = _open_write_conn()
        try:
            try:
                config_mod.delete_profile(write_conn, profile_id)
            except ValueError as e:
                raise HTTPException(400, str(e))
        finally:
            write_conn.close()
        return RedirectResponse(url="/simulator/profiles", status_code=303)

    @app.get("/simulator/runs/{run_id}/csv")
    async def get_run_csv(run_id: int):
        row = conn.execute(
            "SELECT csv_path FROM pricer_runs WHERE id = ?", (run_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"no such run {run_id}")
        full_path = db_path.parent / row["csv_path"]
        if not full_path.exists():
            raise HTTPException(404, f"csv missing on disk for run {run_id}")
        return FileResponse(str(full_path), media_type="text/csv",
                            filename=f"pricer_run_{run_id:04d}.csv")
