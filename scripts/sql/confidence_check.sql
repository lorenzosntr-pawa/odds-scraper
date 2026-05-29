-- Confidence investigation for the reconstructed 1UP/2UP table.
-- Run in DBeaver (ClickHouse). Adjust the table / add a run_ts filter as needed.
-- confidence is 0..1: freshness band x 1X2-consistency band (1.0 = fresh & consistent).

-- 1) How many rows have confidence over 0.8?
SELECT
    count()                                                AS total_rows,
    countIf(confidence > 0.8)                              AS conf_over_0_8,
    round(100.0 * countIf(confidence > 0.8) / count(), 1)  AS pct_over_0_8
FROM risk_Lorenzo.oneup_twoup_reconstructed;

-- 2) Confidence distribution, split prematch vs live.
SELECT
    multiIf(confidence > 0.8, '4: >0.8 (high)',
            confidence >= 0.5, '3: 0.5-0.8',
            confidence > 0,    '2: 0-0.5',
                               '1: 0 (drop)') AS confidence_band,
    count()              AS rows,
    countIf(in_play = 0) AS prematch,
    countIf(in_play = 1) AS live
FROM risk_Lorenzo.oneup_twoup_reconstructed
GROUP BY confidence_band
ORDER BY confidence_band DESC;

-- 3) Rows usable for the sim: high confidence AND V4 priced.
SELECT
    countIf(confidence > 0.8 AND v4_2up_home_odds IS NOT NULL) AS usable_2up,
    countIf(confidence > 0.8 AND v4_1up_home_odds IS NOT NULL) AS usable_1up
FROM risk_Lorenzo.oneup_twoup_reconstructed;

-- 4) (optional) scope to one run if the table holds several:
--    add  WHERE run_ts = '2026-05-29 18:00:00'  to any query above.
