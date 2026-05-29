"""Thin ClickHouse connection adapter (clickhouse-connect over the local
Teleport proxy). Config from env; no business logic."""
from __future__ import annotations

import os
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
    inject a fake client need no driver/network."""
    import clickhouse_connect
    return clickhouse_connect.get_client(**(config or config_from_env()))


def stream_rows(client, sql: str):
    """Yield query result rows as dicts, streaming in blocks."""
    with client.query_rows_stream(sql) as stream:
        columns = stream.source.column_names
        for row in stream:
            yield dict(zip(columns, row))


def insert_rows(client, table: str, rows: Iterable[dict], *, columns: list,
                batch_size: int = 10_000) -> int:
    """Insert dict rows in column order, in batches. Returns total inserted."""
    batch, total = [], 0
    for r in rows:
        batch.append([r.get(c) for c in columns])
        if len(batch) >= batch_size:
            client.insert(table, batch, column_names=columns)
            total += len(batch)
            batch = []
    if batch:
        client.insert(table, batch, column_names=columns)
        total += len(batch)
    return total
