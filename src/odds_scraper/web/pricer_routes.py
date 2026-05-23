from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from odds_scraper.pricer import configs as config_mod, runner


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

    @app.get("/simulator", response_class=HTMLResponse)
    async def simulator_page(request: Request):
        profiles = config_mod.list_profiles(conn)
        last_row = conn.execute(
            "SELECT id, created_at, coverage, n_events, n_rows "
            "FROM pricer_runs ORDER BY id DESC LIMIT 1",
        ).fetchone()
        last_run = dict(last_row) if last_row else None
        history_rows = conn.execute(
            "SELECT r.id, r.created_at, c.name AS profile_name, r.coverage, "
            "       r.n_events, r.n_rows "
            "FROM pricer_runs r LEFT JOIN pricer_configs c ON c.id = r.config_id "
            "ORDER BY r.id DESC LIMIT 20"
        ).fetchall()
        return templates.TemplateResponse(
            request, "simulator.html",
            {
                "profiles": profiles,
                "last_run": last_run,
                "history": [dict(r) for r in history_rows],
            },
        )

    @app.post("/simulator/runs")
    async def post_run(
        config_id: int = Form(...),
        coverage:  str = Form(...),
        status:    str = Form(""),
        country:   str = Form(""),
        league:    str = Form(""),
        date:      str = Form(""),
        search:    str = Form(""),
    ):
        if coverage not in ("all", "latest", "prematch", "live"):
            raise HTTPException(400, f"unknown coverage {coverage!r}")
        write_conn = sqlite3.connect(str(db_path), isolation_level=None)
        write_conn.row_factory = sqlite3.Row
        try:
            profile = config_mod.load_by_id(write_conn, config_id)
            if profile is None:
                raise HTTPException(400, f"unknown config_id {config_id}")
            scope = {"status": status, "country": country, "league": league,
                     "date": date, "search": search}
            run_id = runner.run_simulation(
                write_conn, config=profile,
                coverage=coverage, scope=scope, csv_dir=csv_dir,
            )
        finally:
            write_conn.close()
        return RedirectResponse(url=f"/simulator#run-{run_id}", status_code=303)

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
