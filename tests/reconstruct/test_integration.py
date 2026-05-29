import os
import pytest

from odds_scraper.reconstruct import clickhouse_io as chio
from odds_scraper.reconstruct import constants as c
from odds_scraper.reconstruct import pricing, queries

pytestmark = pytest.mark.skipif(
    not os.environ.get("CH_HOST"), reason="no ClickHouse proxy (CH_HOST unset)")

SOURCE = os.environ.get(
    "CH_SOURCE",
    "bi_Samuel.tbl_oneup_backtest_odds_data_betslip_includingGoalInfo")


def _client():
    return chio.connect()


def test_source_vocabulary_matches_constants():
    client = _client()
    names = {r[0] for r in client.query(
        f"SELECT DISTINCT market_name FROM {SOURCE} LIMIT 200").result_rows}
    assert c.MARKET_1X2 in names
    assert c.MARKET_OU_TOTAL in names
    # at least one "{n} Goal" next-goal market exists
    assert any(n.endswith(" Goal") and n[:-5].isdigit() for n in names)
    sels = {r[0] for r in client.query(
        f"SELECT DISTINCT selection_name FROM {SOURCE} "
        f"WHERE market_name LIKE '% Goal' LIMIT 50").result_rows}
    assert {c.SEL_NG_HOME, c.SEL_NG_AWAY, c.SEL_NG_NONE} <= sels


def test_end_to_end_small_slice_prices_and_inserts():
    client = _client()
    client.command(queries.output_ddl("risk_Lorenzo.recon_smoke_test"))
    # one event's worth of rows
    sql = queries.extraction_sql(SOURCE).rstrip()
    sql += "\nLIMIT 5000"
    rows = list(chio.stream_rows(client, sql))
    restore = pricing.install_dp_cache()
    try:
        moments = list(pricing.moments_from_rows(rows))
        priced = list(pricing.run_pricing(moments, run_ts="2026-05-29 00:00:00"))
    finally:
        restore()
    assert priced, "expected at least one priced moment from a 5000-row slice"
    n = chio.insert_rows(client, "risk_Lorenzo.recon_smoke_test", priced,
                         columns=c.OUTPUT_COLUMNS, batch_size=1000)
    assert n == len(priced)
    client.command("DROP TABLE IF EXISTS risk_Lorenzo.recon_smoke_test")
