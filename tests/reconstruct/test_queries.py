from odds_scraper.reconstruct import queries
from odds_scraper.reconstruct import constants as c


def test_extraction_sql_mentions_source_and_markets():
    sql = queries.extraction_sql("bi_Samuel.tbl_x")
    assert "bi_Samuel.tbl_x" in sql
    assert c.MARKET_1X2 in sql
    assert "handicap / 4.0" in sql or "handicap/4.0" in sql
    assert "true_proba" in sql
    # single ordered scan for the Python carry-forward reducer (no SQL join)
    assert "ORDER BY event_id, in_play, odds_timestamp" in sql
    assert "JOIN" not in sql.upper()
    # next-goal markets matched by the "{n} Goal" pattern, not a fixed handicap
    assert "Goal" in sql


def test_extraction_sql_rejects_unsafe_table_name():
    import pytest
    with pytest.raises(ValueError):
        queries.extraction_sql("bad; DROP TABLE x")


def test_output_ddl_targets_table_and_lists_columns():
    ddl = queries.output_ddl("risk_Lorenzo.out")
    assert "CREATE TABLE IF NOT EXISTS risk_Lorenzo.out" in ddl
    for col in ("run_ts", "event_id", "v4_2up_away_ev"):
        assert col in ddl
