import pytest
from odds_scraper.reconstruct import clickhouse_io as chio


def test_config_from_env_reads_expected_vars(monkeypatch):
    monkeypatch.setenv("CH_HOST", "127.0.0.1")
    monkeypatch.setenv("CH_PORT", "12345")
    monkeypatch.setenv("CH_USER", "lorenzo")
    monkeypatch.setenv("CH_PASSWORD", "secret")
    monkeypatch.setenv("CH_DATABASE", "risk_Lorenzo")
    cfg = chio.config_from_env()
    assert cfg == {"host": "127.0.0.1", "port": 12345, "username": "lorenzo",
                   "password": "secret", "database": "risk_Lorenzo"}


def test_config_from_env_requires_host(monkeypatch):
    monkeypatch.delenv("CH_HOST", raising=False)
    with pytest.raises(RuntimeError, match="CH_HOST"):
        chio.config_from_env()


def test_insert_rows_batches_and_orders_columns():
    captured = []

    class FakeClient:
        def insert(self, table, data, column_names):
            captured.append((table, list(data), list(column_names)))

    rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}, {"a": 5, "b": 6}]
    chio.insert_rows(FakeClient(), "t", rows, columns=["b", "a"], batch_size=2)
    # two batches: sizes 2 and 1, values ordered as ["b","a"]
    assert [len(d) for _, d, _ in captured] == [2, 1]
    assert captured[0][1][0] == [2, 1]   # first row -> [b, a]
    assert captured[0][2] == ["b", "a"]


def test_insert_rows_retries_then_succeeds():
    calls = {"n": 0}

    class FlakyClient:
        def insert(self, table, data, column_names):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("transient")

    rows = [{"event_id": "E1", "a": 1}]
    n = chio.insert_rows(FlakyClient(), "t", rows, columns=["event_id", "a"],
                         retries=2, sleep=lambda _s: None)
    assert n == 1 and calls["n"] == 2   # failed once, retried, succeeded


def test_insert_rows_raises_with_event_id_after_exhausting_retries():
    class DeadClient:
        def insert(self, table, data, column_names):
            raise ConnectionError("down")

    rows = [{"event_id": "E9", "a": 1}]
    with pytest.raises(RuntimeError, match="E9"):
        chio.insert_rows(DeadClient(), "t", rows, columns=["event_id", "a"],
                         retries=1, sleep=lambda _s: None)
