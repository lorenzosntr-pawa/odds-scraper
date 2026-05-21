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
    assert cfg.events == ("11111", "22222")
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


def test_load_with_tournaments(tmp_path: Path):
    p = _write(tmp_path / "c.yaml", """
        country: ng
        events: [11111]
        tournaments: [11965, 11963]
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
    assert cfg.tournaments == ("11965", "11963")


def test_load_without_tournaments_defaults_to_empty(tmp_path: Path):
    p = _write(tmp_path / "c.yaml", """
        country: ng
        events: [11111]
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
    assert cfg.tournaments == ()


def test_refresh_interval_seconds_default_is_86400(tmp_path: Path):
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
    cfg = load_config(p)
    assert cfg.refresh_interval_seconds == 86400


def test_refresh_interval_when_idle_seconds_default_is_600(tmp_path: Path):
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
    cfg = load_config(p)
    assert cfg.refresh_interval_when_idle_seconds == 600


def test_load_with_all_new_fields_explicit(tmp_path: Path):
    p = _write(tmp_path / "c.yaml", """
        country: ng
        events: [11111]
        tournaments: [42]
        refresh_interval_seconds: 3600
        refresh_interval_when_idle_seconds: 120
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
    cfg = load_config(p)
    assert cfg.tournaments == ("42",)
    assert cfg.refresh_interval_seconds == 3600
    assert cfg.refresh_interval_when_idle_seconds == 120
