import textwrap
from pathlib import Path

from odds_scraper.config import AppConfig, load_config


def _write(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


def test_load_minimal_config(tmp_path: Path):
    p = _write(tmp_path / "c.yaml", """
        country: ng
        events: [11111, 22222]
        cadence:
          prematch_seconds: 600
          live_seconds: 90
          status_retry_backoff_seconds: [5, 15, 45]
          watchdog_after_kickoff_seconds: 10800
        output:
          csv_path: data/x.csv
          resolution_cache_path: data/r.json
        log_level: INFO
    """)
    cfg = load_config(p)
    assert isinstance(cfg, AppConfig)
    assert cfg.country == "ng"
    assert cfg.events == ["11111", "22222"]
    assert cfg.cadence.live_seconds == 90
    assert cfg.cadence.prematch_seconds == 600
    assert cfg.cadence.status_retry_backoff_seconds == (5, 15, 45)
    assert cfg.output.csv_path.endswith("x.csv")


def test_env_var_overrides(tmp_path: Path, monkeypatch):
    p = _write(tmp_path / "c.yaml", """
        country: ng
        events: [1]
        cadence:
          prematch_seconds: 600
          live_seconds: 90
          status_retry_backoff_seconds: [5, 15, 45]
          watchdog_after_kickoff_seconds: 10800
        output:
          csv_path: a.csv
          resolution_cache_path: b.json
        log_level: INFO
    """)
    monkeypatch.setenv("ODDS_SCRAPER_LOG_LEVEL", "DEBUG")
    cfg = load_config(p)
    assert cfg.log_level == "DEBUG"
