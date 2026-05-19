from unittest.mock import AsyncMock

from odds_scraper.main import supervise_watcher


async def test_supervisor_restarts_crashed_watcher(monkeypatch):
    calls = {"n": 0}

    async def fake_run():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")

    watcher = AsyncMock()
    watcher.run.side_effect = fake_run

    monkeypatch.setattr("odds_scraper.main.asyncio.sleep", AsyncMock())

    await supervise_watcher(watcher, event_id="x", max_backoff_seconds=1)
    assert calls["n"] == 3
