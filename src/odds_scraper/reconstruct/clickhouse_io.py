"""Thin ClickHouse connection adapter (clickhouse-connect over the local
Teleport proxy). Config from env; no business logic."""
from __future__ import annotations

import os
import time
from typing import Iterable


def config_from_env() -> dict:
    """Read connection config from CH_* env vars. host is required."""
    host = os.environ.get("CH_HOST")
    if not host:
        raise RuntimeError("CH_HOST not set (point it at the local Teleport proxy)")
    return {
        "host": host,
        "port": int(os.environ.get("CH_PORT", "8123")),
        "username": os.environ.get("CH_USER", "default"),
        "password": os.environ.get("CH_PASSWORD", ""),
        "database": os.environ.get("CH_DATABASE", "default"),
    }


def connect(config: dict | None = None):
    """Open a clickhouse-connect client. Imported lazily so unit tests that
    inject a fake client need no driver/network.

    compress=False: clickhouse-connect defaults to lz4-compressed payloads,
    which the Teleport HTTP DB proxy rejects ("unsupported compression method
    lz4"). Over a localhost tunnel compression buys nothing, so disable it."""
    import clickhouse_connect
    return clickhouse_connect.get_client(compress=False, **(config or config_from_env()))


def stream_rows(client, sql: str):
    """Yield query result rows as dicts, streaming in blocks."""
    with client.query_rows_stream(sql) as stream:
        columns = stream.source.column_names
        for row in stream:
            yield dict(zip(columns, row))


def _insert_batch(client, table, batch, columns, retries, sleep):
    """Insert one batch with bounded retry + backoff. On final failure raise
    with the batch's event_id range so the operator knows where the run died
    and can resume from there."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            client.insert(table, batch, column_names=columns)
            return
        except Exception as exc:                       # noqa: BLE001 - re-raised below
            last_exc = exc
            if attempt < retries:
                sleep(min(2 ** attempt, 8))
    where = ""
    if "event_id" in columns and batch:
        idx = columns.index("event_id")
        where = f" (event_id {batch[0][idx]}..{batch[-1][idx]})"
    raise RuntimeError(
        f"insert into {table} failed after {retries + 1} attempts{where}: {last_exc}"
    ) from last_exc


def insert_rows(client, table: str, rows: Iterable[dict], *, columns: list,
                batch_size: int = 10_000, retries: int = 3, sleep=time.sleep) -> int:
    """Insert dict rows in column order, in batches with bounded retry.
    Returns total inserted. Raises RuntimeError (with the failing batch's
    event_id range) if a batch still fails after `retries` retries."""
    batch, total = [], 0
    for r in rows:
        batch.append([r.get(c) for c in columns])
        if len(batch) >= batch_size:
            _insert_batch(client, table, batch, columns, retries, sleep)
            total += len(batch)
            batch = []
    if batch:
        _insert_batch(client, table, batch, columns, retries, sleep)
        total += len(batch)
    return total
