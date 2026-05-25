from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from odds_scraper.pricer import configs as config_mod, runner, runner_v2


# Engine choice the simulator page can submit. "both" runs the dual
# runner emitting v1_* + v2_* columns side-by-side. "v1" stays on the
# pre-V2 runner (byte-identical column layout) for parity. "v2" runs
# only V2 (V1 cells stay blank in the CSV).
VALID_ENGINE_CHOICES = ("v1", "v2", "both")

from . import queries

log = logging.getLogger(__name__)


# Margin constants are stored as (slope, intercept) tuples. Form fields
# arrive as separate `_slope` / `_intercept` inputs, joined here.
_MARGIN_COEFFS = (
    "ONEUP_FAVORITE_MARGIN", "ONEUP_UNDERDOG_MARGIN",
    "ONEUP_TRAILING_FAVORITE_MARGIN", "ONEUP_TRAILING_UNDERDOG_MARGIN",
    "TWOUP_FAVORITE_MARGIN", "TWOUP_UNDERDOG_MARGIN",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RunRecord:
    """Per-process record of a simulator run. The simulator no longer
    persists to the database — it just produces a CSV. Progress and
    history are kept in this in-memory registry for the lifetime of
    the web app process.

    The CSV file on disk outlives the registry; if the user restarts
    the web app, old runs disappear from the History list but their
    CSVs remain on disk at `data/sim/<csv_name>`.
    """
    id: int
    state: str  # 'running' | 'done' | 'failed'
    profile_name: str
    profile_b_name: str  # empty when no profile B was selected
    regime: str
    density: str
    engines: str  # 'v1' | 'v2' | 'v1,v2'
    started_at: str
    n_done: int = 0
    n_total: int = 0
    n_events: int = 0
    n_rows: int = 0
    csv_name: str = ""
    finished_at: Optional[str] = None
    error: str = ""

    def progress_pct(self) -> int:
        if self.n_total:
            return int(100 * self.n_done / self.n_total)
        return 100 if self.state == "done" else 0


class RunRegistry:
    """Thread/async-safe registry of simulator runs. `create` runs in
    the asyncio loop under a lock to serialise the single-flight
    check + id allocation; `update_progress`, `mark_done`, and
    `mark_failed` are called from the worker thread and rely on the
    GIL for atomic dict updates."""

    def __init__(self) -> None:
        self._runs: dict[int, RunRecord] = {}
        self._next_id: int = 1
        self._lock = asyncio.Lock()

    async def acquire_id_if_idle(
        self, *, profile_name: str, profile_b_name: str,
        regime: str, density: str, engines: str,
    ) -> Optional[int]:
        """Allocate a new run id and a 'running' record, but only if no
        other run is currently in flight. Returns None when busy."""
        async with self._lock:
            if any(r.state == "running" for r in self._runs.values()):
                return None
            run_id = self._next_id
            self._next_id += 1
            self._runs[run_id] = RunRecord(
                id=run_id, state="running",
                profile_name=profile_name, profile_b_name=profile_b_name,
                regime=regime, density=density,
                engines=engines,
                started_at=_now_iso(),
            )
            return run_id

    def is_any_running(self) -> bool:
        return any(r.state == "running" for r in self._runs.values())

    def get(self, run_id: int) -> Optional[RunRecord]:
        return self._runs.get(run_id)

    def list_recent(self, limit: int = 20) -> list[RunRecord]:
        return sorted(self._runs.values(), key=lambda r: r.id, reverse=True)[:limit]

    def update_progress(self, run_id: int, n_done: int, n_total: int) -> None:
        r = self._runs.get(run_id)
        if r is None:
            return
        r.n_done = n_done
        r.n_total = n_total

    def mark_done(
        self, run_id: int, *, n_events: int, n_rows: int, csv_name: str,
    ) -> None:
        r = self._runs.get(run_id)
        if r is None:
            return
        r.state = "done"
        r.n_events = n_events
        r.n_rows = n_rows
        r.csv_name = csv_name
        r.n_done = r.n_total
        r.finished_at = _now_iso()

    def mark_failed(self, run_id: int, *, error: str) -> None:
        r = self._runs.get(run_id)
        if r is None:
            return
        r.state = "failed"
        r.error = error
        r.finished_at = _now_iso()


def register_pricer_routes(
    app: FastAPI, templates: Jinja2Templates,
    *, db_path: Path, conn,
) -> None:
    """Attach /simulator routes to `app`. `conn` is the long-lived
    read-only connection. The simulator's writeable connections are
    only for reading prices/snapshots (the runner doesn't write back),
    plus the profile-management CRUD."""
    import sqlite3

    csv_dir = db_path.parent / "sim"
    registry = RunRegistry()
    app.state.run_registry = registry
    app.state.sim_csv_dir = csv_dir

    def _open_write_conn() -> sqlite3.Connection:
        c = sqlite3.connect(str(db_path), isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout = 30000")
        return c

    def _csv_filename(run_id: int, started_at: str) -> str:
        # Use a sortable filename so a directory listing reads naturally.
        ts = started_at.replace(":", "-")
        return f"run_{run_id:04d}_{ts}.csv"

    def _run_in_thread(
        run_id: int, profile_id: int, regime: str, density: str,
        scope: dict, csv_name: str, engine_choice: str,
        profile_b_id: int,
    ) -> None:
        """Runs in the default executor (a background thread). The
        simulator no longer writes to the DB; results land in the CSV
        and progress lands in the in-memory registry. Dispatches to
        the V1 runner (engine='v1' AND profile_b_id == 0) or the dual
        runner (V2 selected OR profile B selected — the dual runner is
        the only one that knows how to emit `pB_*` columns)."""
        write_conn = _open_write_conn()
        try:
            profile = config_mod.load_by_id(write_conn, profile_id)
            if profile is None:
                registry.mark_failed(run_id, error="profile vanished")
                return
            profile_b = None
            if profile_b_id:
                profile_b = config_mod.load_by_id(write_conn, profile_b_id)
                if profile_b is None:
                    registry.mark_failed(run_id, error="profile B vanished")
                    return
            try:
                # Single-profile V1-only path stays on the lean runner;
                # anything else needs the dual runner (it owns the pB_*
                # column writes and the engine-fanout logic).
                if engine_choice == "v1" and profile_b is None:
                    n_events, n_rows = runner.run_simulation(
                        write_conn, config=profile,
                        regime=regime, density=density,
                        scope=scope, csv_path=csv_dir / csv_name,
                        on_progress=lambda done, total: registry.update_progress(
                            run_id, done, total,
                        ),
                    )
                else:
                    engines = (
                        ("v1",) if engine_choice == "v1"
                        else ("v2",) if engine_choice == "v2"
                        else ("v1", "v2")
                    )
                    n_events, n_rows = runner_v2.run_simulation_dual(
                        write_conn, config=profile,
                        config_b=profile_b,
                        regime=regime, density=density,
                        scope=scope, csv_path=csv_dir / csv_name,
                        engines=engines,
                        on_progress=lambda done, total: registry.update_progress(
                            run_id, done, total,
                        ),
                    )
            except Exception as exc:  # noqa: BLE001
                log.exception("background simulation crashed")
                registry.mark_failed(run_id, error=f"{type(exc).__name__}: {exc}")
                return
            registry.mark_done(
                run_id, n_events=n_events, n_rows=n_rows, csv_name=csv_name,
            )
        finally:
            write_conn.close()

    @app.get("/simulator", response_class=HTMLResponse)
    async def simulator_page(request: Request, busy: int = 0):
        profiles = config_mod.list_profiles(conn)
        recent = registry.list_recent(20)
        running_run = next(
            (r for r in recent if r.state == "running"), None,
        )
        last_run = recent[0] if recent else None
        return templates.TemplateResponse(
            request, "simulator.html",
            {
                "profiles": profiles,
                "last_run": last_run,
                "running_run": running_run,
                "busy": bool(busy),
                "history": recent,
                "country_league_index": queries.get_country_league_index(conn),
            },
        )

    @app.post("/simulator/runs")
    async def post_run(
        config_id: int = Form(...),
        config_id_b: int = Form(0),
        regime:    str = Form("any"),
        density:   str = Form("all"),
        engine:    str = Form("both"),
        country:   str = Form(""),
        league:    str = Form(""),
        event_id:  str = Form(""),
        date:      str = Form(""),
        search:    str = Form(""),
    ):
        if regime not in runner.VALID_REGIMES:
            raise HTTPException(400, f"unknown regime {regime!r}")
        if density not in runner.VALID_DENSITIES:
            raise HTTPException(400, f"unknown density {density!r}")
        if engine not in VALID_ENGINE_CHOICES:
            raise HTTPException(400, f"unknown engine {engine!r}")
        engines_str = "v1" if engine == "v1" else ("v2" if engine == "v2" else "v1,v2")
        probe_conn = _open_write_conn()
        try:
            profile = config_mod.load_by_id(probe_conn, config_id)
            profile_b = (
                config_mod.load_by_id(probe_conn, config_id_b)
                if config_id_b else None
            )
        finally:
            probe_conn.close()
        if profile is None:
            raise HTTPException(400, f"unknown config_id {config_id}")
        if config_id_b and profile_b is None:
            raise HTTPException(400, f"unknown config_id_b {config_id_b}")
        if profile_b is not None and profile_b.id == profile.id:
            raise HTTPException(
                400, "config_id_b must differ from config_id",
            )
        run_id = await registry.acquire_id_if_idle(
            profile_name=profile.name,
            profile_b_name=(profile_b.name if profile_b else ""),
            regime=regime, density=density,
            engines=engines_str,
        )
        if run_id is None:
            # Another run is already executing — refuse politely and
            # let the page show its progress bar.
            return RedirectResponse(url="/simulator?busy=1", status_code=303)
        rec = registry.get(run_id)
        csv_name = _csv_filename(run_id, rec.started_at if rec else _now_iso())
        scope = {"country": country, "league": league,
                 "event_id": event_id, "date": date, "search": search}
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None, _run_in_thread,
            run_id, config_id, regime, density, scope, csv_name, engine,
            config_id_b,
        )
        return RedirectResponse(url="/simulator", status_code=303)

    @app.get("/simulator/scope", response_class=HTMLResponse)
    async def scope_preview(
        regime: str = "any", density: str = "all",
        country: str = "", league: str = "",
        event_id: str = "", date: str = "", search: str = "",
    ):
        if regime not in runner.VALID_REGIMES or density not in runner.VALID_DENSITIES:
            return HTMLResponse("<span class='filter-lbl'>invalid scope</span>")
        scope = {"country": country, "league": league,
                 "event_id": event_id, "date": date, "search": search}
        n_ev, n_snap = runner.count_scope(conn, regime, density, scope)
        return HTMLResponse(
            f'<span class="filter-lbl">'
            f'<b style="color:#4ade80">{n_ev:,}</b> events &middot; '
            f'<b style="color:#4ade80">{n_snap:,}</b> ticks in scope'
            f'</span>'
        )

    @app.get("/simulator/options/events", response_class=HTMLResponse)
    async def event_options(
        country: str = "", league: str = "",
        date: str = "", search: str = "",
    ):
        """HTML <option> list for the simulator's event picker. Filtered
        by the same scope inputs as the run itself, so the picker only
        offers events that would actually be in scope. HTMX target."""
        clauses = ["country_name IS NOT NULL AND country_name != ''",
                   "home != '' AND away != ''"]
        params: list = []
        if country:
            clauses.append("country_id = ?"); params.append(country)
        if league:
            clauses.append("league_id = ?"); params.append(league)
        if date:
            clauses.append("DATE(kickoff_utc) = ?"); params.append(date)
        if search:
            clauses.append("(LOWER(home) LIKE ? OR LOWER(away) LIKE ?)")
            like = f"%{search.lower()}%"
            params.extend([like, like])
        # Cap the list — a wide-open filter would otherwise dump every
        # event in the DB into the dropdown.
        sql = (
            "SELECT id, home, away, kickoff_utc "
            "FROM events WHERE " + " AND ".join(clauses) + " "
            "ORDER BY kickoff_utc DESC LIMIT 500"
        )
        rows = conn.execute(sql, params).fetchall()
        parts = ['<option value="">All matching events</option>']
        for r in rows:
            kickoff = (r["kickoff_utc"] or "")[:16].replace("T", " ")
            label = f"{kickoff} · {r['home']} v {r['away']}"
            parts.append(
                f'<option value="{r["id"]}">{label}</option>'
            )
        return HTMLResponse("".join(parts))

    @app.get("/simulator/runs/{run_id}/status")
    async def get_run_status(run_id: int):
        r = registry.get(run_id)
        if r is None:
            raise HTTPException(404, f"no such run {run_id}")
        return JSONResponse({
            "id": r.id, "state": r.state,
            "n_done": r.n_done, "n_total": r.n_total,
            "n_events": r.n_events, "n_rows": r.n_rows,
            "started_at": r.started_at, "finished_at": r.finished_at,
            "csv_name": r.csv_name, "error": r.error,
            "progress_pct": r.progress_pct(),
        })

    @app.get("/simulator/runs/{run_id}/csv")
    async def get_run_csv(run_id: int):
        r = registry.get(run_id)
        if r is None:
            raise HTTPException(404, f"no such run {run_id}")
        if not r.csv_name:
            raise HTTPException(404, f"run {run_id} has no csv (still running?)")
        full = csv_dir / r.csv_name
        if not full.exists():
            raise HTTPException(404, f"csv missing on disk for run {run_id}")
        return FileResponse(
            str(full), media_type="text/csv",
            filename=f"pricer_{r.csv_name}",
        )

    # ----- profile management (unchanged) ---------------------------------

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

    def _parse_profile_form(form) -> tuple[str, dict]:
        """Pull (name, coefficients) out of the create/edit form. Raises
        HTTPException(400) on missing name or non-numeric values so the
        caller doesn't have to repeat the same try/except dance.

        Boolean flag fields (FLAG_NAMES) are read as checkboxes: present
        in the form = True, absent = False. Browsers don't submit
        unchecked checkboxes, so absence is the signal."""
        name = (form.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        coefficients: dict = {}
        for k in config_mod.TUNABLE_NAMES:
            is_v2_only = k in config_mod.V2_ONLY_TUNABLE_NAMES
            if k in _MARGIN_COEFFS:
                slope_raw = form.get(f"{k}_slope")
                inter_raw = form.get(f"{k}_intercept")
                # V2-only fields absent from the form (e.g. older
                # browser cache) fall back to defaults via
                # _validate_and_fill rather than rejecting the submit.
                if is_v2_only and (slope_raw is None or inter_raw is None):
                    continue
                try:
                    coefficients[k] = [float(slope_raw), float(inter_raw)]
                except (TypeError, ValueError):
                    raise HTTPException(400, f"invalid value for {k}")
            else:
                raw = form.get(k)
                if is_v2_only and raw is None:
                    continue
                try:
                    coefficients[k] = float(raw)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"invalid value for {k}")
        for k in config_mod.FLAG_NAMES:
            coefficients[k] = k in form
        return name, coefficients

    @app.post("/simulator/profiles")
    async def create_profile_route(request: Request):
        form = await request.form()
        name, coefficients = _parse_profile_form(form)
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

    @app.get("/simulator/profiles/{profile_id}/edit", response_class=HTMLResponse)
    async def edit_profile_page(request: Request, profile_id: int):
        profile = config_mod.load_by_id(conn, profile_id)
        if profile is None:
            raise HTTPException(404, f"no such profile {profile_id}")
        if profile.is_default:
            raise HTTPException(400, "cannot edit the default profile")
        return templates.TemplateResponse(
            request, "profile_edit.html",
            {
                "profile": profile,
                "margin_coeffs": _MARGIN_COEFFS,
            },
        )

    @app.post("/simulator/profiles/{profile_id}")
    async def edit_profile_route(request: Request, profile_id: int):
        form = await request.form()
        name, coefficients = _parse_profile_form(form)
        write_conn = _open_write_conn()
        try:
            try:
                config_mod.update_profile(
                    write_conn, profile_id, name, coefficients,
                )
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
