from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CadenceConfig:
    prematch_seconds: int
    live_seconds: int
    status_retry_backoff_seconds: tuple[int, ...]
    watchdog_after_kickoff_seconds: int


@dataclass(frozen=True)
class OutputConfig:
    db_path: str
    resolution_cache_path: str


@dataclass(frozen=True)
class AppConfig:
    country: str
    events: tuple[str, ...]
    tournaments: tuple[str, ...]
    refresh_interval_seconds: int
    refresh_interval_when_idle_seconds: int
    cadence: CadenceConfig
    output: OutputConfig
    log_level: str


def load_config(path: Path | str) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cad = raw["cadence"]
    out = raw["output"]
    return AppConfig(
        country=str(raw["country"]),
        events=tuple(str(e) for e in (raw.get("events") or [])),
        tournaments=tuple(str(t) for t in (raw.get("tournaments") or [])),
        refresh_interval_seconds=int(raw.get("refresh_interval_seconds", 86400)),
        refresh_interval_when_idle_seconds=int(
            raw.get("refresh_interval_when_idle_seconds", 600),
        ),
        cadence=CadenceConfig(
            prematch_seconds=int(cad["prematch_seconds"]),
            live_seconds=int(cad["live_seconds"]),
            status_retry_backoff_seconds=tuple(
                int(x) for x in cad["status_retry_backoff_seconds"]
            ),
            watchdog_after_kickoff_seconds=int(cad["watchdog_after_kickoff_seconds"]),
        ),
        output=OutputConfig(
            db_path=str(out.get("db_path", "data/odds.db")),
            resolution_cache_path=str(out["resolution_cache_path"]),
        ),
        log_level=os.environ.get(
            "ODDS_SCRAPER_LOG_LEVEL", str(raw.get("log_level", "INFO")),
        ),
    )
