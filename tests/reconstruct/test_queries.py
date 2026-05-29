import pytest

from odds_scraper.reconstruct import queries
from odds_scraper.reconstruct import constants as c


def test_extraction_sql_mentions_source_and_markets():
    sql = queries.extraction_sql("bi_Samuel.tbl_x")
    assert "bi_Samuel.tbl_x" in sql
    assert c.MARKET_1X2 in sql
    assert "handicap / 4.0" in sql or "handicap/4.0" in sql
    assert "true_proba" in sql
    # single ordered scan, brand-first so brands don't interleave (no SQL join)
    assert "ORDER BY brand, event_id, in_play, odds_timestamp" in sql
    assert "JOIN" not in sql.upper()
    # next-goal market is the literal "{handicap} Goal" template string
    assert "{handicap} Goal" in sql


def test_extraction_sql_brand_and_limit():
    sql = queries.extraction_sql("bi_Samuel.tbl_x", brand="betpawa-ghana", limit=5000)
    assert "brand = 'betpawa-ghana'" in sql
    assert "LIMIT 5000" in sql


def test_extraction_sql_shard():
    sql = queries.extraction_sql("bi_Samuel.tbl_x", shard_index=3, shard_count=30)
    assert "cityHash64(event_id) % 30 = 3" in sql
    with pytest.raises(ValueError):
        queries.extraction_sql("bi_Samuel.tbl_x", shard_index=30, shard_count=30)


def test_extraction_sql_next_goal_toggle():
    with_ng = queries.extraction_sql("bi_Samuel.tbl_x", include_next_goal=True)
    without_ng = queries.extraction_sql("bi_Samuel.tbl_x", include_next_goal=False)
    assert "{handicap} Goal" in with_ng
    assert "{handicap} Goal" not in without_ng
    # 1X2 and O/U always present
    assert c.MARKET_1X2 in without_ng and c.MARKET_OU_TOTAL in without_ng


def test_extraction_sql_sample_mod():
    sql = queries.extraction_sql("bi_Samuel.tbl_x", sample_mod=200)
    assert "cityHash64(event_id) % 200 = 0" in sql
    with pytest.raises(ValueError):
        queries.extraction_sql("bi_Samuel.tbl_x", sample_mod=0)


def test_extraction_sql_in_play_filter():
    assert "in_play = 0" in queries.extraction_sql("bi_Samuel.tbl_x", in_play=0)
    assert "in_play = 1" in queries.extraction_sql("bi_Samuel.tbl_x", in_play=1)
    # both/None => no in_play filter in the WHERE clause
    assert "in_play = " not in queries.extraction_sql("bi_Samuel.tbl_x", in_play=None)


def test_live_score_probe_sql():
    sql = queries.live_score_probe_sql("bi_Samuel.tbl_x", brand="betpawa-ghana")
    assert "in_play" in sql and "home_score != 0" in sql and "away_score != 0" in sql
    assert "brand = 'betpawa-ghana'" in sql and "LIMIT 1" in sql


def test_extraction_sql_rejects_unsafe_table_name():
    with pytest.raises(ValueError):
        queries.extraction_sql("bad; DROP TABLE x")


def test_extraction_sql_rejects_unsafe_brand():
    with pytest.raises(ValueError):
        queries.extraction_sql("bi_Samuel.tbl_x", brand="x'; DROP")


def test_output_ddl_targets_table_and_lists_columns():
    ddl = queries.output_ddl("risk_Lorenzo.out")
    assert "CREATE TABLE IF NOT EXISTS risk_Lorenzo.out" in ddl
    for col in ("run_ts", "event_id", "v4_2up_away_ev"):
        assert col in ddl
